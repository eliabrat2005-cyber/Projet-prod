"""
common/services/reconciliation_sync.py
======================================
Orchestration de la SYNCHRONISATION (la seule opération qui interroge les API).
Boucle sur chaque MOIS d'une période, réconcilie, et écrit un snapshot par mois
en base (via reconciliation_store). Ensuite la page lit ces snapshots
instantanément — changer de mois n'appelle plus jamais les API.

Couche domaine : pas de NiceGUI. La progression remonte via un callback.
"""
from __future__ import annotations

import datetime
import logging

from common.services.reconciliation_store import save_snapshot
from core.reconciliation.io_api import charger_sources, lire_factures_pennylane
from core.reconciliation.io_api_easybeer import lire_commandes_easybeer
from core.reconciliation.reconciliation_core import reconcilier

_log = logging.getLogger("ferment.reconciliation_sync")


def _mois(date_min: str, date_max: str) -> list[tuple[str, str]]:
    """Liste des mois calendaires (début, fin ISO) couvrant [date_min, date_max]."""
    d0 = datetime.date.fromisoformat(date_min)
    d1 = datetime.date.fromisoformat(date_max)
    cur = datetime.date(d0.year, d0.month, 1)
    out = []
    while cur <= d1:
        nxt = (
            datetime.date(cur.year + 1, 1, 1) if cur.month == 12
            else datetime.date(cur.year, cur.month + 1, 1)
        )
        out.append((cur.isoformat(), (nxt - datetime.timedelta(days=1)).isoformat()))
        cur = nxt
    return out


def synchroniser(
    tenant_id: str,
    date_min: str,
    date_max: str,
    *,
    source: str = "api",
    export_path: str | None = None,
    cache=None,
    user_id: str | None = None,
    progress_cb=None,
) -> list[str]:
    """Synchronise chaque mois de [date_min, date_max] et enregistre un snapshot
    par mois. Retourne la liste des mois (period_start) traités.

    progress_cb(mois_faits, mois_total, mois_courant|None) — appelé dans le thread
    de travail : ne pas toucher l'UI, écrire dans un dict relu par un ui.timer.
    """
    # Les commandes Easy Beer sont chargées UNE fois (valables pour tous les mois).
    if source == "api":
        commandes = lire_commandes_easybeer()
    else:
        _, commandes = charger_sources(export_path, date_min=None, date_max=None)

    mois = _mois(date_min, date_max)
    for i, (m_start, m_end) in enumerate(mois):
        if progress_cb:
            progress_cb(i, len(mois), m_start)
        stocks: list = []
        factures = lire_factures_pennylane(
            date_min=m_start, date_max=m_end, cache=cache, stockage_out=stocks,
        )
        res = reconcilier(factures, commandes)
        save_snapshot(tenant_id, m_start, m_end, res, stocks, user_id=user_id)
    if progress_cb:
        progress_cb(len(mois), len(mois), None)
    return [m for m, _ in mois]
