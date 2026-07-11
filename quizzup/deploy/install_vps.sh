#!/usr/bin/env bash
# Installe QuizzUp comme service systemd sur un VPS Ubuntu.
# Usage : bash quizzup/deploy/install_vps.sh   (depuis n'importe où)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"  # racine du repo
VENV="$APP_DIR/.venv-quizzup"
PORT="${QUIZZUP_PORT:-8600}"
HOST="${QUIZZUP_HOST:-127.0.0.1}"   # 0.0.0.0 = accessible depuis Internet

echo "→ Installation dans $APP_DIR (écoute $HOST:$PORT)"

# 1. Environnement Python isolé + dépendances (léger : fastapi + uvicorn)
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/quizzup/requirements.txt"

# 2. Service systemd (démarre au boot, redémarre en cas de crash)
sudo tee /etc/systemd/system/quizzup.service > /dev/null <<EOF
[Unit]
Description=QuizzUp — duels de quiz en temps réel
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
Environment=QUIZZUP_PORT=$PORT
Environment=QUIZZUP_HOST=$HOST
ExecStart=$VENV/bin/python -m quizzup
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now quizzup
sleep 2
if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then
    echo "✅ QuizzUp tourne (port $PORT). Logs : sudo journalctl -u quizzup -f"
else
    echo "❌ Le service ne répond pas — voir : sudo journalctl -u quizzup -n 50"
    exit 1
fi
