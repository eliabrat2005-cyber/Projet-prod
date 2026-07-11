# Déployer QuizzUp sur le VPS OVH

Aucune installation locale nécessaire — tout tourne sur le VPS.

## 1. Installer (une seule fois)

Depuis un terminal (Mac : Terminal, Windows : PowerShell) :

```bash
ssh ubuntu@92.222.229.87
```

Puis, sur le VPS :

```bash
git clone --branch claude/quizzup-game-recreation-j7s0p6 \
  "$(git -C /home/ubuntu/app remote get-url origin)" ~/quizzup
bash ~/quizzup/quizzup/deploy/install_vps.sh
```

Le script crée un venv léger (fastapi + uvicorn uniquement), installe le
service systemd `quizzup` et vérifie que le serveur répond. Le jeu tourne
alors en permanence sur le port 8600 du VPS (démarrage auto au boot).

## 2. Tester tout de suite (sans configuration DNS)

Depuis ton ordinateur (pas le VPS) :

```bash
ssh -L 8600:localhost:8600 ubuntu@92.222.229.87
```

Laisse ce terminal ouvert et ouvre **http://localhost:8600** dans ton
navigateur : tu joues à QuizzUp qui tourne sur le VPS.

## 3. URL publique pour les téléphones (recommandé pour une démo)

1. Chez ton registrar DNS, ajoute un enregistrement **A** :
   `quizz` → `92.222.229.87` (comme `prod.symbiose-kefir.fr`).
2. Sur le VPS, ajoute le site à Caddy (HTTPS automatique, WebSockets inclus) :

```bash
sudo tee -a /etc/caddy/Caddyfile > /dev/null <<'EOF'

quizz.symbiose-kefir.fr {
    reverse_proxy 127.0.0.1:8600
}
EOF
sudo systemctl reload caddy
```

→ **https://quizz.symbiose-kefir.fr** est jouable depuis n'importe quel
téléphone ou ordinateur. Deux personnes peuvent se défier en direct.

## Mettre à jour le jeu

```bash
cd ~/quizzup && git pull && sudo systemctl restart quizzup
```

## Commandes utiles

```bash
sudo systemctl status quizzup        # état du service
sudo journalctl -u quizzup -f        # logs en direct
sudo systemctl restart quizzup       # redémarrer
```
