"""common/services/allocation_sync.py - job de synchronisation de la repartition.

Orchestration (aucune UI). Combine deux sources DEJA persistees / disponibles :
  1. Reconciliation (snapshots) -> cout transport par commande (Pennylane).
  2. EasyBeer detail commande    -> lignes produit (barcode, poids) pour classer.

Pour chaque commande facturee non figee : classe les lignes (FS/Spraga),
repartit le cout au poids, upsert en base. Ne recalcule JAMAIS une commande
VALIDEE (elle est meme exclue des appels API - inutile de la refetch).

Idempotent : rejouer la meme periode reproduit le meme etat (hors nouvelles
factures / nouveaux details EasyBeer).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from common.services import allocation_store
from common.services.reconciliation_store import list_periods, load_snapshot
from core.allocation.classification import classify
from core.allocation.compute import build_allocation
from core.allocation.models import STATUS_ANOMALIE, Allocation, AllocationLine

_log = logging.getLogger("ferment.allocation_sync")


def _period_month(d) -> str:
    """date/‘YYYY-MM-DD' -> 'YYYY-MM'."""
    if isinstance(d, date):
        return d.strftime("%Y-%m")
    return str(d)[:7]


def _build_lines(raw_lines: list[dict], *, override_path: str | None = None) -> list[AllocationLine]:
    out: list[AllocationLine] = []
    for rl in raw_lines:
        entity, weight, esrc, wsrc = classify(
            barcode=rl.get("barcode"),
            product_name=rl.get("product_name"),
            category=rl.get("category"),
            unit_weight_kg=rl.get("unit_weight_kg"),
            override_path=override_path,
        )
        qty = float(rl.get("quantity") or 0)
        line_w = (qty * weight) if (weight and qty) else None
        out.append(AllocationLine(
            barcode=rl.get("barcode"),
            product_name=rl.get("product_name"),
            entity=entity,
            quantity=qty,
            unit_weight_kg=weight,
            line_weight_kg=(round(line_w, 3) if line_w is not None else None),
            weight_source=wsrc,
            entity_source=esrc,
        ))
    return out


def _collect_orders(tenant_id: str, period_from: str, period_to: str) -> dict[int, dict]:
    """Agrege, par N° commande, le cout transport issu des snapshots de reconciliation.

    Cle = numero commande EasyBeer ; valeur = {cost, client, piece, period_month}.
    Un cout est SOMME si plusieurs lignes de transport pointent la meme commande.
    """
    orders: dict[int, dict] = {}
    try:
        periods = list_periods(tenant_id)
    except Exception:  # noqa: BLE001
        _log.exception("Lecture des periodes de reconciliation echouee")
        return orders
    for p in periods:
        ps = p["period_start"]
        pm = _period_month(ps)
        if not (period_from <= pm <= period_to):
            continue
        snap = load_snapshot(tenant_id, ps.isoformat() if isinstance(ps, date) else str(ps))
        if not snap or snap.get("result") is None:
            continue
        for line in snap["result"].lignes:
            num = getattr(line, "numero", None)
            cost = getattr(line, "cout_transport", None)
            if num is None:
                continue
            entry = orders.setdefault(num, {
                "cost": 0.0, "client": getattr(line, "client", None),
                "piece": getattr(line, "piece", None), "period_month": pm,
            })
            if cost is not None:
                entry["cost"] += cost
    return orders


def synchroniser(
    tenant_id: str,
    period_from: str,
    period_to: str,
    *,
    fetch_detail: Callable[[int], dict | None] | None = None,
    override_path: str | None = None,
    full: bool = False,
    progress_cb: Callable[[int, int, str | None], None] | None = None,
) -> dict[str, int]:
    """Synchronise la repartition sur la plage de mois ['YYYY-MM', 'YYYY-MM'].

    INCREMENTAL par defaut : ne (re)traite que les commandes NOUVELLES ou en
    ANOMALIE ; celles deja reparties (PROVISOIRE/VALIDEE) sont sautees (leur
    detail EasyBeer est stable) -> synchros suivantes quasi instantanees.
    full=True force le recalcul de tout.

    ``fetch_detail`` : injectable pour les tests ; par defaut appelle EasyBeer.
    Retourne un compteur {vues, ecrites, anomalies, ignorees}.
    """
    if period_from > period_to:
        period_from, period_to = period_to, period_from
    # Import tardif de la couche transport (EasyBeer). ``fetch_detail`` reste
    # injectable pour les tests ; a defaut on appelle l'API reelle.
    from common.easybeer.orders import extract_order_lines, order_status
    fetch = fetch_detail
    if fetch is None:
        from common.easybeer.orders import fetch_commande_detail
        fetch = fetch_commande_detail

    orders = _collect_orders(tenant_id, period_from, period_to)
    # Déjà réparties : on saute (sauf full) celles PROVISOIRE/VALIDEE ; on retente
    # les ANOMALIE et on traite les nouvelles.
    existantes = {
        r["order_number"]: r["allocation_status"]
        for r in allocation_store.list_allocations(tenant_id)
    }

    # À TRAITER = nouvelles + anomalies (fetch EB, 1/s). On PLAFONNE (max_orders)
    # pour borner la durée : le reste est traité au prochain passage (l'auto en
    # fond rattrape par lots). Les nouvelles d'abord, puis les anomalies.
    a_traiter = [
        (num, info) for num, info in sorted(orders.items())
        if full or existantes.get(num) not in ("PROVISOIRE", "VALIDEE")
    ]
    a_traiter.sort(key=lambda x: existantes.get(x[0]) == "ANOMALIE")  # nouvelles avant
    ignorees = len(orders) - len(a_traiter)
    restantes = 0
    if max_orders and len(a_traiter) > max_orders:
        restantes = len(a_traiter) - max_orders
        a_traiter = a_traiter[:max_orders]

    stats = {"vues": len(orders), "ecrites": 0, "anomalies": 0,
             "ignorees": ignorees, "restantes": restantes}
    total = len(a_traiter)
    for i, (num, info) in enumerate(a_traiter):
        if progress_cb:
            progress_cb(i, total, f"commande {num}")

        cost = round(info["cost"], 2) if info.get("cost") else None
        detail = fetch(num)
        if not detail:
            # Detail indisponible (API 500 / commande absente) -> anomalie.
            alloc = Allocation(
                order_number=num, order_client=info.get("client"),
                invoice_number=info.get("piece"), period_month=info.get("period_month"),
                transport_cost_total=cost, allocation_status=STATUS_ANOMALIE,
                anomaly_reason="detail EasyBeer indisponible (a resynchroniser)",
            )
        else:
            lines = _build_lines(extract_order_lines(detail), override_path=override_path)
            alloc = build_allocation(
                order_number=num,
                order_client=(detail.get("client") or {}).get("nom") or info.get("client"),
                order_status=order_status(detail),
                invoice_number=info.get("piece"),
                period_month=info.get("period_month"),
                transport_cost_total=cost,
                lines=lines,
            )

        try:
            allocation_store.upsert_provisional(tenant_id, alloc)
            stats["ecrites"] += 1
            if alloc.allocation_status == STATUS_ANOMALIE:
                stats["anomalies"] += 1
        except Exception:  # noqa: BLE001
            _log.exception("Upsert repartition commande %s echoue", num)

    if progress_cb:
        progress_cb(total, total, None)
    return stats
