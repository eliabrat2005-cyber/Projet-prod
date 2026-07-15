#!/usr/bin/env python3
"""
scripts/test_imap_commandes.py
==============================
Test de l'ingestion IMAP des commandes (dev local).

Par défaut : **dry-run** - se connecte à la boîte mail, liste les mails
détectés (selon IMAP_SEARCH) qui portent un PDF, SANS rien ingérer ni marquer
comme lu. Idéal pour vérifier le filtre avant de lâcher le robot.

    .venv/bin/python scripts/test_imap_commandes.py            # dry-run (liste)
    .venv/bin/python scripts/test_imap_commandes.py --ingest   # ingère pour de vrai

Variables .env requises : IMAP_HOST, IMAP_USER, IMAP_PASSWORD
(+ optionnel IMAP_PORT, IMAP_FOLDER, IMAP_SEARCH). Gmail : utiliser un
« mot de passe d'application » (App Password), pas le mot de passe du compte.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from common.email_inbound import (  # noqa: E402
    fetch_new_order_pdfs,
    imap_config_from_env,
    poll_once,
)


def main(argv: list[str]) -> int:
    do_ingest = "--ingest" in argv

    config = imap_config_from_env()
    if config is None:
        print("❌ IMAP non configuré. Ajoute dans .env :")
        print("   IMAP_HOST=imap.gmail.com")
        print("   IMAP_USER=ton.email@gmail.com")
        print("   IMAP_PASSWORD=<mot de passe d'application Gmail>")
        print("   (IMAP_SEARCH optionnel — par défaut tous les mails non lus ;")
        print("    l'IA décide elle-même si chaque PDF est une commande.)")
        return 2

    print(f"📬 Connexion : {config.user}@{config.host}:{config.port}")
    print(f"   Dossier : {config.folder} | Filtre : {config.search!r}\n")

    try:
        orders = fetch_new_order_pdfs(config)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Connexion/recherche IMAP échouée : {exc}")
        print("   (Gmail : as-tu activé un App Password + l'IMAP dans les réglages ?)")
        return 1

    if not orders:
        print("Aucun mail avec PDF ne correspond au filtre. "
              "Envoie-toi un mail de test (avec le PDF) qui matche IMAP_SEARCH.")
        return 0

    print(f"✓ {len(orders)} mail(s) détecté(s) :\n")
    for o in orders:
        atts = ", ".join(f"{n} [{ct}] ({len(b) // 1024} Ko)" for n, ct, b in o.attachments)
        print(f"  • De   : {o.sender}")
        print(f"    Sujet: {o.subject}")
        print(f"    PJ   : {atts or '(aucune)'}\n")

    if not do_ingest:
        print("— DRY-RUN — rien n'a été ingéré ni marqué lu.")
        print("Relance avec --ingest pour traiter ces mails (extraction + base).")
        return 0

    from common.sync.scheduler import _get_default_tenant_id

    tenant_id = _get_default_tenant_id()
    if not tenant_id:
        print("❌ tenant_id introuvable (ALLOWED_TENANTS ?).")
        return 1
    print("⚙️  Ingestion en cours…")
    n = poll_once(config, tenant_id)
    print(f"✓ {n} commande(s) ingérée(s). Va voir /commandes. "
          "(Les mails ne sont pas modifiés ; re-lancer est sans risque, "
          "la dédup se fait sur le Message-ID.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
