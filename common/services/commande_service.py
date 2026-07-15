"""
common/services/commande_service.py
===================================
Service domaine : commandes magasins reçues par email (PDF) ou upload manuel.

Orchestration du pipeline (sans UI, testable en script/CLI/cron) :
    PDF bytes → core.commandes.extract → core.commandes.ai_parse
              → persistance ``commandes_magasins`` (tenant-scopée, RLS).

Source unique de vérité de l'ingestion : ``ingest_pdf`` est appelée à la fois
par la page web (upload manuel) et par le poller IMAP (``common/email_inbound``).
Toute évolution du flux d'ingestion se fait ICI.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass
from typing import Any

from core.commandes.ai_parse import AiNotConfigured, parse_commande
from core.commandes.extract import extract_document_text
from core.commandes.models import CommandeExtraite, LigneCommande
from db.conn import run_sql_with_tenant

_log = logging.getLogger("ferment.services.commande")


# ─── Modèles typés (vue service) ─────────────────────────────────────────────

@dataclass(frozen=True)
class CommandeSummary:
    """Résumé pour la liste (sans pdf_bytes / raw_text lourds)."""
    id: str
    magasin: str
    date_reception: _dt.date | None
    date_livraison: _dt.date | None
    source: str
    statut: str                       # 'nouveau' | 'traite' | 'erreur'
    confiance: str
    nb_lignes: int
    total_quantite: float
    total_colis: float
    parse_error: str | None
    email_from: str
    created_at: _dt.datetime


@dataclass(frozen=True)
class CommandeDetail:
    """Commande complète pour l'affichage/édition."""
    id: str
    magasin: str
    date_reception: _dt.date | None
    date_livraison: _dt.date | None
    source: str
    statut: str
    confiance: str
    lignes: list[LigneCommande]
    parse_error: str | None
    pdf_filename: str
    email_from: str
    email_subject: str
    created_at: _dt.datetime
    updated_at: _dt.datetime


# Flux : nouveau -> a_produire -> envoyee. a_verifier = doute (facture ?).
# ignore = écarté (non-commande), erreur = échec extraction (upload).
_ALLOWED_STATUT = ("nouveau", "a_produire", "envoyee", "a_verifier", "ignore", "erreur")
_STATUTS_ACTIFS = ("nouveau", "a_produire")


# ─── Ingestion (upload manuel + IMAP) ────────────────────────────────────────

def _ingest_text(
    tenant_id: str,
    *,
    doc_text: str,
    source_bytes: bytes | None,
    source_filename: str,
    source: str,
    email_message_id: str | None = None,
    email_from: str = "",
    email_subject: str = "",
    email_text: str = "",
    user_id: str | None = None,
) -> str:
    """Cœur de l'ingestion : analyse un texte de document + contexte mail.

    Décision ROBUSTE : la présence de lignes produit prime sur le flag
    ``est_commande`` (qui peut flotter d'un appel IA à l'autre) -> on ne perd
    jamais une vraie commande. Un document sans lignes ET classé non-commande
    est rangé en ``'ignore'`` (mail, masqué de la liste) ou ``'erreur'``
    (upload, retour opérateur). On enregistre TOUJOURS une ligne (avec le
    Message-ID) pour fiabiliser la dédup et ne pas ré-analyser en boucle les
    mails non pertinents.
    """
    extraite = CommandeExtraite()
    statut = "nouveau"
    parse_error: str | None = None
    # Pas de lignes lisibles = pas une commande exploitable -> écarté (mail) ou
    # erreur (upload, retour opérateur). Cas "aucune quantité" demandé par le métier.
    no_lignes_statut = "ignore" if source == "email" else "erreur"

    try:
        if not (doc_text or "").strip():
            statut = no_lignes_statut
            parse_error = "Document vide ou format non lisible (image ?)."
        else:
            extraite = parse_commande(doc_text, email_context=email_text)
            if not extraite.lignes:
                # Aucun produit/quantité -> ce n'est pas une commande.
                statut = no_lignes_statut
                parse_error = "Aucune ligne (produits/quantités) détectée."
            elif not extraite.est_commande:
                # Des produits MAIS l'IA doute (facture ?) -> zone "À vérifier".
                statut = "a_verifier"
                parse_error = "Produits détectés mais possible facture — à vérifier."
            # sinon : vraie commande -> statut reste 'nouveau'
    except AiNotConfigured as exc:
        statut = "erreur"
        parse_error = str(exc)
        _log.error("Ingestion commande : IA non configurée - %s", exc)
    except Exception as exc:  # noqa: BLE001 - on ne perd jamais le document
        statut = "erreur"
        parse_error = f"Échec extraction : {exc}"
        _log.exception("Ingestion commande : échec (%s)", source_filename)

    return _insert(
        tenant_id,
        extraite=extraite,
        raw_text=doc_text,
        pdf_bytes=source_bytes,
        filename=source_filename,
        user_id=user_id,
        source=source,
        email_message_id=email_message_id,
        email_from=email_from,
        email_subject=email_subject,
        statut=statut,
        parse_error=parse_error,
    )


def ingest_pdf(
    tenant_id: str,
    pdf_bytes: bytes,
    *,
    filename: str = "",
    user_id: str | None = None,
    source: str = "upload",
) -> str:
    """Ingestion d'un PDF déposé manuellement sur la page /commandes."""
    doc_text = extract_document_text(
        filename or "commande.pdf", "application/pdf", pdf_bytes
    )
    return _ingest_text(
        tenant_id,
        doc_text=doc_text,
        source_bytes=pdf_bytes,
        source_filename=filename,
        source=source,
        user_id=user_id,
    )


def ingest_email(
    tenant_id: str,
    *,
    sender: str,
    subject: str,
    body: str,
    message_id: str,
    attachments: list[tuple[str, str, bytes]],
    user_id: str | None = None,
) -> str | None:
    """Ingestion d'un mail : analyse le corps + TOUTES les pièces jointes
    (PDF, txt, csv, Excel...).

    On combine le texte de toutes les pièces jointes exploitables ; si aucune
    n'est lisible, on analyse le corps du mail lui-même. La 1re pièce jointe
    (lisible si possible) est archivée pour réouverture/téléchargement.
    """
    email_text = f"{subject}\n{body}".strip()
    parts: list[str] = []
    primary_bytes: bytes | None = None
    primary_name = ""
    for fname, ctype, data in attachments:
        txt = extract_document_text(fname, ctype, data)
        if txt.strip():
            parts.append(f"--- Piece jointe : {fname} ---\n{txt}")
            if primary_bytes is None:
                primary_bytes, primary_name = data, fname

    if parts:
        doc_text = "\n\n".join(parts)
    else:
        # Aucune pièce jointe lisible : on analyse le corps du mail.
        doc_text = body or ""
        if attachments and primary_bytes is None:
            primary_name, _ctype, primary_bytes = attachments[0]

    return _ingest_text(
        tenant_id,
        doc_text=doc_text,
        source_bytes=primary_bytes,
        source_filename=primary_name,
        source="email",
        email_message_id=message_id,
        email_from=sender,
        email_subject=subject,
        email_text=email_text,
        user_id=user_id,
    )


def _insert(
    tenant_id: str,
    *,
    extraite: CommandeExtraite,
    raw_text: str,
    pdf_bytes: bytes,
    filename: str,
    user_id: str | None,
    source: str,
    email_message_id: str | None,
    email_from: str,
    email_subject: str,
    statut: str,
    parse_error: str | None,
) -> str:
    lignes_json = json.dumps(
        [li.to_dict() for li in extraite.lignes], ensure_ascii=False
    )
    rows = run_sql_with_tenant(
        """
        INSERT INTO commandes_magasins
            (tenant_id, created_by, magasin, date_reception, date_livraison,
             source, email_message_id, email_from, email_subject,
             pdf_filename, pdf_bytes, raw_text, lignes, confiance,
             statut, parse_error)
        VALUES
            (:tid, :uid, :magasin, :drec, :dliv,
             :source, :msgid, :efrom, :esubj,
             :fname, :pdf, :raw, CAST(:lignes AS jsonb), :conf,
             :statut, :err)
        RETURNING id
        """,
        {
            "tid": tenant_id,
            "uid": user_id,
            "magasin": extraite.magasin,
            "drec": extraite.date_reception,
            "dliv": extraite.date_livraison,
            "source": source,
            "msgid": email_message_id,
            "efrom": email_from or "",
            "esubj": email_subject or "",
            "fname": filename or "",
            "pdf": pdf_bytes,
            "raw": raw_text or "",
            "lignes": lignes_json,
            "conf": extraite.confiance or "",
            "statut": statut,
            "err": parse_error,
        },
        tenant_id=tenant_id,
    )
    cid = str(rows[0]["id"])
    _log.info(
        "Commande ingérée : id=%s magasin=%r source=%s statut=%s lignes=%d tenant=%s",
        cid, extraite.magasin, source, statut, len(extraite.lignes), tenant_id,
    )
    return cid


# ─── Dédup IMAP ───────────────────────────────────────────────────────────────

def message_already_ingested(tenant_id: str, email_message_id: str) -> bool:
    """True si un mail (Message-ID) a déjà été ingéré pour ce tenant."""
    if not email_message_id:
        return False
    rows = run_sql_with_tenant(
        "SELECT 1 FROM commandes_magasins "
        "WHERE tenant_id = :tid AND email_message_id = :msgid LIMIT 1",
        {"tid": tenant_id, "msgid": email_message_id},
        tenant_id=tenant_id,
    )
    return bool(rows)


# ─── Lecture ──────────────────────────────────────────────────────────────────

def list_commandes(
    tenant_id: str,
    *,
    statuts: tuple[str, ...] = _STATUTS_ACTIFS,
    limit: int = 200,
    offset: int = 0,
) -> list[CommandeSummary]:
    """Liste paginée des commandes du tenant filtrée par statut.

    Par défaut : les commandes ACTIVES (``nouveau`` + ``a_produire``). Passer
    ``statuts=("envoyee",)`` pour les commandes passées, ``("a_verifier",)``
    pour la zone de vérification.
    """
    rows = run_sql_with_tenant(
        """
        SELECT id, magasin, date_reception, date_livraison, source, statut,
               confiance, lignes, parse_error, email_from, created_at
        FROM commandes_magasins
        WHERE tenant_id = :tid AND statut = ANY(:statuts)
        ORDER BY created_at DESC
        LIMIT :lim OFFSET :off
        """,
        {"tid": tenant_id, "statuts": list(statuts), "lim": limit, "off": offset},
        tenant_id=tenant_id,
    )
    out: list[CommandeSummary] = []
    for r in rows:
        lignes = _parse_lignes(r["lignes"])
        out.append(
            CommandeSummary(
                id=str(r["id"]),
                magasin=r["magasin"] or "",
                date_reception=r["date_reception"],
                date_livraison=r["date_livraison"],
                source=r["source"],
                statut=r["statut"],
                confiance=r["confiance"] or "",
                nb_lignes=len(lignes),
                total_quantite=sum(li.quantite or 0.0 for li in lignes),
                total_colis=sum(li.colis or 0.0 for li in lignes),
                parse_error=r["parse_error"],
                email_from=r["email_from"] or "",
                created_at=r["created_at"],
            )
        )
    return out


def count_by_statut(tenant_id: str) -> dict[str, int]:
    """Compte les commandes par statut (pour les compteurs / badges)."""
    rows = run_sql_with_tenant(
        "SELECT statut, COUNT(*) AS n FROM commandes_magasins "
        "WHERE tenant_id = :tid GROUP BY statut",
        {"tid": tenant_id},
        tenant_id=tenant_id,
    )
    return {r["statut"]: int(r["n"]) for r in rows}


def set_statut(tenant_id: str, commande_id: str, statut: str) -> bool:
    """Change le statut d'une commande (transitions du flux : à produire,
    envoyée, ou promotion d'une 'à vérifier' en 'nouveau')."""
    if statut not in _ALLOWED_STATUT:
        raise ValueError(f"statut invalide : {statut!r}")
    n = run_sql_with_tenant(
        "UPDATE commandes_magasins SET statut = :statut "
        "WHERE tenant_id = :tid AND id = :id",
        {"statut": statut, "tid": tenant_id, "id": commande_id},
        tenant_id=tenant_id,
    )
    return bool(n)


def get_commande(tenant_id: str, commande_id: str) -> CommandeDetail | None:
    rows = run_sql_with_tenant(
        """
        SELECT id, magasin, date_reception, date_livraison, source, statut,
               confiance, lignes, parse_error, pdf_filename, email_from,
               email_subject, created_at, updated_at
        FROM commandes_magasins
        WHERE tenant_id = :tid AND id = :id
        """,
        {"tid": tenant_id, "id": commande_id},
        tenant_id=tenant_id,
    )
    if not rows:
        return None
    r = rows[0]
    return CommandeDetail(
        id=str(r["id"]),
        magasin=r["magasin"] or "",
        date_reception=r["date_reception"],
        date_livraison=r["date_livraison"],
        source=r["source"],
        statut=r["statut"],
        confiance=r["confiance"] or "",
        lignes=_parse_lignes(r["lignes"]),
        parse_error=r["parse_error"],
        pdf_filename=r["pdf_filename"] or "",
        email_from=r["email_from"] or "",
        email_subject=r["email_subject"] or "",
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def get_pdf(tenant_id: str, commande_id: str) -> tuple[bytes, str] | None:
    """Retourne (pdf_bytes, filename) pour téléchargement, ou None."""
    rows = run_sql_with_tenant(
        "SELECT pdf_bytes, pdf_filename FROM commandes_magasins "
        "WHERE tenant_id = :tid AND id = :id",
        {"tid": tenant_id, "id": commande_id},
        tenant_id=tenant_id,
    )
    if not rows or rows[0]["pdf_bytes"] is None:
        return None
    return bytes(rows[0]["pdf_bytes"]), (rows[0]["pdf_filename"] or "commande.pdf")


# ─── Correction manuelle ──────────────────────────────────────────────────────

def update_commande(
    tenant_id: str,
    commande_id: str,
    *,
    magasin: str | None = None,
    date_reception: str | None = None,
    date_livraison: str | None = None,
    lignes: list[LigneCommande] | None = None,
    statut: str | None = None,
) -> bool:
    """Met à jour les champs corrigés à la main. Renvoie True si une ligne modifiée.

    Seuls les champs non-``None`` sont mis à jour. ``date_*`` attend une chaîne
    ISO (``YYYY-MM-DD``) ou ``""`` pour effacer.
    """
    sets: list[str] = []
    params: dict[str, Any] = {"tid": tenant_id, "id": commande_id}

    if magasin is not None:
        sets.append("magasin = :magasin")
        params["magasin"] = magasin
    if date_reception is not None:
        sets.append("date_reception = :drec")
        params["drec"] = date_reception or None
    if date_livraison is not None:
        sets.append("date_livraison = :dliv")
        params["dliv"] = date_livraison or None
    if lignes is not None:
        sets.append("lignes = CAST(:lignes AS jsonb)")
        params["lignes"] = json.dumps(
            [li.to_dict() for li in lignes], ensure_ascii=False
        )
    if statut is not None:
        if statut not in _ALLOWED_STATUT:
            raise ValueError(f"statut invalide : {statut!r}")
        sets.append("statut = :statut")
        params["statut"] = statut

    if not sets:
        return False

    n = run_sql_with_tenant(
        f"UPDATE commandes_magasins SET {', '.join(sets)} "  # noqa: S608 - colonnes statiques
        "WHERE tenant_id = :tid AND id = :id",
        params,
        tenant_id=tenant_id,
    )
    return bool(n)


def delete_commande(tenant_id: str, commande_id: str) -> bool:
    n = run_sql_with_tenant(
        "DELETE FROM commandes_magasins WHERE tenant_id = :tid AND id = :id",
        {"tid": tenant_id, "id": commande_id},
        tenant_id=tenant_id,
    )
    return bool(n)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_lignes(raw: Any) -> list[LigneCommande]:
    """JSONB lignes (list[dict], ou str selon le driver) → list[LigneCommande]."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [LigneCommande.from_dict(x) for x in raw]
