"""Point d'entrée : ``python -m quizzup`` lance le serveur sur le port 8600."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # PORT est fourni par les hébergeurs cloud (Render, Railway…) ;
    # QUIZZUP_PORT reste prioritaire pour un déploiement manuel.
    port = int(os.environ.get("QUIZZUP_PORT") or os.environ.get("PORT") or "8600")
    host = os.environ.get("QUIZZUP_HOST", "0.0.0.0")
    uvicorn.run("quizzup.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
