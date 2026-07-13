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
    assert len(topics) >= 100
    assert sum(len(t["questions"]) for t in topics.values()) >= 4500
    for topic in topics.values():
        assert len(topic["questions"]) >= 20
        hashes = [item["h"] for item in topic["questions"]]
        assert len(hashes) == len(set(hashes)), f"question dupliquée dans {topic['id']}"
        for item in topic["questions"]:
            assert len(item["choices"]) == 4
            assert 0 <= item["answer"] <= 3
            assert len(set(item["choices"])) == 4, f"doublon de choix : {item['q']}"
            assert item["difficulty"] in (1, 2, 3, 4), \
                f"difficulté invalide dans {topic['id']} : {item['q']}"


def test_handwritten_topics_have_all_tiers(quizzup_modules):
    """Les thèmes écrits à la main proposent les 4 paliers (≥4 questions
    chacun) pour que le mode choisi soit vraiment ressenti. Les thèmes
    *générés* (ex. capitales d'États américains) peuvent être intrinsèquement
    durs et n'ont pas de palier facile — le tirage complète alors depuis les
    paliers voisins (couvert par test_every_topic_is_playable_at_all_tiers)."""
    _, _, q = quizzup_modules
    from collections import Counter

    from quizzup import qgen
    generated = {t["id"] for t in qgen.GENERATED_TOPICS}
    thin = []
    for tid, topic in q.load_topics().items():
        if tid in generated:
            continue
        c = Counter(item["difficulty"] for item in topic["questions"])
        for tier in (1, 2, 3, 4):
            if c.get(tier, 0) < 4:
                thin.append(f"{tid} palier {tier}={c.get(tier, 0)}")
    assert not thin, "paliers trop pauvres : " + ", ".join(thin)


def test_every_topic_is_playable_at_all_tiers(quizzup_modules):
    """Pour CHAQUE thème et CHAQUE palier, un match reçoit toujours 7
    questions distinctes (le tirage complète depuis les paliers voisins si
    un palier est trop pauvre)."""
    _, _, q = quizzup_modules
    for tid in q.load_topics():
        for tier in (1, 2, 3, 4):
            picked = q.pick_game_questions(tid, difficulty=tier)
            assert len(picked) == 7, f"{tid} palier {tier} : {len(picked)} questions"
            assert len({p["q"] for p in picked}) == 7, f"{tid} palier {tier} : doublon"


def test_pick_respects_difficulty(quizzup_modules):
    """Un match d'un palier donné tire en priorité des questions de ce palier."""
    _, _, q = quizzup_modules
    topics = q.load_topics()
    # capitales a beaucoup de questions par palier → tirage 100 % dans le palier
    for tier in (1, 2, 3, 4):
        picked = q.pick_game_questions("capitales", difficulty=tier)
        assert len(picked) == 7
        in_tier = sum(1 for p in picked if p["difficulty"] == tier)
        assert in_tier == 7, f"palier {tier} : seulement {in_tier}/7 dans le bon palier"


def test_xp_scales_with_difficulty(quizzup_modules):
    """Un match extrême rapporte plus d'XP qu'un match facile à score égal."""
    engine, _, _ = quizzup_modules
    easy = engine.Game.xp_for("win", 120, difficulty=1)
    hard = engine.Game.xp_for("win", 120, difficulty=4)
    assert hard > easy


def test_generated_topics_valid(quizzup_modules):
    _, _, q = quizzup_modules
    topics = q.load_topics()
    for tid in ("capitales", "drapeaux", "calcul", "anglais"):
        assert tid in topics, f"thème généré manquant : {tid}"
        pool = topics[tid]["questions"]
        assert len(pool) >= 130, f"{tid} : pool trop petit ({len(pool)})"
        for item in pool:
            assert len(item["choices"]) == 4
            assert len(set(item["choices"])) == 4, f"doublon dans {item['q']}"
            assert 0 <= item["answer"] <= 3


def test_no_repeat_across_consecutive_games(quizzup_modules):
    """Simule des parties consécutives : aucune question ne revient tant que
    le thème n'est pas épuisé, puis le cycle repart proprement."""
    engine, _, q = quizzup_modules
    from quizzup import store

    p1 = engine.Participant("joueur-norepeat", "NoRepeat")
    topic_id = "capitales"
    pool_size = len(q.load_topics()[topic_id]["questions"])
    full_cycles = pool_size // q.ROUNDS_PER_GAME

    seen: set[str] = set()
    games = min(full_cycles, 30)
    for i in range(games):
        picked = engine._pick_fresh_questions(topic_id, [p1])
        hashes = {x["h"] for x in picked}
        assert len(hashes) == 7
        assert not (hashes & seen), f"répétition à la partie {i + 1}"
        seen |= hashes
    # Épuisement : le cycle suivant repart sans erreur
    for _ in range(3):
        picked = engine._pick_fresh_questions(topic_id, [p1])
        assert len(picked) == 7
    store.reset_seen_questions("joueur-norepeat", topic_id)


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

    # Context manager = portal anyio partagé : tous les websockets du test
    # vivent dans le MÊME event loop, comme en production sous uvicorn.
    # Sans lui, chaque websocket a sa propre boucle et les messages émis
    # depuis la boucle de l'adversaire peuvent ne jamais réveiller le lecteur.
    with TestClient(server.app) as client:
        resp = client.get("/api/topics")
        assert resp.status_code == 200
        topic_id = resp.json()[0]["id"]

        _run_full_bot_game(client, topic_id)

        # Le match est bien persistant : stats et classement mis à jour.
        lb = client.get(f"/api/leaderboard/{topic_id}").json()
        assert any(row["name"] == "Testeur" for row in lb)


def _run_full_bot_game(client, topic_id):
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
                assert msg["difficulty"] in (1, 2, 3, 4)
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

    with TestClient(server.app) as client:
        _run_rematch_flow(client, client.get("/api/topics").json()[0]["id"])


def _run_rematch_flow(client, topic_id):
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

    # Portal partagé obligatoire : les deux websockets doivent vivre dans le
    # même event loop pour que les émissions croisées se délivrent (cf. prod).
    with TestClient(server.app) as client:
        _run_quick_match_flow(client, client.get("/api/topics").json()[0]["id"])


def _run_quick_match_flow(client, topic_id):
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


def test_quick_match_separates_difficulties(quizzup_modules):
    """Deux joueurs sur des paliers différents ne sont PAS appariés ensemble
    (chacun tombe sur un bot via le fallback)."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        topic_id = client.get("/api/topics").json()[0]["id"]
        with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
            ws1.send_text(json.dumps({"type": "hello", "name": "Facile"}))
            json.loads(ws1.receive_text())
            ws2.send_text(json.dumps({"type": "hello", "name": "Extreme"}))
            json.loads(ws2.receive_text())

            ws1.send_text(json.dumps({"type": "find_match", "topic_id": topic_id, "difficulty": 1}))
            assert json.loads(ws1.receive_text())["type"] == "queued"
            ws2.send_text(json.dumps({"type": "find_match", "topic_id": topic_id, "difficulty": 4}))
            assert json.loads(ws2.receive_text())["type"] == "queued"

            # Chacun bascule sur un bot (fallback FAST ~1s) au même palier choisi.
            m1 = _drain_until(ws1, "match_found")
            m2 = _drain_until(ws2, "match_found")
            assert m1["opponent"]["is_bot"] is True and m1["difficulty"] == 1
            assert m2["opponent"]["is_bot"] is True and m2["difficulty"] == 4


def test_party_three_players(quizzup_modules):
    """Salon à 3 joueurs : création, join, démarrage, 7 manches, podium final."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        topic_id = client.get("/api/topics").json()[0]["id"]
        with client.websocket_connect("/ws") as w1, \
             client.websocket_connect("/ws") as w2, \
             client.websocket_connect("/ws") as w3:
            for w, name in ((w1, "Hôte"), (w2, "Bea"), (w3, "Caro")):
                w.send_text(json.dumps({"type": "hello", "name": name}))
                json.loads(w.receive_text())

            w1.send_text(json.dumps({"type": "create_party", "topic_id": topic_id,
                                     "difficulty": 2, "rounds": 7}))
            created = _drain_until(w1, "party_created")
            code = created["code"]
            assert len(code) == 4 and created["is_host"] is True

            w2.send_text(json.dumps({"type": "join_party", "code": code}))
            w3.send_text(json.dumps({"type": "join_party", "code": code.lower()}))
            # L'hôte voit la liste monter à 3 joueurs
            members = _drain_until(w1, "party_update")["members"]
            while len(members) < 3:
                members = _drain_until(w1, "party_update")["members"]
            assert {m["name"] for m in members} == {"Hôte", "Bea", "Caro"}

            w1.send_text(json.dumps({"type": "start_party"}))
            socks = {"a": w1, "b": w2, "c": w3}
            gid = {}
            for key, w in socks.items():
                st = _drain_until(w, "party_started")
                assert len(st["players"]) == 3 and st["rounds"] == 7
                gid[key] = st["game_id"]

            # Lockstep sur un seul thread : les 3 sockets reçoivent les mêmes
            # diffusions, on lit un message par socket à tour de rôle.
            overs = {}
            for _ in range(600):
                if len(overs) == 3:
                    break
                for key, w in socks.items():
                    if key in overs:
                        continue
                    m = json.loads(w.receive_text())
                    if m["type"] == "question":
                        w.send_text(json.dumps({"type": "answer", "game_id": gid[key],
                                                "round": m["round"], "choice": 0}))
                    elif m["type"] == "party_over":
                        overs[key] = m
            assert len(overs) == 3
            for key in ("a", "b", "c"):
                assert len(overs[key]["podium"]) == 3
                assert overs[key]["result"] in {"win", "loss", "draw"}


def test_tournament_bot(quizzup_modules):
    """Tournoi 5 thèmes vs bot : 5 manches, une par thème choisi, dernière doublée."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        all_ids = [t["id"] for t in client.get("/api/topics").json()]
        chosen = all_ids[:5]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "name": "Tournoyeur"}))
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "play_tournament",
                                     "topics": chosen, "difficulty": 2}))
            gid, seen_topics, doubles, seen = None, [], [], 0
            for _ in range(300):
                msg = json.loads(ws.receive_text())
                if msg["type"] == "match_found":
                    gid = msg["game_id"]
                    assert msg["topic"]["id"] == "tournoi"
                    assert msg["rounds"] == 5
                    assert msg["tournament"]["count"] == 5
                elif msg["type"] == "question":
                    seen += 1
                    assert msg["round_topic"] is not None
                    seen_topics.append(msg["round_topic"]["name"])
                    if msg["double"]:
                        doubles.append(msg["round"])
                    ws.send_text(json.dumps({"type": "answer", "game_id": gid,
                                             "round": msg["round"], "choice": 0}))
                elif msg["type"] == "game_over":
                    assert msg["result"] in {"win", "loss", "draw"}
                    break
            assert seen == 5
            assert doubles == [5]
            assert len(set(seen_topics)) == 5  # 5 thèmes distincts


def test_tournament_rejects_bad_size(quizzup_modules):
    """Un tournoi de 4 thèmes est refusé (tailles autorisées : 3, 5, 7)."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        ids = [t["id"] for t in client.get("/api/topics").json()][:4]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "name": "Bancal"}))
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "play_tournament", "topics": ids, "difficulty": 2}))
            assert _drain_until(ws, "error")["type"] == "error"


def test_rounds_selectable(quizzup_modules):
    """Une partie bot en 15 questions comporte bien 15 manches (dernière doublée)."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        topic_id = client.get("/api/topics").json()[0]["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "hello", "name": "Longue"}))
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "play_bot", "topic_id": topic_id,
                                     "difficulty": 2, "rounds": 15}))
            gid, seen, double_rounds = None, 0, []
            for _ in range(400):
                msg = json.loads(ws.receive_text())
                if msg["type"] == "match_found":
                    gid = msg["game_id"]
                    assert msg["rounds"] == 15
                elif msg["type"] == "question":
                    seen += 1
                    if msg["double"]:
                        double_rounds.append(msg["round"])
                    ws.send_text(json.dumps({"type": "answer", "game_id": gid,
                                             "round": msg["round"], "choice": 0}))
                elif msg["type"] == "game_over":
                    break
            assert seen == 15
            assert double_rounds == [15]  # seule la dernière manche est doublée


def test_invalid_rounds_falls_back(quizzup_modules):
    engine, _, _ = quizzup_modules
    # 12 n'est pas proposé → le serveur retombe sur la valeur par défaut
    assert 12 not in engine.ALLOWED_ROUNDS
    assert engine.ROUNDS in engine.ALLOWED_ROUNDS


def test_friend_store_and_head_to_head(quizzup_modules):
    """Ajout d'ami par code + bilan tête-à-tête incrémenté."""
    _, _, _ = quizzup_modules
    from quizzup import store

    a = store.create_player("Alpha")
    b = store.create_player("Bravo")
    code_b = store.get_or_create_friend_code(b["id"])
    assert code_b and len(code_b) == 6
    assert store.get_or_create_friend_code(b["id"]) == code_b  # stable

    assert store.add_friend_by_code(a["id"], code_b)["id"] == b["id"]
    assert store.are_friends(a["id"], b["id"]) and store.are_friends(b["id"], a["id"])
    assert store.add_friend_by_code(a["id"], "ZZZZZZ") is None      # code inconnu
    assert store.add_friend_by_code(a["id"], code_b.lower()) is not None  # insensible casse

    store.record_friend_result(a["id"], b["id"], "win")
    store.record_friend_result(a["id"], b["id"], "win")
    store.record_friend_result(b["id"], a["id"], "win")   # = défaite pour A
    store.record_friend_result(a["id"], b["id"], "draw")

    fa = {f["id"]: f for f in store.list_friends(a["id"])}[b["id"]]
    fb = {f["id"]: f for f in store.list_friends(b["id"])}[a["id"]]
    assert (fa["wins"], fa["losses"], fa["draws"]) == (2, 1, 1)
    assert (fb["wins"], fb["losses"], fb["draws"]) == (1, 2, 1)  # miroir


def test_friend_challenge_mutual_start(quizzup_modules):
    """Deux amis se défient (accept) sur le même thème → partie lancée, H2H enregistré."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        topic_id = client.get("/api/topics").json()[0]["id"]
        with client.websocket_connect("/ws") as w1, client.websocket_connect("/ws") as w2:
            w1.send_text(json.dumps({"type": "hello", "name": "Amaury"}))
            welc1 = json.loads(w1.receive_text())
            w2.send_text(json.dumps({"type": "hello", "name": "Bao"}))
            welc2 = json.loads(w2.receive_text())
            id1, id2 = welc1["player"]["id"], welc2["player"]["id"]
            code2 = welc2["friend_code"]

            # Amaury ajoute Bao par code
            w1.send_text(json.dumps({"type": "add_friend", "code": code2}))
            assert _drain_until(w1, "friend_added")["friend"]["id"] == id2

            # Amaury défie Bao ; Bao reçoit l'invite et accepte
            w1.send_text(json.dumps({"type": "challenge_friend", "friend_id": id2,
                                     "topic_id": topic_id, "difficulty": 2}))
            inv = _drain_until(w2, "challenge_received")
            assert inv["from_id"] == id1 and inv["from_name"] == "Amaury"
            assert "label" in inv and inv["label"]  # libellé lisible du défi
            w2.send_text(json.dumps({"type": "accept_challenge", "from_id": id1}))

            m1 = _drain_until(w1, "match_found")
            m2 = _drain_until(w2, "match_found")
            assert m1["opponent"]["name"] == "Bao" and m1["opponent"]["is_bot"] is False
            assert m2["opponent"]["name"] == "Amaury"
            gid = m1["game_id"]

            for _ in range(400):
                msg = json.loads(w1.receive_text())
                if msg["type"] == "question":
                    w1.send_text(json.dumps({"type": "answer", "game_id": gid,
                                             "round": msg["round"], "choice": 0}))
                elif msg["type"] == "game_over":
                    break

            # Le bilan tête-à-tête est enregistré (1 partie jouée entre amis)
            w1.send_text(json.dumps({"type": "list_friends"}))
            friends = _drain_until(w1, "friends")["friends"]
            bao = next(f for f in friends if f["id"] == id2)
            assert bao["wins"] + bao["losses"] + bao["draws"] == 1


def test_friend_challenge_mutual_autostart(quizzup_modules):
    """Les DEUX amis appuient sur « Défier » (même thème+mode) → la partie
    se lance automatiquement, sans que personne n'accepte."""
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        topic_id = client.get("/api/topics").json()[0]["id"]
        with client.websocket_connect("/ws") as w1, client.websocket_connect("/ws") as w2:
            w1.send_text(json.dumps({"type": "hello", "name": "Chloe"}))
            id1 = json.loads(w1.receive_text())["player"]["id"]
            w2.send_text(json.dumps({"type": "hello", "name": "Driss"}))
            welc2 = json.loads(w2.receive_text())
            id2, code2 = welc2["player"]["id"], welc2["friend_code"]

            w1.send_text(json.dumps({"type": "add_friend", "code": code2}))
            _drain_until(w1, "friend_added")

            # Les deux se défient mutuellement sur le même thème + mode
            w1.send_text(json.dumps({"type": "challenge_friend", "friend_id": id2,
                                     "topic_id": topic_id, "difficulty": 3}))
            w2.send_text(json.dumps({"type": "challenge_friend", "friend_id": id1,
                                     "topic_id": topic_id, "difficulty": 3}))

            m1 = _drain_until(w1, "match_found")
            m2 = _drain_until(w2, "match_found")
            assert m1["opponent"]["name"] == "Driss" and m1["difficulty"] == 3
            assert m2["opponent"]["name"] == "Chloe" and m2["opponent"]["is_bot"] is False


def test_room_code_flow(quizzup_modules):
    _, server, _ = quizzup_modules
    from starlette.testclient import TestClient

    with TestClient(server.app) as client:
        _run_room_code_flow(client, client.get("/api/topics").json()[0]["id"])


def _run_room_code_flow(client, topic_id):
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
