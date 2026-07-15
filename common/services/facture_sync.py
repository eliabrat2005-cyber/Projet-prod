"""
common/services/facture_sync.py
===============================
Workflow COMPLET (CDC §8) : récupérer les factures SOFRIPA via l'API Pennylane,
les traiter (parse → valide → relie Easy Beer) et les GRAVER dans le registre.

Idempotent : une facture déjà en base est sautée (unicité id_facture_source),
donc on peut relancer sans risque (cron quotidien ou bouton).

Couche domaine : pas de NiceGUI. Progression via callback.
"""
from __future__ import annotations

import logging
import os
import tempfile

from common.services import facture_store
from core.reconciliation.facture_processor import traiter_facture
from core.reconciliation.io_api import (
    SOFRIPA_SUPPLIER_ID,
    _build_filter,
    _get_pages,
    _session,
)

_log = logging.getLogger("ferment.facture_sync")


def traiter_factures_pennylane(
    tenant_id: str,
    commandes_par_num: dict,
    *,
    date_min: str | None = None,
    date_max: str | None = None,
    user_id: str | None = None,
    progress_cb=None,
) -> dict:
    """Traite toutes les factures SOFRIPA de la période et les enregistre.

    Retourne un récap : {total, stored, skipped, rejected, erreurs:[(id, msg)]}.
    progress_cb(fait, total, id_facture) — dans le thread de travail.
    """
    s = _session()
    params = {}
    filtre = _build_filter(
        supplier_id=SOFRIPA_SUPPLIER_ID, date_min=date_min, date_max=date_max,
    )
    if filtre:
        params["filter"] = filtre
    factures = _get_pages(s, "supplier_invoices", params)

    recap = {"total": len(factures), "stored": 0, "skipped": 0,
             "rejected": 0, "erreurs": []}

    # INCRÉMENTAL : on saute (sans télécharger) toute facture dont l'id Pennylane
    # est déjà en base. La 1re synchro traite tout ; les suivantes ne chargent que
    # les NOUVELLES factures -> quasi instantanées.
    deja = facture_store.processed_source_ids(tenant_id)

    for i, fac_meta in enumerate(factures, start=1):
        sid = str(fac_meta.get("id")) if fac_meta.get("id") is not None else None
        if sid and sid in deja:
            recap["skipped"] += 1
            if progress_cb:
                progress_cb(i, len(factures), None)
            continue
        url = fac_meta.get("public_file_url")
        if not url:
            if progress_cb:
                progress_cb(i, len(factures), None)
            continue
        pdf = s.get(url, timeout=60)
        pdf.raise_for_status()
        fd, chemin = tempfile.mkstemp(suffix=".pdf", prefix="sofripa_")
        os.close(fd)
        try:
            with open(chemin, "wb") as fh:
                fh.write(pdf.content)
            res = traiter_facture(chemin, commandes_par_num)
            statut = facture_store.enregistrer(
                tenant_id, res, user_id=user_id, source_id=sid)
        finally:
            os.unlink(chemin)

        if statut == "skipped":
            recap["skipped"] += 1
        elif res.status == "REJECTED":
            recap["rejected"] += 1
            recap["erreurs"].append((res.facture.id_facture, res.erreurs[:3]))
        else:
            recap["stored"] += 1
        if progress_cb:
            progress_cb(i, len(factures), res.facture.id_facture)

    return recap
