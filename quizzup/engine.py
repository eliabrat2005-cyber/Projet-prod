"""Moteur de jeu temps réel : duels 1v1, matchmaking, bots, revanche.

Serveur autoritatif : le chrono d'une question démarre quand le serveur
envoie l'événement ``question`` et les points sont calculés côté serveur
à la réception de la réponse. Barème (fidèle à l'original) :

- 7 questions par match, 10 secondes chacune
- bonne réponse : 10 à 20 points selon la rapidité
- mauvaise réponse / temps écoulé : 0
- question 7 : points doublés → score parfait = 160
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import time
import uuid
from collections.abc import Callable

from . import questions as qbank
from . import store

log = logging.getLogger("quizzup")

ROUNDS = qbank.ROUNDS_PER_GAME
FAST = os.environ.get("QUIZZUP_FAST") == "1"  # timings raccourcis pour les tests

QUESTION_SECONDS = 1.2 if FAST else 10.0
COUNTDOWN_SECONDS = 0.2 if FAST else 3.0
REVEAL_SECONDS = 0.2 if FAST else 3.2
VS_SCREEN_SECONDS = 0.2 if FAST else 2.8
ANSWER_GRACE = 0.35  # tolérance réseau sur la fin de question
MATCHMAKING_BOT_FALLBACK = 1.0 if FAST else 5.0

BOT_NAMES = [
    "Léa", "Hugo", "Chloé", "Nathan", "Manon", "Enzo", "Camille", "Louis",
    "Emma", "Jules", "Zoé", "Théo", "Inès", "Gabriel", "Jade", "Rafael",
    "Louna", "Adam", "Alice", "Sacha",
]


def score_for_answer(correct: bool, elapsed: float, double: bool) -> int:
    """10-20 pts selon la rapidité (20 = instantané, 10 = dernière seconde)."""
    if not correct:
        return 0
    ratio = max(0.0, min(1.0, elapsed / QUESTION_SECONDS))
    pts = 10 + round(10 * (1 - ratio))
    return pts * 2 if double else pts


# ─── Participants ───────────────────────────────────────────────────────────

class Participant:
    """Un côté du duel : humain connecté (Session) ou bot."""

    def __init__(self, player_id: str, name: str, is_bot: bool = False):
        self.player_id = player_id
        self.name = name
        self.is_bot = is_bot
        self.send: Callable[[dict], asyncio.Future] | None = None  # posé par Session

    async def emit(self, msg: dict) -> None:
        if self.send is None:
            return
        try:
            await self.send(msg)
        except Exception:  # connexion fermée entre-temps — le disconnect gère
            pass


def make_bot(level_hint: int) -> tuple[Participant, float]:
    """Crée un bot dont la force suit le niveau du joueur sur le thème."""
    name = f"{random.choice(BOT_NAMES)} 🤖"
    accuracy = min(0.85, 0.55 + level_hint * 0.008)
    bot = Participant(player_id=f"bot-{uuid.uuid4().hex[:8]}", name=name, is_bot=True)
    return bot, accuracy


# ─── Partie ─────────────────────────────────────────────────────────────────

def _pick_fresh_questions(topic_id: str, players: list[Participant],
                          difficulty: int | None = None) -> list[dict]:
    """Tire les questions du match en évitant celles déjà vues par les joueurs.

    Quand un joueur a fait le tour du palier demandé, son historique du
    thème est remis à zéro (nouveau cycle) — garantit un maximum de
    variété partie après partie.
    """
    humans = [p for p in players if not p.is_bot]
    exclude: set[str] = set()
    for p in humans:
        exclude |= store.get_seen_questions(p.player_id, topic_id)
    pool = qbank.get_topic(topic_id)["questions"]
    tier_hashes = {q["h"] for q in pool
                   if difficulty is None or q["difficulty"] == difficulty}
    if len(tier_hashes) >= ROUNDS and len(tier_hashes - exclude) < ROUNDS:
        for p in humans:
            store.reset_seen_questions(p.player_id, topic_id)
        exclude = set()
    questions = qbank.pick_game_questions(topic_id, exclude=exclude,
                                          difficulty=difficulty)
    picked = [q["h"] for q in questions]
    for p in humans:
        store.record_seen_questions(p.player_id, topic_id, picked)
    return questions


class Game:
    def __init__(self, topic_id: str, p1: Participant, p2: Participant,
                 bot_accuracy: float = 0.7,
                 difficulty: int = qbank.DEFAULT_DIFFICULTY):
        self.id = uuid.uuid4().hex[:12]
        self.topic_id = topic_id
        self.difficulty = difficulty
        self.players = [p1, p2]
        self.bot_accuracy = bot_accuracy
        self.questions = _pick_fresh_questions(topic_id, self.players, difficulty)
        self.scores = {p1.player_id: 0, p2.player_id: 0}
        self.round_no = 0
        self.round_answers: dict[str, tuple[int, float]] = {}  # pid -> (choix, temps)
        self.round_start = 0.0
        self.round_done = asyncio.Event()
        self.finished = False
        self.aborted_by: str | None = None
        self.rematch_votes: set[str] = set()
        self.task: asyncio.Task | None = None

    def opponent_of(self, pid: str) -> Participant:
        return self.players[1] if self.players[0].player_id == pid else self.players[0]

    def participant(self, pid: str) -> Participant | None:
        for p in self.players:
            if p.player_id == pid:
                return p
        return None

    # ── Boucle principale ──────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            await self._run_inner()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("game %s crashed", self.id)
            for p in self.players:
                await p.emit({"type": "error", "message": "Erreur serveur — partie interrompue."})

    async def _run_inner(self) -> None:
        topic = qbank.get_topic(self.topic_id)
        for me in self.players:
            opp = self.opponent_of(me.player_id)
            opp_stats = qbank.level_info(store.get_topic_stats(opp.player_id, self.topic_id)["xp"]) \
                if not opp.is_bot else {"level": 0, "title": "?"}
            await me.emit({
                "type": "match_found",
                "game_id": self.id,
                "topic": {"id": topic["id"], "name": topic["name"],
                          "icon": topic["icon"], "color": topic["color"]},
                "difficulty": self.difficulty,
                "difficulty_label": qbank.DIFFICULTIES[self.difficulty],
                "rounds": ROUNDS,
                "opponent": {"name": opp.name, "is_bot": opp.is_bot,
                             "level": opp_stats["level"]},
            })
        await asyncio.sleep(VS_SCREEN_SECONDS)

        for rnd in range(1, ROUNDS + 1):
            if self.finished:
                return
            await self._play_round(rnd)

        await self._finish()

    async def _play_round(self, rnd: int) -> None:
        self.round_no = rnd
        double = rnd == ROUNDS
        q = self.questions[rnd - 1]

        await self._broadcast({"type": "countdown", "round": rnd, "rounds": ROUNDS,
                               "seconds": COUNTDOWN_SECONDS, "double": double})
        await asyncio.sleep(COUNTDOWN_SECONDS)
        if self.finished:
            return

        self.round_answers = {}
        self.round_done = asyncio.Event()
        self.round_start = time.monotonic()
        await self._broadcast({"type": "question", "round": rnd, "rounds": ROUNDS,
                               "q": q["q"], "choices": q["choices"],
                               "duration": QUESTION_SECONDS, "double": double})

        bot_task = None
        for p in self.players:
            if p.is_bot:
                bot_task = asyncio.create_task(self._bot_answer(p, q, rnd))

        try:
            await asyncio.wait_for(self.round_done.wait(),
                                   timeout=QUESTION_SECONDS + ANSWER_GRACE)
        except TimeoutError:
            pass
        if bot_task:
            bot_task.cancel()
        if self.finished:
            return

        # Révélation : points de chacun sur ce round
        reveal = {"type": "reveal", "round": rnd, "correct": q["answer"], "double": double}
        per_player = {}
        for p in self.players:
            ans = self.round_answers.get(p.player_id)
            if ans is None:
                per_player[p.player_id] = {"choice": None, "points": 0, "time": None}
            else:
                choice, elapsed = ans
                pts = score_for_answer(choice == q["answer"], elapsed, double)
                self.scores[p.player_id] += pts
                per_player[p.player_id] = {"choice": choice, "points": pts,
                                           "time": round(elapsed, 2)}
        for me in self.players:
            opp = self.opponent_of(me.player_id)
            await me.emit({**reveal,
                           "you": per_player[me.player_id],
                           "opp": per_player[opp.player_id],
                           "scores": {"you": self.scores[me.player_id],
                                      "opp": self.scores[opp.player_id]}})
        await asyncio.sleep(REVEAL_SECONDS)

    async def _bot_answer(self, bot: Participant, q: dict, rnd: int) -> None:
        delay = random.uniform(1.5, QUESTION_SECONDS * 0.9)
        if FAST:
            delay = random.uniform(0.05, QUESTION_SECONDS * 0.8)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if random.random() < self.bot_accuracy:
            choice = q["answer"]
        else:
            choice = random.choice([i for i in range(4) if i != q["answer"]])
        self.submit_answer(bot.player_id, rnd, choice)
        # Prévenir l'humain que l'adversaire a répondu
        for p in self.players:
            if not p.is_bot:
                await p.emit({"type": "opponent_answered", "round": rnd})

    # ── Réponses ───────────────────────────────────────────────────────────

    def submit_answer(self, pid: str, rnd: int, choice: int) -> bool:
        """Enregistre une réponse ; renvoie True si prise en compte."""
        if self.finished or rnd != self.round_no or pid in self.round_answers:
            return False
        elapsed = time.monotonic() - self.round_start
        if elapsed > QUESTION_SECONDS + ANSWER_GRACE:
            return False
        if not isinstance(choice, int) or not (0 <= choice <= 3):
            return False
        self.round_answers[pid] = (choice, min(elapsed, QUESTION_SECONDS))
        if len(self.round_answers) == 2:
            self.round_done.set()
        return True

    async def notify_answered(self, pid: str, rnd: int) -> None:
        opp = self.opponent_of(pid)
        await opp.emit({"type": "opponent_answered", "round": rnd})

    # ── Fin de partie ──────────────────────────────────────────────────────

    def _result_for(self, pid: str) -> str:
        me, opp = self.scores[pid], self.scores[self.opponent_of(pid).player_id]
        if self.aborted_by is not None:
            return "loss" if pid == self.aborted_by else "win"
        if me > opp:
            return "win"
        if me < opp:
            return "loss"
        return "draw"

    @staticmethod
    def xp_for(result: str, score: int,
               difficulty: int = qbank.DEFAULT_DIFFICULTY) -> int:
        """XP du match : base + bonus résultat, multipliés par la difficulté."""
        bonus = {"win": 30, "draw": 15, "loss": 5}[result]
        mult = {1: 0.8, 2: 1.0, 3: 1.3, 4: 1.6}[difficulty]
        return round((score / 4 + bonus) * mult)

    async def _finish(self, forfeit_pid: str | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        self.aborted_by = forfeit_pid
        self.round_done.set()

        for me in self.players:
            result = self._result_for(me.player_id)
            score = self.scores[me.player_id]
            payload = {"type": "game_over", "game_id": self.id,
                       "result": result, "forfeit": forfeit_pid is not None,
                       "scores": {"you": score,
                                  "opp": self.scores[self.opponent_of(me.player_id).player_id]},
                       "opponent": self.opponent_of(me.player_id).name}
            if not me.is_bot:
                xp_gain = self.xp_for(result, score, self.difficulty)
                before = qbank.level_info(store.get_topic_stats(me.player_id, self.topic_id)["xp"])
                stats = store.record_game(me.player_id, self.topic_id, xp_gain, result, score)
                after = qbank.level_info(stats["xp"])
                payload.update({"xp_gained": xp_gain, "level_before": before,
                                "level_after": after, "topic_stats": stats,
                                "level_up": after["level"] > before["level"]})
            await me.emit(payload)

    async def forfeit(self, pid: str) -> None:
        """Le joueur ``pid`` quitte/déconnecte : l'adversaire gagne."""
        if not self.finished:
            await self._finish(forfeit_pid=pid)
        if self.task and not self.task.done():
            self.task.cancel()

    async def _broadcast(self, msg: dict) -> None:
        for p in self.players:
            await p.emit(msg)


# ─── Lobby : matchmaking, salons privés, revanche ───────────────────────────

class Lobby:
    def __init__(self) -> None:
        self.games: dict[str, Game] = {}
        # (topic, difficulté) -> file d'attente
        self.queues: dict[tuple[str, int], list[Participant]] = {}
        self.queue_timers: dict[str, asyncio.Task] = {}     # player_id -> fallback bot
        # code -> (topic, difficulté, hôte)
        self.rooms: dict[str, tuple[str, int, Participant]] = {}

    # ── Démarrage de partie ────────────────────────────────────────────────

    async def start_game(self, topic_id: str, p1: Participant, p2: Participant,
                         bot_accuracy: float = 0.7,
                         difficulty: int = qbank.DEFAULT_DIFFICULTY) -> Game:
        # Un joueur ne peut être que dans une partie à la fois : si une
        # partie active traîne (autre onglet, client figé), on la clôt.
        for p in (p1, p2):
            if not p.is_bot:
                stale = self.active_game_of(p.player_id)
                if stale is not None:
                    await stale.forfeit(p.player_id)
        game = Game(topic_id, p1, p2, bot_accuracy=bot_accuracy,
                    difficulty=difficulty)
        self.games[game.id] = game
        game.task = asyncio.create_task(self._run_and_cleanup(game))
        return game

    async def _run_and_cleanup(self, game: Game) -> None:
        try:
            await game.run()
        finally:
            # On garde la partie 5 min pour la revanche, puis on la purge.
            await asyncio.sleep(3 if FAST else 300)
            self.games.pop(game.id, None)

    async def start_bot_game(self, topic_id: str, human: Participant,
                             difficulty: int = qbank.DEFAULT_DIFFICULTY) -> Game:
        level = qbank.level_info(store.get_topic_stats(human.player_id, topic_id)["xp"])["level"]
        bot, accuracy = make_bot(level)
        return await self.start_game(topic_id, human, bot, bot_accuracy=accuracy,
                                     difficulty=difficulty)

    # ── Matchmaking « Partie rapide » ──────────────────────────────────────

    async def find_match(self, topic_id: str, me: Participant,
                         difficulty: int = qbank.DEFAULT_DIFFICULTY) -> None:
        queue = self.queues.setdefault((topic_id, difficulty), [])
        queue[:] = [p for p in queue if p.send is not None]  # purge les morts
        for waiting in queue:
            if waiting.player_id != me.player_id:
                queue.remove(waiting)
                self._cancel_queue_timer(waiting.player_id)
                await self.start_game(topic_id, waiting, me, difficulty=difficulty)
                return
        if me not in queue:
            queue.append(me)
        await me.emit({"type": "queued", "topic_id": topic_id,
                       "difficulty": difficulty})
        self._cancel_queue_timer(me.player_id)
        self.queue_timers[me.player_id] = asyncio.create_task(
            self._bot_fallback(topic_id, me, difficulty))

    async def _bot_fallback(self, topic_id: str, me: Participant,
                            difficulty: int) -> None:
        try:
            await asyncio.sleep(MATCHMAKING_BOT_FALLBACK)
        except asyncio.CancelledError:
            return
        queue = self.queues.get((topic_id, difficulty), [])
        if me in queue:
            queue.remove(me)
            await self.start_bot_game(topic_id, me, difficulty=difficulty)

    def cancel_find(self, me: Participant) -> None:
        for queue in self.queues.values():
            if me in queue:
                queue.remove(me)
        self._cancel_queue_timer(me.player_id)

    def _cancel_queue_timer(self, pid: str) -> None:
        timer = self.queue_timers.pop(pid, None)
        if timer and not timer.done():
            timer.cancel()

    # ── Salons privés « Défier un ami » ────────────────────────────────────

    def create_room(self, topic_id: str, host: Participant,
                    difficulty: int = qbank.DEFAULT_DIFFICULTY) -> str:
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sans caractères ambigus
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        while code in self.rooms:
            code = "".join(secrets.choice(alphabet) for _ in range(4))
        self.rooms[code] = (topic_id, difficulty, host)
        return code

    async def join_room(self, code: str, guest: Participant) -> Game | None:
        entry = self.rooms.pop(code.strip().upper(), None)
        if entry is None:
            return None
        topic_id, difficulty, host = entry
        if host.send is None or host.player_id == guest.player_id:
            return None
        return await self.start_game(topic_id, host, guest, difficulty=difficulty)

    def close_rooms_of(self, pid: str) -> None:
        for code in [c for c, (_, _, h) in self.rooms.items() if h.player_id == pid]:
            self.rooms.pop(code, None)

    # ── Revanche ───────────────────────────────────────────────────────────

    async def request_rematch(self, game_id: str, pid: str) -> None:
        game = self.games.get(game_id)
        if game is None or not game.finished:
            return
        me = game.participant(pid)
        if me is None or self.active_game_of(pid) is not None:
            return
        game.rematch_votes.add(pid)
        opp = game.opponent_of(pid)
        if opp.is_bot:
            game.rematch_votes.add(opp.player_id)
        else:
            await opp.emit({"type": "rematch_offer", "game_id": game_id,
                            "from": me.name})
        if all(p.player_id in game.rematch_votes for p in game.players):
            self.games.pop(game_id, None)
            # Mêmes adversaires (bot compris : même nom, même force),
            # nouvelles questions — comme l'original.
            await self.start_game(game.topic_id, game.players[0], game.players[1],
                                  bot_accuracy=game.bot_accuracy,
                                  difficulty=game.difficulty)

    async def decline_rematch(self, game_id: str, pid: str) -> None:
        game = self.games.get(game_id)
        if game is None:
            return
        opp = game.opponent_of(pid)
        game.rematch_votes.discard(pid)
        await opp.emit({"type": "rematch_declined", "game_id": game_id})

    # ── Déconnexions ───────────────────────────────────────────────────────

    async def handle_disconnect(self, me: Participant) -> None:
        self.cancel_find(me)
        self.close_rooms_of(me.player_id)
        for game in list(self.games.values()):
            if game.participant(me.player_id) and not game.finished:
                opp = game.opponent_of(me.player_id)
                await opp.emit({"type": "opponent_left", "game_id": game.id})
                await game.forfeit(me.player_id)

    def active_game_of(self, pid: str) -> Game | None:
        for game in self.games.values():
            if not game.finished and game.participant(pid):
                return game
        return None
