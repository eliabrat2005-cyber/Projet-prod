"""Tests de l'extraction texte PDF (core/commandes/extract.py)."""
from __future__ import annotations

import pytest

from core.commandes.extract import (
    PdfTextEmpty,
    extract_document_text,
    extract_text,
)


def _make_pdf(text_lines: list[str]) -> bytes:
    """Génère un PDF minimal en mémoire avec fpdf2 (déjà une dépendance)."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in text_lines:
        pdf.cell(0, 10, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    out = pdf.output()  # fpdf2 >= 2.x renvoie un bytearray
    return bytes(out)


def test_extract_text_lit_le_contenu():
    pdf = _make_pdf([
        "Commande Franprix Bio",
        "Date: 15/06/2026",
        "Kefir Original 33cl - 48 cartons",
    ])
    text = extract_text(pdf)
    assert "Franprix" in text
    assert "48 cartons" in text
    assert "15/06/2026" in text


def test_extract_text_pdf_vide_leve():
    with pytest.raises(PdfTextEmpty):
        extract_text(b"")


def test_extract_text_pdf_sans_texte_leve():
    # Un PDF valide mais sans aucun texte (page blanche) → PdfTextEmpty
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    blank = bytes(pdf.output())
    with pytest.raises(PdfTextEmpty):
        extract_text(blank)


# ─── extract_document_text : tous formats ────────────────────────────────────

def test_extract_document_text_txt():
    data = b"Commande Monoprix\nKefir 33cl x 84\n"
    out = extract_document_text("commande.txt", "text/plain", data)
    assert "Monoprix" in out
    assert "84" in out


def test_extract_document_text_csv():
    data = b"produit;qte\nKefir 33cl;84\nKombucha 75cl;42\n"
    out = extract_document_text("commande.csv", "text/csv", data)
    assert "Kombucha 75cl" in out


def test_extract_document_text_pdf():
    pdf = _make_pdf(["Bon de commande", "Kefir 33cl - 84"])
    out = extract_document_text("c.pdf", "application/pdf", pdf)
    assert "Bon de commande" in out


def test_extract_document_text_xlsx():
    import io

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Produit", "Qte"])
    ws.append(["Kefir 33cl", 84])
    buf = io.BytesIO()
    wb.save(buf)
    out = extract_document_text("c.xlsx", "", buf.getvalue())
    assert "Kefir 33cl" in out
    assert "84" in out


def test_extract_document_text_unsupported_returns_empty():
    # Une "image" (octets bidon) -> pas de texte, mais pas d'exception.
    assert extract_document_text("photo.png", "image/png", b"\x89PNG\r\n") == ""
    assert extract_document_text("vide.pdf", "application/pdf", b"") == ""
