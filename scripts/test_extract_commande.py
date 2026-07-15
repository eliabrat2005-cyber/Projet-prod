#!/usr/bin/env python3
"""
scripts/test_extract_commande.py
================================
Outil de mise au point (dev local) : passe un PDF de commande magasin dans le
pipeline complet (extraction texte → extraction IA) et affiche le résultat
structuré. Aucune écriture en base.

Usage ::

    .venv/bin/python scripts/test_extract_commande.py "/chemin/vers/commande.pdf"
    .venv/bin/python scripts/test_extract_commande.py commande.pdf --texte  # dump aussi le texte brut

Nécessite ``GEMINI_API_KEY`` (dans le ``.env`` local) pour l'étape IA.
Clé gratuite : https://aistudio.google.com/apikey
Sans la clé, seule l'extraction texte est affichée.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Rendre le repo importable quand lancé directement.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from core.commandes.ai_parse import (  # noqa: E402
    AiNotConfigured,
    is_ai_configured,
    parse_commande,
)
from core.commandes.extract import PdfTextEmpty, extract_text  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    dump_texte = "--texte" in argv
    if not args:
        print("Usage : test_extract_commande.py <chemin.pdf> [--texte]")
        return 2

    path = Path(args[0])
    if not path.exists():
        print(f"Fichier introuvable : {path}")
        return 2

    pdf_bytes = path.read_bytes()
    print(f"📄 {path.name} ({len(pdf_bytes) / 1024:.1f} Ko)\n")

    try:
        text = extract_text(pdf_bytes)
    except PdfTextEmpty as exc:
        print(f"❌ Extraction texte impossible : {exc}")
        return 1
    print(f"✓ Texte extrait : {len(text)} caractères")
    if dump_texte:
        print("\n--- TEXTE BRUT ---\n" + text + "\n--- FIN ---\n")

    if not is_ai_configured():
        print("\n⚠️  GEMINI_API_KEY absente, étape IA sautée.")
        print("   Clé gratuite : https://aistudio.google.com/apikey")
        print("   Puis ajoute GEMINI_API_KEY=... dans .env.")
        return 0

    print("\n🤖 Extraction IA en cours…")
    try:
        cmd = parse_commande(text)
    except AiNotConfigured as exc:
        print(f"❌ {exc}")
        return 1

    print("\n=== RÉSULTAT STRUCTURÉ ===")
    print(f"Magasin        : {cmd.magasin!r}")
    print(f"Date réception : {cmd.date_reception}")
    print(f"Date livraison : {cmd.date_livraison}")
    print(f"Confiance      : {cmd.confiance}")
    print(f"Lignes ({len(cmd.lignes)}) :")
    for li in cmd.lignes:
        q = "-" if li.quantite is None else f"{li.quantite:g}"
        colis = "" if li.colis is None else f"  ({li.colis:g} colis)"
        print(f"  • {li.gamme}  ->  {q} {li.unite}{colis}".rstrip())
    print(f"\nTotal quantité : {cmd.total_quantite:g}")
    print("\n--- JSON ---")
    print(json.dumps(cmd.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
