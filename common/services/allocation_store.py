"""common/services/allocation_store.py - persistance des repartitions transport.

Couche domaine (DB via db.conn, pas de NiceGUI). Regle d'or : la sync ecrit des
lignes PROVISOIRE/ANOMALIE en upsert, mais ne TOUCHE JAMAIS une ligne VALIDEE
(garanti par la clause WHERE allocation_status <> 'VALIDEE' du ON CONFLICT).
Seule une action operateur (validate / apply_manual) fige une commande.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.allocation.models import (
    STATUS_PROVISOIRE,
    STATUS_VALIDEE,
    Allocation,
)
from db.conn import run_sql_with_tenant

_log = logging.getLogger("ferment.allocation_store")


# ── Ecriture (sync) ───────────────────────────────────────────────────────
def upsert_provisional(tenant_id: str, alloc: Allocation) -> None:
    """Insere/actualise une repartition NON figee. No-op si la ligne est VALIDEE."""
    run_sql_with_tenant(
        """
        INSERT INTO transport_allocation
            (tenant_id, order_number, order_client, order_status, invoice_number,
             period_month, weight_fs_kg, weight_spraga_kg, weight_total_kg,
             pct_fs, pct_spraga, transport_cost_total, cost_fs, cost_spraga,
             allocation_status, anomaly_reason, lines_json, last_synced_at)
        VALUES
            (:t, :num, :client, :ostatus, :inv, :pm, :wfs, :wsp, :wtot,
             :pfs, :psp, :ctot, :cfs, :csp, :astatus, :areason,
             CAST(:lines AS JSONB), now())
        ON CONFLICT (tenant_id, order_number) DO UPDATE SET
            order_client         = EXCLUDED.order_client,
            order_status         = EXCLUDED.order_status,
            invoice_number       = EXCLUDED.invoice_number,
            period_month         = EXCLUDED.period_month,
            weight_fs_kg         = EXCLUDED.weight_fs_kg,
            weight_spraga_kg     = EXCLUDED.weight_spraga_kg,
            weight_total_kg      = EXCLUDED.weight_total_kg,
            pct_fs               = EXCLUDED.pct_fs,
            pct_spraga           = EXCLUDED.pct_spraga,
            transport_cost_total = EXCLUDED.transport_cost_total,
            cost_fs              = EXCLUDED.cost_fs,
            cost_spraga          = EXCLUDED.cost_spraga,
            allocation_status    = EXCLUDED.allocation_status,
            anomaly_reason       = EXCLUDED.anomaly_reason,
            lines_json           = EXCLUDED.lines_json,
            last_synced_at       = now(),
            updated_at           = now()
        WHERE transport_allocation.allocation_status <> :validee
        """,
        {
            "t": tenant_id, "num": alloc.order_number, "client": alloc.order_client,
            "ostatus": alloc.order_status, "inv": alloc.invoice_number,
            "pm": alloc.period_month, "wfs": alloc.weight_fs_kg,
            "wsp": alloc.weight_spraga_kg, "wtot": alloc.weight_total_kg,
            "pfs": alloc.pct_fs, "psp": alloc.pct_spraga,
            "ctot": alloc.transport_cost_total, "cfs": alloc.cost_fs,
            "csp": alloc.cost_spraga, "astatus": alloc.allocation_status,
            "areason": alloc.anomaly_reason,
            "lines": json.dumps([ln.to_dict() for ln in alloc.lines]),
            "validee": STATUS_VALIDEE,
        },
        tenant_id=tenant_id,
    )


# ── Lecture (UI) ──────────────────────────────────────────────────────────
def list_allocations(
    tenant_id: str,
    period_from: str | None = None,
    period_to: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Repartitions du tenant, filtrees par plage de mois et/ou statut."""
    where = ["tenant_id = :t"]
    params: dict[str, Any] = {"t": tenant_id}
    if period_from:
        where.append("period_month >= :pf")
        params["pf"] = period_from
    if period_to:
        where.append("period_month <= :pt")
        params["pt"] = period_to
    if status:
        where.append("allocation_status = :st")
        params["st"] = status
    rows = run_sql_with_tenant(
        f"""
        SELECT order_number, order_client, order_status, invoice_number,
               period_month, weight_fs_kg, weight_spraga_kg, weight_total_kg,
               pct_fs, pct_spraga, transport_cost_total, cost_fs, cost_spraga,
               allocation_status, is_manual_override, anomaly_reason, lines_json,
               validated_at, validated_by, last_synced_at
        FROM transport_allocation
        WHERE {' AND '.join(where)}
        ORDER BY period_month DESC, order_number DESC
        """,
        params,
        tenant_id=tenant_id,
    )
    return rows if isinstance(rows, list) else []


def get_allocation(tenant_id: str, order_number: int) -> dict[str, Any] | None:
    rows = run_sql_with_tenant(
        "SELECT * FROM transport_allocation WHERE tenant_id=:t AND order_number=:n",
        {"t": tenant_id, "n": order_number},
        tenant_id=tenant_id,
    )
    return rows[0] if isinstance(rows, list) and rows else None


def list_period_months(tenant_id: str) -> list[str]:
    rows = run_sql_with_tenant(
        """
        SELECT DISTINCT period_month FROM transport_allocation
        WHERE tenant_id=:t AND period_month IS NOT NULL
        ORDER BY period_month DESC
        """,
        {"t": tenant_id},
        tenant_id=tenant_id,
    )
    return [r["period_month"] for r in rows] if isinstance(rows, list) else []


# ── Validation / edition operateur (fige la commande) ─────────────────────
def _audit(tenant_id, order_number, action, *, field=None, before=None,
           after=None, reason=None, by=None) -> None:
    run_sql_with_tenant(
        """
        INSERT INTO allocation_audit
            (tenant_id, order_number, action, field, value_before, value_after,
             reason, changed_by)
        VALUES (:t, :n, :a, :f, :b, :af, :r, :by)
        """,
        {"t": tenant_id, "n": order_number, "a": action, "f": field,
         "b": (str(before) if before is not None else None),
         "af": (str(after) if after is not None else None),
         "r": reason, "by": by},
        tenant_id=tenant_id,
    )


def validate(tenant_id: str, order_number: int, *, by: str,
             reason: str | None = None) -> int:
    """Fige une repartition PROVISOIRE telle quelle. Retourne le nb de lignes figees."""
    n = run_sql_with_tenant(
        """
        UPDATE transport_allocation
        SET allocation_status = :validee, is_manual_override = false,
            validated_at = now(), validated_by = :by, updated_at = now()
        WHERE tenant_id=:t AND order_number=:n AND allocation_status = :prov
        """,
        {"t": tenant_id, "n": order_number, "by": by,
         "validee": STATUS_VALIDEE, "prov": STATUS_PROVISOIRE},
        tenant_id=tenant_id,
    )
    if n:
        _audit(tenant_id, order_number, "VALIDATE", reason=reason, by=by)
    return n if isinstance(n, int) else 0


def mass_validate(tenant_id: str, order_numbers: list[int], *, by: str) -> int:
    """Valide en masse toutes les commandes PROVISOIRE de la liste."""
    figees = 0
    for num in order_numbers:
        figees += validate(tenant_id, num, by=by, reason="validation en masse")
    return figees


def apply_manual(
    tenant_id: str,
    order_number: int,
    *,
    cost_fs: float,
    cost_spraga: float,
    by: str,
    reason: str | None = None,
    weight_fs: float | None = None,
    weight_spraga: float | None = None,
) -> int:
    """Surcharge manuelle des montants (+ poids optionnels) PUIS fige la commande.

    Journalise l'avant/apres. Fonctionne aussi sur une ANOMALIE (l'operateur
    corrige puis fige). Retourne 1 si applique, 0 sinon.
    """
    before = get_allocation(tenant_id, order_number)
    if not before:
        return 0
    total = before.get("transport_cost_total")
    sets = ["cost_fs=:cfs", "cost_spraga=:csp",
            "allocation_status=:validee", "is_manual_override=true",
            "anomaly_reason=NULL", "validated_at=now()", "validated_by=:by",
            "updated_at=now()"]
    params: dict[str, Any] = {
        "t": tenant_id, "n": order_number, "cfs": round(float(cost_fs), 2),
        "csp": round(float(cost_spraga), 2), "validee": STATUS_VALIDEE, "by": by,
    }
    if weight_fs is not None:
        sets.append("weight_fs_kg=:wfs")
        params["wfs"] = weight_fs
    if weight_spraga is not None:
        sets.append("weight_spraga_kg=:wsp")
        params["wsp"] = weight_spraga
    if weight_fs is not None and weight_spraga is not None:
        wt = round(float(weight_fs) + float(weight_spraga), 3)
        sets.append("weight_total_kg=:wtot")
        params["wtot"] = wt
        if wt > 0:
            sets.append("pct_fs=:pfs")
            sets.append("pct_spraga=:psp")
            params["pfs"] = round(float(weight_fs) / wt, 6)
            params["psp"] = round(float(weight_spraga) / wt, 6)
    n = run_sql_with_tenant(
        f"UPDATE transport_allocation SET {', '.join(sets)} "
        "WHERE tenant_id=:t AND order_number=:n",
        params, tenant_id=tenant_id,
    )
    if n:
        _audit(tenant_id, order_number, "EDIT",
               field="cost_fs/cost_spraga",
               before=f"fs={before.get('cost_fs')} spraga={before.get('cost_spraga')}",
               after=f"fs={params['cfs']} spraga={params['csp']} (total={total})",
               reason=reason, by=by)
    return n if isinstance(n, int) else 0


def list_audit(tenant_id: str, order_number: int) -> list[dict[str, Any]]:
    rows = run_sql_with_tenant(
        """
        SELECT action, field, value_before, value_after, reason, changed_by, changed_at
        FROM allocation_audit
        WHERE tenant_id=:t AND order_number=:n
        ORDER BY changed_at DESC
        """,
        {"t": tenant_id, "n": order_number},
        tenant_id=tenant_id,
    )
    return rows if isinstance(rows, list) else []
