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


def _bornes_date(date_min: str | None, date_max: str | None) -> tuple[str, dict]:
    """Fragment SQL + params pour borner sur date_facture (ISO 'YYYY-MM-DD').

    Filtre uniquement les factures QUI ONT une date dans la plage ; celles sans
    date (date_facture NULL) sont exclues dès qu'une borne est posée."""
    frag, params = "", {}
    if date_min:
        frag += " AND date_facture >= :dmin"
        params["dmin"] = date_min
    if date_max:
        frag += " AND date_facture <= :dmax"
        params["dmax"] = date_max
    return frag, params


def stats_factures(tenant_id: str, date_min: str | None = None,
                   date_max: str | None = None) -> dict:
    """Compteurs pour la synthèse : nb factures, OK, rejetées, gasoil, HT total.

    Bornée à [date_min, date_max] (dates de facture) si fournies -> la synthèse
    reflète le mois affiché, pas tout l'historique."""
    frag, params = _bornes_date(date_min, date_max)
    params["t"] = tenant_id
    rows = run_sql(
        f"""
        SELECT count(*) AS nb,
               count(*) FILTER (WHERE status='OK') AS ok,
               count(*) FILTER (WHERE status='REJECTED') AS rejetees,
               COALESCE(sum(maj_total) FILTER (WHERE status='OK'), 0) AS gasoil,
               COALESCE(sum(montant_ht) FILTER (WHERE status='OK'), 0) AS ht
        FROM processed_invoices WHERE tenant_id=:t{frag}
        """,
        params,
    )
    return rows[0] if rows else {"nb": 0, "ok": 0, "rejetees": 0, "gasoil": 0, "ht": 0}


def list_factures(tenant_id: str, date_min: str | None = None,
                  date_max: str | None = None) -> list[dict]:
    """Liste des factures traitées (récentes d'abord).

    Bornée à [date_min, date_max] (dates de facture) si fournies -> n'affiche que
    les factures du/des mois sélectionné(s)."""
    frag, params = _bornes_date(date_min, date_max)
    params["t"] = tenant_id
    return run_sql(
        f"""
        SELECT id, id_facture_source, date_facture, montant_ht, montant_ttc,
               maj_total, nb_lignes, status, error_log, date_traitement
        FROM processed_invoices WHERE tenant_id=:t{frag}
        ORDER BY date_facture DESC NULLS LAST, date_traitement DESC
        """,
        params,
    )


def lire_lignes_reconciliation(tenant_id: str, date_min: str, date_max: str) -> list:
    """Lignes des factures VALIDÉES (status OK) sur [date_min, date_max] (dates
    de facture ISO), converties en ``LigneFacture`` pour alimenter ``reconcilier``.

    C'est la nouvelle SOURCE de la réconciliation : on ne réconcilie plus que des
    factures dont les comptes tombent juste (Σ lignes = HT, totaux journaliers OK).
    Les factures REJETÉES sont donc automatiquement exclues de la réconciliation.
    """
    from core.reconciliation.io_api import _exp_date_annee
    from core.reconciliation.reconciliation_core import LigneFacture

    rows = run_sql(
        """
        SELECT l.exp_date, l.num_ordre_transport, l.destinataire, l.num_piece,
               l.poids, l.unite, l.quantite, l.montant_final, l.surtaxe_gasoil,
               f.date_facture, f.id_facture_source
        FROM processed_invoice_lines l
        JOIN processed_invoices f ON f.id = l.invoice_id
        WHERE l.tenant_id = :t AND f.status = 'OK'
          AND f.date_facture BETWEEN :a AND :b
        ORDER BY f.date_facture, l.ligne_index
        """,
        {"t": tenant_id, "a": date_min, "b": date_max},
    )

    def _f(v):
        return float(v) if v is not None else None

    return [
        LigneFacture(
            # La date de ligne est stockée « jj/mm » (sans année) : on complète
            # l'année depuis la date de facture, SINON _parse_date_fr renvoie None
            # et les garde-fous de date (pièce ±90j, déduit ±6j) sont désactivés
            # -> faux rapprochements sur de vieilles commandes (n° à 3 chiffres).
            exp_date=_exp_date_annee(
                r["exp_date"],
                r["date_facture"].strftime("%d/%m/%Y") if r["date_facture"] else None,
            ),
            ot=r["num_ordre_transport"],
            client=r["destinataire"],
            piece=r["num_piece"],
            poids=_f(r["poids"]),
            montant=_f(r["montant_final"]),
            surtaxe_gasoil=_f(r["surtaxe_gasoil"]) or 0.0,
            unite=r["unite"],
            quantite=_f(r["quantite"]),
            facture=r["id_facture_source"],
        )
        for r in rows
    ]


def get_facture_data(tenant_id: str, invoice_id: str) -> dict | None:
    """JSON parsé (lignes + totaux journaliers) + statut/erreur d'une facture.

    Sert à AFFICHER une facture REJETÉE : ses lignes ne sont pas dans
    processed_invoice_lines (on ne stocke les lignes que si status=OK), mais le
    parse complet est conservé dans facture_data_json. On peut ainsi montrer où
    ça coince (jour dont le total ne tombe pas juste).
    """
    rows = run_sql(
        """
        SELECT status, error_log, date_facture, montant_ht, maj_total,
               facture_data_json
        FROM processed_invoices WHERE tenant_id=:t AND id=:i
        """,
        {"t": tenant_id, "i": invoice_id},
    )
    if not rows:
        return None
    row = rows[0]
    data = row["facture_data_json"]
    if isinstance(data, str):
        data = json.loads(data)
    return {
        "status": row["status"], "error_log": row["error_log"],
        "date_facture": row["date_facture"], "montant_ht": row["montant_ht"],
        "maj_total": row["maj_total"], "data": data or {},
    }


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


def exporter_registre_xlsx(tenant_id: str) -> bytes:
    """Construit un Excel du registre : onglet Factures + onglet Lignes (gasoil
    et commande Easy Beer inclus). C'est « la base de données » téléchargeable."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    factures = run_sql(
        """
        SELECT id_facture_source, date_facture, status, montant_ht, montant_tva,
               montant_ttc, maj_go, maj_gnr, maj_total, nb_lignes, date_traitement,
               error_log
        FROM processed_invoices WHERE tenant_id=:t
        ORDER BY date_facture DESC NULLS LAST
        """,
        {"t": tenant_id},
    )
    lignes = run_sql(
        """
        SELECT id_facture_source, jour, num_ordre_transport, num_piece, expediteur,
               destinataire, poids, unite, transport, frais_admin, montant_brut,
               surtaxe_gasoil, montant_final, id_commande_easybeer, client_easybeer,
               statut_match
        FROM processed_invoice_lines WHERE tenant_id=:t
        ORDER BY id_facture_source, ligne_index
        """,
        {"t": tenant_id},
    )

    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="15803D")
    hdr_font = Font(bold=True, color="FFFFFF")

    def _cell(v):
        # Excel refuse les datetimes avec fuseau horaire → on l'enlève.
        if isinstance(v, datetime.datetime) and v.tzinfo is not None:
            return v.replace(tzinfo=None)
        return v

    def _fill(ws, rows):
        if not rows:
            ws.append(["(vide)"])
            return
        headers = list(rows[0].keys())
        ws.append(headers)
        for c in ws[1]:
            c.fill = hdr_fill
            c.font = hdr_font
        for r in rows:
            ws.append([_cell(r[h]) for h in headers])
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(
                max(len(h) + 2, 12), 40
            )

    ws1 = wb.active
    ws1.title = "Factures"
    _fill(ws1, factures)
    _fill(wb.create_sheet("Lignes"), lignes)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def processed_source_ids(tenant_id: str) -> set:
    """Ids Pennylane déjà traités (pour sauter le re-téléchargement au sync)."""
    rows = run_sql(
        "SELECT source_id FROM processed_invoices "
        "WHERE tenant_id=:t AND source_id IS NOT NULL",
        {"t": tenant_id},
    )
    return {r["source_id"] for r in rows} if isinstance(rows, list) else set()


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


def enregistrer(tenant_id: str, res: ResultatTraitement, user_id: str | None = None,
                source_id: str | None = None) -> str:
    """Grave la facture dans le registre. Retourne 'stored' | 'skipped'.

    - déjà traitée → 'skipped' (+ audit SKIP)
    - sinon transaction : facture + lignes (si OK) + audit, tout ou rien
    """
    fac = res.facture
    idf = fac.id_facture

    if idf and deja_traitee(tenant_id, idf):
        with get_engine().begin() as conn:
            # Backfill de l'id Pennylane sur les factures déjà en base (sinon on
            # les re-téléchargerait à chaque synchro). Autorisé par le trigger.
            if source_id:
                conn.execute(
                    text("UPDATE processed_invoices SET source_id=:s "
                         "WHERE tenant_id=:t AND id_facture_source=:f "
                         "AND source_id IS NULL"),
                    {"s": source_id, "t": tenant_id, "f": idf},
                )
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
                   source_id, facture_data_json, created_by)
                VALUES
                  (:t, :f, :df, :ht, :tva, :ttc, :mgo, :mgnr, :mtot, :nl, :st, :err,
                   :src, CAST(:js AS JSONB), :u)
                RETURNING id
                """
            ),
            {
                "t": tenant_id, "f": idf, "df": _date_iso(fac.date_facture),
                "ht": fac.montant_ht, "tva": fac.montant_tva, "ttc": fac.montant_ttc,
                "mgo": fac.maj_go, "mgnr": fac.maj_gnr, "mtot": fac.maj_total,
                "nl": len(fac.lignes), "st": res.status,
                "err": "\n".join(res.erreurs) if res.erreurs else None,
                "src": source_id, "js": json.dumps(asdict(fac)), "u": user_id,
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
                           poids, unite, quantite, transport, frais_admin, montant_brut,
                           surtaxe_gasoil, montant_final, id_commande_easybeer,
                           client_easybeer, statut_match)
                        VALUES
                          (:t, :inv, :f, :idx, :ed, :j, :ot, :pc, :exp, :dest, :poids, :u,
                           :qte, :tr, :fa, :mb, :sg, :mf, :cmd, :cl, :sm)
                        """
                    ),
                    {
                        "t": tenant_id, "inv": inv_id, "f": idf, "idx": i,
                        "ed": L.exp_date, "j": L.jour, "ot": L.num_ot, "pc": L.num_piece,
                        "exp": L.expediteur, "dest": L.destinataire, "poids": L.poids,
                        "u": L.unite, "qte": L.quantite,
                        "tr": L.transport, "fa": L.frais_admin,
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
