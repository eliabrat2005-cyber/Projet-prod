"""Chargement de la banque de questions + système de niveaux/titres.

Deux sources fusionnées :
- JSON dans ``quizzup/data/questions/`` (thèmes rédigés à la main) :
    {"id": "...", "name": "...", "icon": "🎬", "color": "#hex",
     "questions": [{"q": "...", "choices": ["a","b","c","d"], "answer": 0}]}
- Thèmes générés programmatiquement (``qgen.py``) — pools stables de
  centaines de questions (capitales, drapeaux, calcul, anglais…).

Anti-répétition : chaque question a un hash stable (md5 du texte) ; le
serveur mémorise les hashes vus par joueur/thème et pioche d'abord dans
les questions jamais vues.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from . import qgen

_DATA_DIR = Path(__file__).parent / "data" / "questions"

ROUNDS_PER_GAME = 7

# Titres par niveau, dans l'esprit QuizUp (le dernier palier est "Dieu du thème").
TITLES: list[tuple[int, str]] = [
    (0, "Débutant"),
    (2, "Novice"),
    (4, "Apprenti"),
    (7, "Amateur"),
    (10, "Habitué"),
    (14, "Passionné"),
    (18, "Connaisseur"),
    (24, "Spécialiste"),
    (30, "Expert"),
    (38, "Maître"),
    (48, "Grand Maître"),
    (60, "Légende"),
    (75, "Immortel"),
    (90, "Dieu du thème"),
]

MAX_LEVEL = 100


def xp_to_next(level: int) -> int:
    """XP nécessaire pour passer du niveau ``level`` au suivant."""
    return 40 + 12 * level


def level_from_xp(xp: int) -> tuple[int, int, int]:
    """Renvoie (niveau, xp dans le niveau courant, xp requis pour le suivant)."""
    level = 0
    remaining = max(0, xp)
    while level < MAX_LEVEL and remaining >= xp_to_next(level):
        remaining -= xp_to_next(level)
        level += 1
    return level, remaining, xp_to_next(level)


def title_for_level(level: int) -> str:
    title = TITLES[0][1]
    for threshold, name in TITLES:
        if level >= threshold:
            title = name
    return title


def level_info(xp: int) -> dict:
    level, cur, needed = level_from_xp(xp)
    return {
        "level": level,
        "xp": xp,
        "xp_in_level": cur,
        "xp_for_next": needed,
        "title": title_for_level(level),
    }


# ─── Banque de questions ────────────────────────────────────────────────────

_topics: dict[str, dict] = {}


def question_hash(text: str) -> str:
    """Identifiant stable d'une question (pour l'anti-répétition)."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def load_topics() -> dict[str, dict]:
    global _topics
    if _topics:
        return _topics
    topics: dict[str, dict] = {}
    for path in sorted(_DATA_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        topics[data["id"]] = data
    topics.update(qgen.generated_topics())
    for tid, data in topics.items():
        for i, q in enumerate(data["questions"]):
            if len(q["choices"]) != 4 or not (0 <= q["answer"] <= 3):
                raise ValueError(f"{tid} question #{i} invalide")
            q["h"] = question_hash(q["q"])
    _topics = topics
    return topics


def topic_list() -> list[dict]:
    """Liste des thèmes sans les questions (pour le client)."""
    return [
        {"id": t["id"], "name": t["name"], "icon": t["icon"], "color": t["color"],
         "count": len(t["questions"])}
        for t in load_topics().values()
    ]


def get_topic(topic_id: str) -> dict | None:
    return load_topics().get(topic_id)


def pick_game_questions(topic_id: str, n: int = ROUNDS_PER_GAME,
                        exclude: set[str] | None = None) -> list[dict]:
    """Tire ``n`` questions distinctes, réponses mélangées.

    ``exclude`` = hashes déjà vus par les joueurs : on pioche d'abord dans
    les questions jamais vues, et on ne complète avec des déjà-vues que si
    le thème est épuisé. Chaque item : {"q", "h", "choices", "answer"}.
    """
    topic = load_topics()[topic_id]
    pool = topic["questions"]
    n = min(n, len(pool))
    exclude = exclude or set()
    fresh = [q for q in pool if q["h"] not in exclude]
    if len(fresh) >= n:
        picked = random.sample(fresh, n)
    else:  # thème épuisé : toutes les fraîches + complément déjà vu
        seen_pool = [q for q in pool if q["h"] in exclude]
        picked = fresh + random.sample(seen_pool, n - len(fresh))
        random.shuffle(picked)
    out = []
    for q in picked:
        order = list(range(4))
        random.shuffle(order)
        choices = [q["choices"][i] for i in order]
        answer = order.index(q["answer"])
        out.append({"q": q["q"], "h": q["h"], "choices": choices, "answer": answer})
    return out
