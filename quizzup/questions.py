"""Chargement de la banque de questions + système de niveaux/titres.

Chaque thème est un JSON dans ``quizzup/data/questions/`` :
    {"id": "...", "name": "...", "icon": "🎬", "color": "#hex",
     "questions": [{"q": "...", "choices": ["a","b","c","d"], "answer": 0}]}
"""
from __future__ import annotations

import json
import random
from pathlib import Path

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


def load_topics() -> dict[str, dict]:
    global _topics
    if _topics:
        return _topics
    topics: dict[str, dict] = {}
    for path in sorted(_DATA_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for i, q in enumerate(data["questions"]):
            if len(q["choices"]) != 4 or not (0 <= q["answer"] <= 3):
                raise ValueError(f"{path.name} question #{i} invalide")
        topics[data["id"]] = data
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


def pick_game_questions(topic_id: str, n: int = ROUNDS_PER_GAME) -> list[dict]:
    """Tire ``n`` questions distinctes, réponses mélangées.

    Chaque item : {"q", "choices" (mélangées), "answer" (index post-mélange)}.
    """
    topic = load_topics()[topic_id]
    picked = random.sample(topic["questions"], min(n, len(topic["questions"])))
    out = []
    for q in picked:
        order = list(range(4))
        random.shuffle(order)
        choices = [q["choices"][i] for i in order]
        answer = order.index(q["answer"])
        out.append({"q": q["q"], "choices": choices, "answer": answer})
    return out
