"""
common/services/facture_store.py
================================
Enregistrement IMMUABLE d'une facture traitée dans les 3 tables du registre
(processed_invoices / processed_invoice_lines / invoice_processing_audit).

Règles CDC :
  - Immuable : que des INSERT (un trigger BD bloque les UPDATE).
  - Unicité : une facture (id_facture_source) n'est jamais traitée 2 fois.
  - Tout-ou-rien : facture + lignes insérées dans UNE transaction.
  - Audit : chaque action tracée.

Couche domaine : accès DB via db.conn, pas de NiceGUI.
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import asdict

from sqlalchemy import text

from core.reconciliation.facture_processor import ResultatTraitement
from db.conn import get_engine, run_sql

_log = logging.getLogger("ferment.facture_store")


def _date_iso(s: str | None) -> str | None:
    """'30/04/26' -> '2026-04-30' (pour une colonne DATE). None si illisible."""
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%d/%m/%y").date().isoformat()
    except ValueError:
        return None


def stats_factures(tenant_id: str) -> dict:
    """Compteurs pour la synthèse : nb factures, OK, rejetées, gasoil, HT total."""
    rows = run_sql(
        """
        SELECT count(*) AS nb,
               count(*) FILTER (WHERE status='OK') AS ok,
               count(*) FILTER (WHERE status='REJECTED') AS rejetees,
               COALESCE(sum(maj_total) FILTER (WHERE status='OK'), 0) AS gasoil,
               COALESCE(sum(montant_ht) FILTER (WHERE status='OK'), 0) AS ht
        FROM processed_invoices WHERE tenant_id=:t
        """,
        {"t": tenant_id},
    )
    return rows[0] if rows else {"nb": 0, "ok": 0, "rejetees": 0, "gasoil": 0, "ht": 0}


def list_factures(tenant_id: str) -> list[dict]:
    """Liste des factures traitées (récentes d'abord)."""
    return run_sql(
        """
        SELECT id, id_facture_source, date_facture, montant_ht, montant_ttc,
               maj_total, nb_lignes, status, date_traitement
        FROM processed_invoices WHERE tenant_id=:t
        ORDER BY date_facture DESC NULLS LAST, date_traitement DESC
        """,
        {"t": tenant_id},
    )


def get_lignes(tenant_id: str, invoice_id: str) -> list[dict]:
    """Lignes d'une facture (avec la part gasoil et le montant final)."""
    return run_sql(
        """
        SELECT ligne_index, exp_date, num_ordre_transport, num_piece, destinataire,
               poids, unite, transport, frais_admin, surtaxe_gasoil, montant_final,
               client_easybeer, statut_match
        FROM processed_invoice_lines WHERE tenant_id=:t AND invoice_id=:i
        ORDER BY ligne_index
        """,
        {"t": tenant_id, "i": invoice_id},
    )


def deja_traitee(tenant_id: str, id_facture: str) -> bool:
    rows = run_sql(
        "SELECT 1 FROM processed_invoices WHERE tenant_id=:t AND id_facture_source=:f",
        {"t": tenant_id, "f": id_facture},
    )
    return bool(rows)


def _audit(conn, tenant_id, id_facture, action, details):
    conn.execute(
        text(
            "INSERT INTO invoice_processing_audit (tenant_id, id_facture_source, action, details) "
            "VALUES (:t, :f, :a, CAST(:d AS JSONB))"
        ),
        {"t": tenant_id, "f": id_facture, "a": action, "d": json.dumps(details)},
    )


def enregistrer(tenant_id: str, res: ResultatTraitement, user_id: str | None = None) -> str:
    """Grave la facture dans le registre. Retourne 'stored' | 'skipped'.

    - déjà traitée → 'skipped' (+ audit SKIP)
    - sinon transaction : facture + lignes (si OK) + audit, tout ou rien
    """
    fac = res.facture
    idf = fac.id_facture

    if idf and deja_traitee(tenant_id, idf):
        with get_engine().begin() as conn:
            _audit(conn, tenant_id, idf, "SKIP", {"raison": "déjà traitée (unicité)"})
        return "skipped"

    with get_engine().begin() as conn:
        _audit(conn, tenant_id, idf, "VALIDATE",
               {"status": res.status, "erreurs": res.erreurs})

        inv_id = conn.execute(
            text(
                """
                INSERT INTO processed_invoices
                  (tenant_id, id_facture_source, date_facture, montant_ht, montant_tva,
                   montant_ttc, maj_go, maj_gnr, maj_total, nb_lignes, status, error_log,
                   facture_data_json, created_by)
                VALUES
                  (:t, :f, :df, :ht, :tva, :ttc, :mgo, :mgnr, :mtot, :nl, :st, :err,
                   CAST(:js AS JSONB), :u)
                RETURNING id
                """
            ),
            {
                "t": tenant_id, "f": idf, "df": _date_iso(fac.date_facture),
                "ht": fac.montant_ht, "tva": fac.montant_tva, "ttc": fac.montant_ttc,
                "mgo": fac.maj_go, "mgnr": fac.maj_gnr, "mtot": fac.maj_total,
                "nl": len(fac.lignes), "st": res.status,
                "err": "\n".join(res.erreurs) if res.erreurs else None,
                "js": json.dumps(asdict(fac)), "u": user_id,
            },
        ).scalar()

        if res.status == "OK":
            for i, L in enumerate(fac.lignes):
                conn.execute(
                    text(
                        """
                        INSERT INTO processed_invoice_lines
                          (tenant_id, invoice_id, id_facture_source, ligne_index, exp_date,
                           jour, num_ordre_transport, num_piece, expediteur, destinataire,
                           poids, unite, transport, frais_admin, montant_brut, surtaxe_gasoil,
                           montant_final, id_commande_easybeer, client_easybeer, statut_match)
                        VALUES
                          (:t, :inv, :f, :idx, :ed, :j, :ot, :pc, :exp, :dest, :poids, :u,
                           :tr, :fa, :mb, :sg, :mf, :cmd, :cl, :sm)
                        """
                    ),
                    {
                        "t": tenant_id, "inv": inv_id, "f": idf, "idx": i,
                        "ed": L.exp_date, "j": L.jour, "ot": L.num_ot, "pc": L.num_piece,
                        "exp": L.expediteur, "dest": L.destinataire, "poids": L.poids,
                        "u": L.unite, "tr": L.transport, "fa": L.frais_admin,
                        "mb": L.montant_brut, "sg": L.surtaxe_gasoil, "mf": L.montant_final,
                        "cmd": L.id_commande_easybeer, "cl": L.client_easybeer,
                        "sm": L.statut_match,
                    },
                )
            _audit(conn, tenant_id, idf, "STORE",
                   {"nb_lignes": len(fac.lignes), "liaison": res.recap_liaison})
        elif res.status == "STOCKAGE":
            _audit(conn, tenant_id, idf, "STORE", {"type": "stockage"})
        else:
            _audit(conn, tenant_id, idf, "ERROR", {"erreurs": res.erreurs})

    return "stored"
