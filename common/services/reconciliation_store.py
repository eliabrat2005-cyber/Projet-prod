"""
common/services/reconciliation_store.py
=======================================
Persistance de la réconciliation transport, UN SNAPSHOT PAR MOIS et par tenant
(table reconciliation_snapshots). Objectif : la page LIT ces snapshots
(instantané) et l'utilisateur change de mois sans aucun appel API ; la synchro
(qui interroge Pennylane + EasyBeer) reste une action manuelle.

Couche domaine : accès DB via db.conn, pas de NiceGUI ici.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.reconciliation.reconciliation_core import (
    Resultat,
    agreger,
    from_dict,
    to_dict,
)
from db.conn import run_sql

_log = logging.getLogger("ferment.reconciliation_store")


def save_snapshot(
    tenant_id: str,
    period_start: str,
    period_end: str | None,
    res: Resultat,
    stocks: list[dict],
    *,
    user_id: str | None = None,
) -> None:
    """Écrit (ou remplace) le snapshot d'UN mois pour le tenant."""
    payload = {"result": to_dict(res), "stockage": stocks}
    nb = len(res.lignes)
    nb_sp = len(res.sans_piece)
    taux = nb / (nb + nb_sp) if (nb + nb_sp) else None
    run_sql(
        """
        INSERT INTO reconciliation_snapshots
            (tenant_id, period_start, period_end, payload,
             nb_lignes, nb_sans_piece, taux, synced_at, synced_by)
        VALUES
            (:t, :ps, :pe, CAST(:p AS JSONB), :nl, :nsp, :tx, now(), :u)
        ON CONFLICT (tenant_id, period_start) DO UPDATE SET
            period_end   = EXCLUDED.period_end,
            payload      = EXCLUDED.payload,
            nb_lignes    = EXCLUDED.nb_lignes,
            nb_sans_piece= EXCLUDED.nb_sans_piece,
            taux         = EXCLUDED.taux,
            synced_at    = now(),
            synced_by    = EXCLUDED.synced_by
        """,
        {
            "t": tenant_id, "ps": period_start, "pe": period_end,
            "p": json.dumps(payload), "nl": nb, "nsp": nb_sp,
            "tx": taux, "u": user_id,
        },
    )


def list_periods(tenant_id: str) -> list[dict[str, Any]]:
    """Mois disponibles en base pour le tenant, du plus récent au plus ancien."""
    return run_sql(
        """
        SELECT period_start, period_end, nb_lignes, nb_sans_piece, taux, synced_at
        FROM reconciliation_snapshots
        WHERE tenant_id = :t
        ORDER BY period_start DESC
        """,
        {"t": tenant_id},
    )


def load_snapshot(tenant_id: str, period_start: str) -> dict[str, Any] | None:
    """Relit le snapshot d'un mois. Retourne {result, stockage, period_*, taux,
    synced_at} ou None."""
    rows = run_sql(
        """
        SELECT period_start, period_end, payload, taux, synced_at
        FROM reconciliation_snapshots
        WHERE tenant_id = :t AND period_start = :ps
        """,
        {"t": tenant_id, "ps": period_start},
    )
    if not rows:
        return None
    row = rows[0]
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "result": from_dict(payload.get("result", {})),
        "stockage": payload.get("stockage", []),
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "taux": row["taux"],
        "synced_at": row["synced_at"],
    }


def load_range(tenant_id: str, ps_from: str, ps_to: str) -> dict[str, Any] | None:
    """Charge tous les mois de [ps_from, ps_to] et les FUSIONNE en un seul
    résultat (KPIs + enseignes recalculés sur l'ensemble). Aucun appel API —
    lecture base pure. Retourne None si aucun mois dans la plage."""
    if ps_from > ps_to:
        ps_from, ps_to = ps_to, ps_from
    rows = run_sql(
        """
        SELECT period_start, period_end, payload, synced_at
        FROM reconciliation_snapshots
        WHERE tenant_id = :t AND period_start BETWEEN :a AND :b
        ORDER BY period_start
        """,
        {"t": tenant_id, "a": ps_from, "b": ps_to},
    )
    if not rows:
        return None
    resultats, stockage = [], []
    synced = None
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        resultats.append(from_dict(payload.get("result", {})))
        stockage.extend(payload.get("stockage", []))
        if synced is None or (row["synced_at"] and row["synced_at"] > synced):
            synced = row["synced_at"]
    return {
        "result": agreger(resultats),
        "stockage": stockage,
        "period_start": rows[0]["period_start"],
        "period_end": rows[-1]["period_end"],
        "synced_at": synced,
        "nb_mois": len(rows),
    }
