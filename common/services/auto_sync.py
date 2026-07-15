"""common/services/auto_sync.py
==============================
Synchronisation AUTOMATIQUE en arrière-plan de la réconciliation transport ET
de la répartition transport, pour que l'opérateur n'ait jamais à cliquer
« Mettre à jour ».

Les synchros sont INCRÉMENTALES (ne traitent que le nouveau) donc chaque passage
est bon marché après le premier. Un verrou évite qu'une passe auto chevauche une
synchro manuelle (et donc les bans de rate-limit EasyBeer).

Réglage : env AUTO_SYNC_INTERVAL_SECONDS (défaut 3600 = 1 h ; 0 = désactivé).
Lancé au démarrage de l'app (app_nicegui) via asyncio.ensure_future.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import threading

_log = logging.getLogger("ferment.auto_sync")

# Verrou global : une seule synchro (auto OU manuelle) à la fois -> évite les
# doubles appels API concurrents. Les pages peuvent l'importer pour s'y plier.
SYNC_LOCK = threading.Lock()


def _tenants_avec_donnees() -> list[str]:
    """Tenants qui ont déjà des factures traitées (donc à synchroniser)."""
    from db.conn import run_sql
    rows = run_sql("SELECT DISTINCT tenant_id FROM processed_invoices")
    return [str(r["tenant_id"]) for r in rows] if isinstance(rows, list) else []


def sync_tenant(tenant_id: str, *, mois: int = 12) -> dict:
    """Une passe INCRÉMENTALE pour un tenant : réconciliation puis répartition.

    Sous verrou global (SYNC_LOCK) : si une synchro tourne déjà, on saute.
    Retourne un petit récap. Ne lève jamais (log + dict vide en cas d'échec).
    """
    if not SYNC_LOCK.acquire(blocking=False):
        _log.info("auto-sync: une synchro tourne déjà, on saute")
        return {"skipped_locked": True}
    recap: dict = {}
    try:
        d1 = (datetime.date.today() - datetime.timedelta(days=30 * mois)).replace(day=1)
        d2 = datetime.date.today()
        from common.services.reconciliation_sync import synchroniser as sync_recon
        try:
            sync_recon(tenant_id, d1.isoformat(), d2.isoformat())
            recap["reconciliation"] = "ok"
        except Exception:  # noqa: BLE001
            _log.exception("auto-sync réconciliation échouée (tenant %s)", tenant_id)
            recap["reconciliation"] = "erreur"
        from common.services.allocation_sync import synchroniser as sync_alloc
        try:
            recap["repartition"] = sync_alloc(
                tenant_id, d1.strftime("%Y-%m"), d2.strftime("%Y-%m"))
        except Exception:  # noqa: BLE001
            _log.exception("auto-sync répartition échouée (tenant %s)", tenant_id)
            recap["repartition"] = "erreur"
    finally:
        SYNC_LOCK.release()
    return recap


async def auto_sync_loop(interval_seconds: int | None = None):
    """Boucle de fond : synchronise chaque tenant toutes les X secondes."""
    interval = interval_seconds if interval_seconds is not None else int(
        os.getenv("AUTO_SYNC_INTERVAL_SECONDS", "3600"))
    if interval <= 0:
        _log.info("auto-sync désactivée (AUTO_SYNC_INTERVAL_SECONDS<=0)")
        return
    _log.info("auto-sync démarrée (toutes les %ss)", interval)
    await asyncio.sleep(30)  # laisser l'app finir de démarrer
    while True:
        try:
            for tenant_id in _tenants_avec_donnees():
                await asyncio.to_thread(sync_tenant, tenant_id)
        except Exception:  # noqa: BLE001
            _log.exception("auto-sync: passage échoué")
        await asyncio.sleep(interval)
