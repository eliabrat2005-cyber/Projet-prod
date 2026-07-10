"""Tests QuizzUp : scoring, niveaux, banque de questions, match complet.

Le match de bout en bout tourne en mode ``QUIZZUP_FAST`` (timings raccourcis)
contre un bot, via le TestClient Starlette (support WebSocket natif).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def quizzup_modules(tmp_path_factory):
    """Importe quizzup en mode FAST avec une DB jetable."""
    os.environ["QUIZZUP_FAST"] = "1"
    os.environ["QUIZZUP_DB"] = str(tmp_path_factory.mktemp("db") / "test.db")
    for name in [m for m in list(sys.modules) if m.startswith("quizzup")]:
        del sys.modules[name]
    engine = importlib.import_module("quizzup.engine")
    server = importlib.import_module("quizzup.server")
    questions = importlib.import_module("quizzup.questions")
    return engine, server, questions


# ─── Scoring ────────────────────────────────────────────────────────────────

def test_score_speed_scale(quizzup_modules):
    engine, _, _ = quizzup_modules
    dur = engine.QUESTION_SECONDS
    assert engine.score_for_answer(True, 0.0, double=False) == 20
    assert engine.score_for_answer(True, dur, double=False) == 10
    assert engine.score_for_answer(True, dur / 2, double=False) == 15
    assert engine.score_for_answer(False, 0.0, double=False) == 0


def test_score_double_round(quizzup_modules):
    engine, _, _ = quizzup_modules
    assert engine.score_for_answer(True, 0.0, double=True) == 40
    assert engine.score_for_answer(True, engine.QUESTION_SECONDS, double=True) == 20
    assert engine.score_for_answer(False, 1.0, double=True) == 0


def test_perfect_game_is_160(quizzup_modules):
    engine, _, _ = quizzup_modules
    total = sum(engine.score_for_answer(True, 0.0, double=(r == 7)) for r in range(1, 8))
    assert total == 160


# ─── Niveaux / titres ───────────────────────────────────────────────────────

def test_levels_monotonic(quizzup_modules):
    _, _, q = quizzup_modules
    assert q.level_from_xp(0)[0] == 0
    assert q.level_from_xp(39)[0] == 0
    assert q.level_from_xp(40)[0] == 1
    levels = [q.level_from_xp(xp)[0] for xp in range(0, 5000, 50)]
    assert levels == sorted(levels)


def test_titles(quizzup_modules):
    _, _, q = quizzup_modules
    assert q.title_for_level(0) == "Débutant"
    assert q.title_for_level(30) == "Expert"
    assert q.title_for_level(95) == "Dieu du thème"


# ─── Banque de questions ────────────────────────────────────────────────────

def test_question_bank_valid(quizzup_modules):
    _, _, q = quizzup_modules
    topics = q.load_topics()
    assert len(topics) >= 10
    for topic in topics.values():
        assert len(topic["questions"]) >= 20
        for item in topic["questions"]:
            assert len(item["choices"]) == 4
            assert 0 <= item["answer"] <= 3
            assert len(set(item["choices"])) == 4, f"doublon de choix : {item['q']}"


def test_pick_game_questions_shuffles(quizzup_modules):
    _, _, q = quizzup_modules
    topic_id = next(iter(q.load_topics()))
    picked = q.pick_game_questions(topic_id)
    assert len(picked) == 7
    assert len({p["q"] for p in picked}) == 7  # pas de doublon dans un match
    for p in picked:
        assert p["choices"][p["answer"]]  # l'index pointe bien sur un choix


# ─── Match complet contre un bot (websocket de bout en bout) ────────────────

def test_full_bot_game(quizzup_modules):
    _, server, q = quizzup_modules
    from starlette.testclient import TestClient

    client = TestClient(server.app)

    resp = client.get("/api/topics")
    assert resp.status_code == 200
    topic_id = resp.json()[0]["id"]

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "hello", "name": "Testeur"}))
        welcome = json.loads(ws.receive_text())
        assert welcome["type"] == "welcome"
        assert welcome["player"]["name"] == "Testeur"

        ws.send_text(json.dumps({"type": "play_bot", "topic_id": topic_id}))

        game_id = None
        rounds_seen = 0
        game_over = None
        # On répond à chaque question et on déroule jusqu'au game_over.
        for _ in range(200):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "match_found":
                game_id = msg["game_id"]
                assert msg["opponent"]["is_bot"] is True
                assert msg["rounds"] == 7
            elif msg["type"] == "question":
                rounds_seen += 1
                assert len(msg["choices"]) == 4
                ws.send_text(json.dumps({"type": "answer", "game_id": game_id,
                                         "round": msg["round"], "choice": 0}))
            elif msg["type"] == "reveal":
                assert msg["you"]["choice"] == 0
                assert "scores" in msg
            elif msg["type"] == "game_over":
                game_over = msg
                break
        assert rounds_seen == 7
        assert game_over is not None
        assert game_over["result"] in {"win", "loss", "draw"}
        assert game_over["xp_gained"] >= 5
        assert game_over["level_after"]["xp"] > 0

    # Le match est bien persistant : stats et classement mis à jour.
    lb = client.get(f"/api/leaderboard/{topic_id}").json()
    assert any(row["name"] == "Testeur" for row in lb)


def _drain_until(ws, wanted: str, limit: int = 300) -> dict:
    """Lit les messages jusqu'à trouver ``wanted`` (répond '0' aux questions)."""
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg["type"] == wanted:
            return msg
        if msg["type"] == "question":
            ws.send_text(json.dumps({"type": "answer", "game_id": msg.get("game_id"),
                                     "round": msg["round"], "choice": 0}))
    raise AssertionError(f"message '{wanted}' jamais reçu")


def test_rematch_keeps_same_bot(quizzup_modules):
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    client = TestClient(server.app)
    topic_id = client.get("/api/topics").json()[0]["id"]

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "hello", "name": "Revanchard"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "play_bot", "topic_id": topic_id}))

        m1 = _drain_until(ws, "match_found")
        # Le message answer a besoin du game_id : on rejoue le drain avec.
        game_id = m1["game_id"]
        bot_name = m1["opponent"]["name"]

        for _ in range(300):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "question":
                ws.send_text(json.dumps({"type": "answer", "game_id": game_id,
                                         "round": msg["round"], "choice": 0}))
            elif msg["type"] == "game_over":
                break

        ws.send_text(json.dumps({"type": "rematch", "game_id": game_id}))
        m2 = _drain_until(ws, "match_found")
        assert m2["opponent"]["name"] == bot_name  # même adversaire, pas un nouveau bot
        assert m2["game_id"] != game_id            # mais une nouvelle partie


def test_quick_match_pairs_two_humans(quizzup_modules):
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    client = TestClient(server.app)
    topic_id = client.get("/api/topics").json()[0]["id"]

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        ws1.send_text(json.dumps({"type": "hello", "name": "Rapide1"}))
        json.loads(ws1.receive_text())
        ws2.send_text(json.dumps({"type": "hello", "name": "Rapide2"}))
        json.loads(ws2.receive_text())

        ws1.send_text(json.dumps({"type": "find_match", "topic_id": topic_id}))
        assert json.loads(ws1.receive_text())["type"] == "queued"
        ws2.send_text(json.dumps({"type": "find_match", "topic_id": topic_id}))

        m1 = _drain_until(ws1, "match_found")
        m2 = _drain_until(ws2, "match_found")
        assert m1["opponent"]["name"] == "Rapide2" and m1["opponent"]["is_bot"] is False
        assert m2["opponent"]["name"] == "Rapide1" and m2["opponent"]["is_bot"] is False

        # Abandon en cours de match : l'autre gagne par forfait.
        ws1.send_text(json.dumps({"type": "leave_game", "game_id": m1["game_id"]}))
        over1 = _drain_until(ws1, "game_over")
        over2 = _drain_until(ws2, "game_over")
        assert over1["result"] == "loss" and over1["forfeit"] is True
        assert over2["result"] == "win" and over2["forfeit"] is True


def test_room_code_flow(quizzup_modules):
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    client = TestClient(server.app)
    topic_id = client.get("/api/topics").json()[0]["id"]

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        ws1.send_text(json.dumps({"type": "hello", "name": "Hôte"}))
        json.loads(ws1.receive_text())
        ws2.send_text(json.dumps({"type": "hello", "name": "Invité"}))
        json.loads(ws2.receive_text())

        ws1.send_text(json.dumps({"type": "create_room", "topic_id": topic_id}))
        room = json.loads(ws1.receive_text())
        assert room["type"] == "room_created" and len(room["code"]) == 4

        ws2.send_text(json.dumps({"type": "join_room", "code": room["code"].lower()}))
        m1 = json.loads(ws1.receive_text())
        m2 = json.loads(ws2.receive_text())
        assert m1["type"] == m2["type"] == "match_found"
        assert m1["opponent"]["name"] == "Invité"
        assert m2["opponent"]["name"] == "Hôte"
