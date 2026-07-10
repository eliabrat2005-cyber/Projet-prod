# QuizzUp ⚡

Recréation du jeu de quiz en duel temps réel : affronte un ami ou un adversaire
en ligne sur le thème de ton choix — réponds juste et vite pour marquer plus de
points.

Application web autonome (aucun lien avec le reste de Ferment Station) :
backend Python (FastAPI + WebSockets), frontend SPA vanilla JS mobile-first,
persistance SQLite.

## Lancer le jeu

```bash
pip install -r requirements.txt   # fastapi + uvicorn (déjà présents via nicegui)
python -m quizzup                 # http://localhost:8600
```

Variables d'environnement optionnelles :

| Variable | Défaut | Rôle |
|---|---|---|
| `QUIZZUP_PORT` | `8600` | Port HTTP |
| `QUIZZUP_HOST` | `0.0.0.0` | Interface d'écoute |
| `QUIZZUP_DB` | `quizzup/quizzup.db` | Chemin du fichier SQLite |
| `QUIZZUP_FAST` | — | `1` = timings raccourcis (tests uniquement) |

Pour jouer à deux : les deux joueurs ouvrent la même URL (le serveur doit être
joignable par les deux — même réseau local, ou déployé derrière un reverse
proxy avec support WebSocket).

## Règles du jeu

- **Duel 1 contre 1** sur un thème choisi parmi 11 (275 questions en français)
- **7 questions** par match, **10 secondes** chacune, 4 choix de réponse
- Compte à rebours 3-2-1 avant chaque question, les deux joueurs voient la
  même question au même moment
- **Bonne réponse : 10 à 20 points selon la rapidité** (instantané = 20,
  dernière seconde = 10) — mauvaise réponse ou temps écoulé : 0
- **Question 7 : points doublés** (jusqu'à 40) — score parfait : **160**
- Tu vois en direct quand ton adversaire a répondu ; les points de chacun
  sont révélés entre chaque question
- Fin de match : victoire / défaite / égalité, gain d'XP, **revanche** possible
- Abandon ou déconnexion en cours de match = victoire de l'adversaire

## Modes de jeu

- **⚡ Partie rapide** — matchmaking : t'affronte le premier joueur en attente
  sur le thème ; si personne sous 5 s, un bot (nom suffixé 🤖) prend le relais.
  La force du bot s'adapte à ton niveau sur le thème.
- **👥 Défier un ami** — génère un code à 4 caractères, ton ami le saisit et
  le duel démarre immédiatement.
- **🤖 Contre un bot** — entraînement immédiat.

## Progression

- **XP par thème** : `score/4 + bonus` (30 victoire / 15 égalité / 5 défaite)
- **Niveaux** (jusqu'à 100) avec **titres** : Débutant → Novice → Apprenti →
  Amateur → Habitué → Passionné → Connaisseur → Spécialiste → Expert →
  Maître → Grand Maître → Légende → Immortel → **Dieu du thème**
- **Classement par thème** (top 20 XP) et **profil** (matchs, victoires,
  défaites, XP total, détail par thème, record de points)

## Architecture

```
quizzup/
├── __main__.py        # python -m quizzup → uvicorn :8600
├── server.py          # FastAPI : SPA, API REST, endpoint WebSocket /ws
├── engine.py          # Moteur : parties, rounds, scoring, matchmaking, bots, revanche
├── questions.py       # Banque de questions + niveaux/titres/XP
├── store.py           # SQLite : joueurs, stats par thème, classements
├── data/questions/    # 11 thèmes × 25 questions (JSON)
└── static/            # SPA : index.html + app.js + style.css (zéro dépendance)
```

Le serveur est **autoritatif** : le chrono démarre quand le serveur émet la
question et les points sont calculés côté serveur à réception de la réponse —
impossible de tricher en modifiant le client.

### Protocole WebSocket (client → serveur)

`hello`, `find_match`, `cancel_find`, `play_bot`, `create_room`, `join_room`,
`cancel_room`, `answer`, `rematch`, `decline_rematch`, `leave_game`,
`get_profile`, `rename`

### (serveur → client)

`welcome`, `need_name`, `queued`, `room_created`, `room_not_found`,
`match_found`, `countdown`, `question`, `opponent_answered`, `reveal`,
`game_over`, `opponent_left`, `rematch_offer`, `rematch_declined`, `profile`,
`error`

## Tests

```bash
pytest tests/test_quizzup.py -v
```

Couvre le scoring, les niveaux/titres, la banque de questions, et un match
complet de bout en bout (login → matchmaking → 7 rounds → XP) via le
TestClient Starlette en mode `QUIZZUP_FAST`.
