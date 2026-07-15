"""Serveur QuizzUp : FastAPI + WebSocket.

- ``GET /``            → SPA (static/index.html)
- ``GET /static/*``    → assets
- ``GET /api/topics``  → liste des thèmes
- ``GET /api/leaderboard/{topic}`` → classement du thème
- ``WS  /ws``          → tout le temps réel (auth légère, matchmaking, duel)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import engine as engine_mod
from . import questions as qbank
from . import store
from .engine import Lobby, Participant

log = logging.getLogger("quizzup")
logging.basicConfig(level=logging.INFO)

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="QuizzUp")
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

lobby = Lobby()


@app.get("/")
async def index():
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/topics")
async def api_topics():
    return qbank.topic_list()


@app.get("/api/cards")
async def api_cards(topic_id: str = "", difficulty: int = 0, n: int = 40):
    """Cartes d'apprentissage : questions aléatoires (+ réponse) filtrées
    par thème et/ou palier. Utilisé par l'onglet Cartes (flashcards)."""
    topics = qbank.load_topics()
    sources = [topics[topic_id]] if topic_id in topics else list(topics.values())
    pool = [(t, q) for t in sources for q in t["questions"]
            if difficulty not in (1, 2, 3, 4) or q["difficulty"] == difficulty]
    if not pool:
        return []
    n = max(1, min(int(n), 60))
    picked = random.sample(pool, min(n, len(pool)))
    return [{"q": q["q"], "a": q["choices"][q["answer"]],
             "difficulty": q["difficulty"],
             "topic": {"id": t["id"], "name": t["name"],
                       "icon": t["icon"], "color": t["color"]}}
            for t, q in picked]


@app.get("/api/leaderboard/{topic_id}")
async def api_leaderboard(topic_id: str):
    if qbank.get_topic(topic_id) is None:
        return JSONResponse({"error": "unknown topic"}, status_code=404)
    rows = store.leaderboard(topic_id)
    return [
        {"name": r["name"], "player_id": r["player_id"],
         **qbank.level_info(r["xp"]), "games": r["games"], "wins": r["wins"]}
        for r in rows
    ]


# ─── Session WebSocket ──────────────────────────────────────────────────────

def _profile_payload(pid: str) -> dict:
    per_topic = store.all_topic_stats(pid)
    return {
        "global": store.global_profile(pid),
        "topics": {tid: {**stats, **qbank.level_info(stats["xp"])}
                   for tid, stats in per_topic.items()},
    }


class Session:
    """Une connexion WebSocket identifiée (après le message ``hello``)."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.participant: Participant | None = None
        self._send_lock = asyncio.Lock()

    async def send(self, msg: dict) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    async def handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "hello":
            await self._hello(msg)
            return
        if self.participant is None:
            await self.send({"type": "error", "message": "Envoie d'abord 'hello'."})
            return
        handler = {
            "find_match": self._find_match,
            "cancel_find": self._cancel_find,
            "play_bot": self._play_bot,
            "create_room": self._create_room,
            "cancel_room": self._cancel_room,
            "join_room": self._join_room,
            "answer": self._answer,
            "rematch": self._rematch,
            "decline_rematch": self._decline_rematch,
            "leave_game": self._leave_game,
            "get_profile": self._get_profile,
            "rename": self._rename,
            "add_friend": self._add_friend,
            "list_friends": self._list_friends,
            "get_friend_profile": self._get_friend_profile,
            "challenge_friend": self._challenge_friend,
            "play_tournament": self._play_tournament,
            "challenge_tournament": self._challenge_tournament,
            "accept_challenge": self._accept_challenge,
            "decline_challenge": self._decline_challenge,
            "cancel_challenge": self._cancel_challenge,
            "create_party": self._create_party,
            "join_party": self._join_party,
            "start_party": self._start_party,
            "leave_party": self._leave_party,
        }.get(mtype)
        if handler is None:
            await self.send({"type": "error", "message": f"Type inconnu : {mtype}"})
        else:
            await handler(msg)

    # ── Handlers ───────────────────────────────────────────────────────────

    async def _hello(self, msg: dict) -> None:
        name = str(msg.get("name") or "").strip()[:20]
        pid = msg.get("player_id")
        player = store.get_player(pid) if pid else None
        if player is None:
            if not name:
                await self.send({"type": "need_name"})
                return
            player = store.create_player(name)
        elif name and name != player["name"]:
            store.rename_player(player["id"], name)
            player["name"] = name
        self.participant = Participant(player["id"], player["name"])
        self.participant.send = self.send
        await lobby.set_online(self.participant)
        await self.send({
            "type": "welcome",
            "player": player,
            "topics": qbank.topic_list(),
            "profile": _profile_payload(player["id"]),
            "friend_code": store.get_or_create_friend_code(player["id"]),
            "friends": self._friends_payload(player["id"]),
        })

    def _friends_payload(self, pid: str) -> list[dict]:
        online = set(lobby.online)
        return [{**f, "online": f["id"] in online} for f in store.list_friends(pid)]

    def _valid_topic(self, msg: dict) -> str | None:
        topic_id = msg.get("topic_id")
        return topic_id if topic_id and qbank.get_topic(topic_id) else None

    @staticmethod
    def _difficulty(msg: dict) -> int:
        try:
            d = int(msg.get("difficulty", qbank.DEFAULT_DIFFICULTY))
        except (TypeError, ValueError):
            return qbank.DEFAULT_DIFFICULTY
        return d if d in qbank.DIFFICULTIES else qbank.DEFAULT_DIFFICULTY

    @staticmethod
    def _rounds(msg: dict) -> int:
        try:
            r = int(msg.get("rounds", engine_mod.ROUNDS))
        except (TypeError, ValueError):
            return engine_mod.ROUNDS
        return r if r in engine_mod.ALLOWED_ROUNDS else engine_mod.ROUNDS

    @staticmethod
    def _valid_tournament_topics(msg: dict) -> list[str] | None:
        raw = msg.get("topics")
        if not isinstance(raw, list):
            return None
        seen, topics = set(), []
        for t in raw:
            if isinstance(t, str) and t not in seen and qbank.get_topic(t):
                seen.add(t)
                topics.append(t)
        return topics if len(topics) in engine_mod.TOURNAMENT_SIZES else None

    async def _find_match(self, msg: dict) -> None:
        topic_id = self._valid_topic(msg)
        if topic_id is None:
            await self.send({"type": "error", "message": "Thème inconnu."})
            return
        await lobby.find_match(topic_id, self.participant,
                               difficulty=self._difficulty(msg),
                               rounds=self._rounds(msg))

    async def _cancel_find(self, msg: dict) -> None:
        lobby.cancel_find(self.participant)
        await self.send({"type": "find_cancelled"})

    async def _play_bot(self, msg: dict) -> None:
        topic_id = self._valid_topic(msg)
        if topic_id is None:
            await self.send({"type": "error", "message": "Thème inconnu."})
            return
        await lobby.start_bot_game(topic_id, self.participant,
                                   difficulty=self._difficulty(msg),
                                   rounds=self._rounds(msg))

    async def _create_room(self, msg: dict) -> None:
        topic_id = self._valid_topic(msg)
        if topic_id is None:
            await self.send({"type": "error", "message": "Thème inconnu."})
            return
        lobby.close_rooms_of(self.participant.player_id)
        difficulty = self._difficulty(msg)
        code = lobby.create_room(topic_id, self.participant, difficulty=difficulty)
        await self.send({"type": "room_created", "code": code,
                         "topic_id": topic_id, "difficulty": difficulty})

    async def _cancel_room(self, msg: dict) -> None:
        lobby.close_rooms_of(self.participant.player_id)
        await self.send({"type": "room_cancelled"})

    async def _join_room(self, msg: dict) -> None:
        code = str(msg.get("code") or "")
        game = await lobby.join_room(code, self.participant)
        if game is None:
            await self.send({"type": "room_not_found", "code": code})

    async def _answer(self, msg: dict) -> None:
        gid = str(msg.get("game_id"))
        pid = self.participant.player_id
        game = lobby.games.get(gid)
        if game is not None:
            if game.submit_answer(pid, msg.get("round"), msg.get("choice")):
                await self.send({"type": "answer_ack", "round": msg.get("round")})
                await game.notify_answered(pid, msg.get("round"))
            return
        party = lobby.party_games.get(gid)
        if party is not None and party.submit_answer(pid, msg.get("round"), msg.get("choice")):
            await self.send({"type": "answer_ack", "round": msg.get("round")})

    async def _rematch(self, msg: dict) -> None:
        await lobby.request_rematch(str(msg.get("game_id")), self.participant.player_id)

    async def _decline_rematch(self, msg: dict) -> None:
        await lobby.decline_rematch(str(msg.get("game_id")), self.participant.player_id)

    async def _leave_game(self, msg: dict) -> None:
        gid = str(msg.get("game_id"))
        game = lobby.games.get(gid)
        if game and not game.finished:
            opp = game.opponent_of(self.participant.player_id)
            await opp.emit({"type": "opponent_left", "game_id": game.id})
            await game.forfeit(self.participant.player_id)
            return
        party = lobby.party_games.get(gid)
        if party and not party.finished:
            await party.remove_player(self.participant.player_id)

    async def _get_profile(self, msg: dict) -> None:
        await self.send({"type": "profile",
                         "profile": _profile_payload(self.participant.player_id)})

    async def _rename(self, msg: dict) -> None:
        name = str(msg.get("name") or "").strip()[:20]
        if name:
            store.rename_player(self.participant.player_id, name)
            self.participant.name = name
            await self.send({"type": "renamed", "name": name})

    # ── Amis ─────────────────────────────────────────────────────────────────

    async def _add_friend(self, msg: dict) -> None:
        pid = self.participant.player_id
        friend = store.add_friend_by_code(pid, str(msg.get("code") or ""))
        if friend is None:
            await self.send({"type": "friend_error",
                             "message": "Code ami introuvable."})
            return
        await self.send({"type": "friend_added", "friend": friend,
                         "friends": self._friends_payload(pid)})
        # Prévient l'ami (s'il est en ligne) que sa liste a changé
        other = lobby.online.get(friend["id"])
        if other is not None:
            await other.emit({"type": "friends",
                              "friends": self._friends_payload(friend["id"])})

    async def _list_friends(self, msg: dict) -> None:
        await self.send({"type": "friends",
                         "friends": self._friends_payload(self.participant.player_id)})

    async def _get_friend_profile(self, msg: dict) -> None:
        fid = str(msg.get("friend_id") or "")
        pid = self.participant.player_id
        if not store.are_friends(pid, fid):
            await self.send({"type": "friend_error", "message": "Vous n'êtes pas amis."})
            return
        friend = store.get_player(fid)
        h2h = next((f for f in store.list_friends(pid) if f["id"] == fid),
                   {"wins": 0, "losses": 0, "draws": 0})
        await self.send({
            "type": "friend_profile",
            "friend": {"id": fid, "name": friend["name"] if friend else "?"},
            "global": store.global_profile(fid),
            "h2h": {"wins": h2h["wins"], "losses": h2h["losses"], "draws": h2h["draws"]},
            "online": fid in lobby.online,
        })

    async def _challenge_friend(self, msg: dict) -> None:
        topic_id = self._valid_topic(msg)
        friend_id = str(msg.get("friend_id") or "")
        if topic_id is None or not friend_id:
            await self.send({"type": "friend_error", "message": "Défi invalide."})
            return
        if not store.are_friends(self.participant.player_id, friend_id):
            await self.send({"type": "friend_error", "message": "Vous n'êtes pas amis."})
            return
        diff, rounds = self._difficulty(msg), self._rounds(msg)
        spec = {"kind": "solo", "topic": topic_id, "difficulty": diff, "rounds": rounds}
        t = qbank.get_topic(topic_id)
        label = f"{t['icon']} {t['name']} · {qbank.DIFFICULTIES[diff]} · {rounds} q."
        await lobby.challenge_friend_spec(self.participant, friend_id, spec, label)

    async def _play_tournament(self, msg: dict) -> None:
        topics = self._valid_tournament_topics(msg)
        if topics is None:
            await self.send({"type": "error", "message": "Choisis 3, 5 ou 7 thèmes."})
            return
        await lobby.start_bot_tournament(self.participant, topics,
                                         difficulty=self._difficulty(msg))

    async def _challenge_tournament(self, msg: dict) -> None:
        topics = self._valid_tournament_topics(msg)
        friend_id = str(msg.get("friend_id") or "")
        if topics is None or not friend_id:
            await self.send({"type": "friend_error", "message": "Tournoi invalide."})
            return
        if not store.are_friends(self.participant.player_id, friend_id):
            await self.send({"type": "friend_error", "message": "Vous n'êtes pas amis."})
            return
        diff = self._difficulty(msg)
        spec = {"kind": "tournoi", "topics": topics, "difficulty": diff}
        label = f"🏆 Tournoi · {len(topics)} thèmes · {qbank.DIFFICULTIES[diff]}"
        await lobby.challenge_friend_spec(self.participant, friend_id, spec, label)

    async def _accept_challenge(self, msg: dict) -> None:
        await lobby.accept_challenge(self.participant, str(msg.get("from_id") or ""))

    async def _decline_challenge(self, msg: dict) -> None:
        await lobby.decline_challenge(self.participant, str(msg.get("from_id") or ""))

    async def _cancel_challenge(self, msg: dict) -> None:
        await lobby.cancel_challenge(self.participant)

    # ── Parties de groupe ────────────────────────────────────────────────────

    async def _create_party(self, msg: dict) -> None:
        topics = self._valid_tournament_topics(msg)
        diff = self._difficulty(msg)
        if topics is not None:  # tournoi de groupe
            spec = {"kind": "tournoi", "topics": topics, "difficulty": diff,
                    "label": f"🏆 Tournoi · {len(topics)} thèmes · {qbank.DIFFICULTIES[diff]}"}
        else:
            topic_id = self._valid_topic(msg)
            if topic_id is None:
                await self.send({"type": "party_error", "message": "Thème inconnu."})
                return
            rounds = self._rounds(msg)
            t = qbank.get_topic(topic_id)
            spec = {"kind": "solo", "topic": topic_id, "difficulty": diff, "rounds": rounds,
                    "label": f"{t['icon']} {t['name']} · {qbank.DIFFICULTIES[diff]} · {rounds} q."}
        await lobby.create_party(self.participant, spec)

    async def _join_party(self, msg: dict) -> None:
        await lobby.join_party(self.participant, str(msg.get("code") or ""))

    async def _start_party(self, msg: dict) -> None:
        await lobby.start_party(self.participant)

    async def _leave_party(self, msg: dict) -> None:
        await lobby.leave_party(self.participant)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = Session(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await session.send({"type": "error", "message": "JSON invalide."})
                continue
            await session.handle(msg)
    except WebSocketDisconnect:
        pass
    finally:
        if session.participant is not None:
            session.participant.send = None
            await lobby.handle_disconnect(session.participant)
