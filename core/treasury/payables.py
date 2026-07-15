"""
payables.py — Dettes fournisseurs (échéancier des paiements à sortir).

Source : Pennylane API v2 `supplier_invoices` (+ `suppliers` pour les noms).
On réutilise la session authentifiée de core.reconciliation.io_api (même token
Bearer, même base v2).

⚠️ Périmètre du token actuel : seules les factures FOURNISSEURS sont accessibles
(customer_invoices et bank_accounts → 403). Ce module ne couvre donc que le
CASH SORTANT. La trésorerie complète (créances clients, solde bancaire,
prévision entrées−sorties) nécessitera un token Pennylane élargi.

Modélisation (vérifiée sur données réelles 2026-06) :
  - une facture est DUE si remaining_amount_with_tax != 0 (le signe est négatif
    côté Pennylane pour un montant à payer → on prend la valeur absolue) ;
  - `payment_status` (to_be_processed / fully_paid / to_be_paid) reflète un statut
    de workflow comptable, PAS le règlement → on ne s'y fie pas pour le « dû » ;
  - le nom du fournisseur n'est pas dans la liste des factures (objet réduit à
    {id, url}) → jointure via /suppliers.
"""
from __future__ import annotations

import datetime
import json

from core.reconciliation.io_api import PENNYLANE_BASE_URL, _session


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _get_all(s, chemin, params=None) -> list:
    """GET paginé (curseur v2). Renvoie la liste agrégée."""
    params = dict(params or {})
    params.setdefault("limit", 100)
    items, cursor = [], None
    while True:
        if cursor:
            params["cursor"] = cursor
        r = s.get(f"{PENNYLANE_BASE_URL}/{chemin}", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items") or [])
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    return items


def lire_dettes_fournisseurs(date_min: str | None = None) -> list[dict]:
    """Factures fournisseurs NON soldées → échéancier trié par date d'échéance.

    date_min : borne basse sur la date de facture ("YYYY-MM-DD") pour limiter le
        volume (les factures anciennes sont soldées). Défaut : 18 mois en arrière.
    """
    s = _session()
    if date_min is None:
        cutoff = datetime.date.today() - datetime.timedelta(days=548)
        date_min = cutoff.isoformat()

    smap = {sup["id"]: sup.get("name") for sup in _get_all(s, "suppliers")}
    flt = json.dumps([{"field": "date", "operator": "gteq", "value": date_min}])
    invoices = _get_all(s, "supplier_invoices", {"filter": flt})

    dettes = []
    for inv in invoices:
        reste = abs(_f(inv.get("remaining_amount_with_tax")))
        if reste < 0.01:
            continue  # facture soldée
        sid = (inv.get("supplier") or {}).get("id")
        dettes.append({
            "id": inv.get("id"),
            "supplier": smap.get(sid) or "—",
            "date": inv.get("date"),
            "deadline": inv.get("deadline"),
            "amount": _f(inv.get("amount")),
            "remaining": reste,
            "invoice_number": inv.get("invoice_number"),
            "label": inv.get("label"),
        })
    dettes.sort(key=lambda d: d.get("deadline") or "9999")
    return dettes


def synthese_dettes(dettes: list[dict], today: str) -> dict:
    """KPIs : total dû, en retard, à payer sous 30j, nb de fournisseurs."""
    d_today = datetime.date.fromisoformat(today)
    d_30 = (d_today + datetime.timedelta(days=30)).isoformat()
    retard = [d for d in dettes if (d["deadline"] or "9999") < today]
    sous30 = [d for d in dettes if today <= (d["deadline"] or "9999") <= d_30]
    return {
        "total": round(sum(d["remaining"] for d in dettes), 2),
        "nb": len(dettes),
        "retard_total": round(sum(d["remaining"] for d in retard), 2),
        "retard_nb": len(retard),
        "sous30_total": round(sum(d["remaining"] for d in sous30), 2),
        "sous30_nb": len(sous30),
        "nb_fournisseurs": len({d["supplier"] for d in dettes}),
    }


def prioriser(dettes: list[dict], today: str, part_pareto: float = 0.8,
              horizon_jours: int = 30) -> tuple[list[dict], list[dict], list[dict]]:
    """Découpe 80/20 des encours en 3 blocs de priorité décroissante.

    « Grosse dette » = la minorité de factures qui cumulent ``part_pareto`` (80 %)
    du montant total dû (principe de Pareto). « Urgente » = échéance déjà passée
    ou dans les ``horizon_jours`` prochains jours.

    Retourne (grosses_urgentes, grosses_non_urgentes, reste) :
      - grosses_urgentes : à régler EN PRIORITÉ (gros montant + urgent), triées
        par échéance (les plus pressantes d'abord) ;
      - grosses_non_urgentes : gros montants à anticiper, triées par montant ;
      - reste : le reliquat, trié par échéance.
    """
    total = sum(d["remaining"] for d in dettes) or 1.0
    par_montant = sorted(dettes, key=lambda d: -d["remaining"])
    gros_ids, cumul = set(), 0.0
    for d in par_montant:
        if cumul >= part_pareto * total:
            break
        gros_ids.add(d["id"])
        cumul += d["remaining"]

    horizon = (
        datetime.date.fromisoformat(today) + datetime.timedelta(days=horizon_jours)
    ).isoformat()

    def _urgent(d) -> bool:
        return (d["deadline"] or "9999") <= horizon

    # Bloc prioritaire : les plus GROS montants d'abord (le boss scanne du haut).
    grosses_urgentes = sorted(
        (d for d in dettes if d["id"] in gros_ids and _urgent(d)),
        key=lambda d: -d["remaining"],
    )
    grosses_non_urg = sorted(
        (d for d in dettes if d["id"] in gros_ids and not _urgent(d)),
        key=lambda d: -d["remaining"],
    )
    reste = [d for d in dettes if d["id"] not in gros_ids]  # déjà trié par échéance
    return grosses_urgentes, grosses_non_urg, reste


def par_fournisseur(dettes: list[dict], today: str) -> list[dict]:
    """Agrégat par fournisseur (total dû, dont en retard), trié par total décroissant."""
    groups: dict[str, dict] = {}
    for d in dettes:
        g = groups.setdefault(
            d["supplier"],
            {"fournisseur": d["supplier"], "nb": 0, "total": 0.0, "retard": 0.0},
        )
        g["nb"] += 1
        g["total"] += d["remaining"]
        if (d["deadline"] or "9999") < today:
            g["retard"] += d["remaining"]
    return sorted(groups.values(), key=lambda r: -r["total"])
