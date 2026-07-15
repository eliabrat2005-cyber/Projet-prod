"""
core/commandes/extract.py
=========================
Extraction du texte d'un PDF de commande (étage ② du pipeline).

On ne fait QUE sortir le texte brut - l'interprétation (magasin, dates,
lignes) est déléguée à Claude dans ``ai_parse``. Robuste aux formats variés
des enseignes (Franprix, Bio c'Bon, Coop…) car aucune règle de layout n'est
codée ici.

Réutilise ``pdfplumber`` (déjà une dépendance, cf. réconciliation transport).
"""
from __future__ import annotations

import io
import logging

import pdfplumber

_log = logging.getLogger("ferment.commandes.extract")


class PdfTextEmpty(Exception):
    """Levée quand le PDF ne contient aucun texte extractible (PDF scanné/image)."""


def extract_text(pdf_bytes: bytes) -> str:
    """Concatène le texte de toutes les pages d'un PDF.

    Args:
        pdf_bytes: contenu binaire du PDF.

    Returns:
        Le texte brut, pages séparées par un saut de ligne.

    Raises:
        PdfTextEmpty: si aucun texte n'a pu être extrait (PDF image → il
            faudrait de l'OCR, hors périmètre actuel).
    """
    if not pdf_bytes:
        raise PdfTextEmpty("PDF vide (0 octet).")

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(txt)

    text = "\n\n".join(parts).strip()
    if not text:
        raise PdfTextEmpty(
            "Aucun texte extractible - le PDF est probablement scanné (image). "
            "OCR non supporté pour l'instant."
        )
    _log.debug("PDF extrait : %d pages, %d caractères", len(parts), len(text))
    return text


def _decode_text(data: bytes) -> str:
    """Décode des octets texte (txt/csv) en tolérant l'encodage."""
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace").strip()


def _extract_xlsx_text(data: bytes) -> str:
    """Texte d'un classeur Excel (.xlsx) : toutes les cellules non vides."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - fichier corrompu / format inattendu
        return ""
    lines: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                lines.append("\t".join(cells))
    return "\n".join(lines).strip()


def extract_document_text(filename: str, content_type: str, data: bytes) -> str:
    """Extrait le texte d'une pièce jointe, quel que soit son format.

    Gère PDF, texte (txt/csv/tsv), et Excel (xlsx). Renvoie ``""`` si le format
    n'est pas exploitable en texte (image, etc.) ou si le contenu est vide -
    l'appelant décidera quoi faire (analyser le corps du mail, ignorer...).
    Ne lève jamais : un format non géré ne doit pas casser l'ingestion.
    """
    if not data:
        return ""
    name = (filename or "").lower()
    ctype = (content_type or "").lower()

    is_pdf = ctype == "application/pdf" or name.endswith(".pdf")
    if is_pdf:
        try:
            return extract_text(data)
        except PdfTextEmpty:
            return ""  # PDF scanné (image) : pas de texte -> géré en amont

    is_xlsx = (
        name.endswith((".xlsx", ".xlsm"))
        or "spreadsheetml" in ctype
        or "openxmlformats-officedocument.spreadsheet" in ctype
    )
    if is_xlsx:
        return _extract_xlsx_text(data)

    is_text = (
        ctype.startswith("text/")
        or name.endswith((".txt", ".csv", ".tsv", ".text"))
    )
    if is_text:
        return _decode_text(data)

    # Format non géré en texte (image, docx, etc.) : on renvoie vide.
    return ""
