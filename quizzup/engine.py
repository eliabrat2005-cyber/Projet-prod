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
ALLOWED_ROUNDS = (7, 10, 15, 20)  # longueurs de partie proposées
TOURNAMENT_SIZES = (3, 5, 7)      # nombre de thèmes d'un tournoi
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
                          difficulty: int | None = None,
                          rounds: int = ROUNDS) -> list[dict]:
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
    if len(tier_hashes) >= rounds and len(tier_hashes - exclude) < rounds:
        for p in humans:
            store.reset_seen_questions(p.player_id, topic_id)
        exclude = set()
    questions = qbank.pick_game_questions(topic_id, n=rounds, exclude=exclude,
                                          difficulty=difficulty)
    picked = [q["h"] for q in questions]
    for p in humans:
        store.record_seen_questions(p.player_id, topic_id, picked)
    return questions


def _pick_tournament_questions(topics: list[str], players: list[Participant],
                               difficulty: int | None) -> list[dict]:
    """Une question par thème du tournoi, fraîche pour les joueurs humains."""
    out = []
    for tid in topics:
        out.append(_pick_fresh_questions(tid, players, difficulty, rounds=1)[0])
    return out


class Game:
    def __init__(self, topic_id: str | None, p1: Participant, p2: Participant,
                 bot_accuracy: float = 0.7,
                 difficulty: int = qbank.DEFAULT_DIFFICULTY,
                 rounds: int = ROUNDS,
                 topics: list[str] | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.difficulty = difficulty
        self.players = [p1, p2]
        self.bot_accuracy = bot_accuracy
        self.is_tournament = bool(topics)
        if self.is_tournament:
            # Tournoi : une manche par thème, dernière manche doublée.
            self.tournament_topics = topics
            self.round_topics = [qbank.get_topic(t) for t in topics]
            self.topic_id = "tournoi"           # pseudo-thème pour les stats globales
            self.rounds = len(topics)
            self.questions = _pick_tournament_questions(topics, self.players, difficulty)
        else:
            self.tournament_topics = None
            self.round_topics = None
            self.topic_id = topic_id
            self.rounds = rounds
            self.questions = _pick_fresh_questions(topic_id, self.players, difficulty, rounds)
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

    def _topic_card(self) -> dict:
        if self.is_tournament:
            return {"id": "tournoi", "name": "Tournoi", "icon": "🏆", "color": "#b08d3e"}
        t = qbank.get_topic(self.topic_id)
        return {"id": t["id"], "name": t["name"], "icon": t["icon"], "color": t["color"]}

    async def _run_inner(self) -> None:
        card = self._topic_card()
        tournament = None
        if self.is_tournament:
            tournament = {"count": self.rounds,
                          "topics": [{"icon": t["icon"], "name": t["name"]}
                                     for t in self.round_topics]}
        for me in self.players:
            opp = self.opponent_of(me.player_id)
            opp_stats = qbank.level_info(store.get_topic_stats(opp.player_id, self.topic_id)["xp"]) \
                if not opp.is_bot else {"level": 0, "title": "?"}
            await me.emit({
                "type": "match_found",
                "game_id": self.id,
                "topic": card,
                "tournament": tournament,
                "difficulty": self.difficulty,
                "difficulty_label": qbank.DIFFICULTIES[self.difficulty],
                "rounds": self.rounds,
                "opponent": {"name": opp.name, "is_bot": opp.is_bot,
                             "level": opp_stats["level"]},
            })
        await asyncio.sleep(VS_SCREEN_SECONDS)

        for rnd in range(1, self.rounds + 1):
            if self.finished:
                return
            await self._play_round(rnd)

        await self._finish()

    async def _play_round(self, rnd: int) -> None:
        self.round_no = rnd
        double = rnd == self.rounds
        q = self.questions[rnd - 1]
        # En tournoi, chaque manche affiche le thème du moment.
        round_topic = None
        if self.is_tournament:
            rt = self.round_topics[rnd - 1]
            round_topic = {"name": rt["name"], "icon": rt["icon"], "color": rt["color"]}

        await self._broadcast({"type": "countdown", "round": rnd, "rounds": self.rounds,
                               "seconds": COUNTDOWN_SECONDS, "double": double,
                               "round_topic": round_topic})
        await asyncio.sleep(COUNTDOWN_SECONDS)
        if self.finished:
            return

        self.round_answers = {}
        self.round_done = asyncio.Event()
        self.round_start = time.monotonic()
        await self._broadcast({"type": "question", "round": rnd, "rounds": self.rounds,
                               "q": q["q"], "choices": q["choices"],
                               "duration": QUESTION_SECONDS, "double": double,
                               "round_topic": round_topic})

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

        # Bilan tête-à-tête entre amis (parties humain vs humain uniquement)
        if all(not p.is_bot for p in self.players):
            p0, p1 = self.players
            store.record_friend_result(p0.player_id, p1.player_id,
                                       self._result_for(p0.player_id))

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


MAX_PARTY = 8  # joueurs max dans une partie de groupe


class PartyGame:
    """Partie de groupe (2 à MAX_PARTY joueurs humains) : mêmes questions
    pour tous, scores en direct, classement final. Séparé de ``Game`` (1v1)
    pour ne pas fragiliser le duel."""

    def __init__(self, players: list[Participant], topic_id: str | None,
                 difficulty: int, rounds: int, topics: list[str] | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.players = list(players)
        self.difficulty = difficulty
        self.names = {p.player_id: p.name for p in self.players}
        self.active = {p.player_id for p in self.players}
        self.is_tournament = bool(topics)
        if self.is_tournament:
            self.tournament_topics = topics
            self.round_topics = [qbank.get_topic(t) for t in topics]
            self.topic_id = "tournoi"
            self.rounds = len(topics)
            self.questions = _pick_tournament_questions(topics, self.players, difficulty)
        else:
            self.tournament_topics = None
            self.round_topics = None
            self.topic_id = topic_id
            self.rounds = rounds
            self.questions = _pick_fresh_questions(topic_id, self.players, difficulty, rounds)
        self.scores = {p.player_id: 0 for p in self.players}
        self.round_no = 0
        self.round_answers: dict[str, tuple[int, float]] = {}
        self.round_start = 0.0
        self.round_done = asyncio.Event()
        self.finished = False
        self.task: asyncio.Task | None = None

    def participant(self, pid: str) -> Participant | None:
        for p in self.players:
            if p.player_id == pid:
                return p
        return None

    def _connected(self) -> list[Participant]:
        return [p for p in self.players
                if p.player_id in self.active and p.send is not None]

    async def _broadcast(self, msg: dict) -> None:
        for p in self._connected():
            await p.emit(msg)

    def _topic_card(self) -> dict:
        if self.is_tournament:
            return {"id": "tournoi", "name": "Tournoi", "icon": "🏆", "color": "#b08d3e"}
        t = qbank.get_topic(self.topic_id)
        return {"id": t["id"], "name": t["name"], "icon": t["icon"], "color": t["color"]}

    async def run(self) -> None:
        try:
            await self._run_inner()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("party %s crashed", self.id)
            await self._broadcast({"type": "error", "message": "Erreur serveur — partie interrompue."})

    async def _run_inner(self) -> None:
        card = self._topic_card()
        tournament = None
        if self.is_tournament:
            tournament = {"count": self.rounds,
                          "topics": [{"icon": t["icon"], "name": t["name"]}
                                     for t in self.round_topics]}
        roster = [{"id": p.player_id, "name": p.name} for p in self.players]
        await self._broadcast({"type": "party_started", "game_id": self.id,
                               "players": roster, "topic": card,
                               "tournament": tournament, "rounds": self.rounds,
                               "difficulty": self.difficulty,
                               "difficulty_label": qbank.DIFFICULTIES[self.difficulty]})
        await asyncio.sleep(VS_SCREEN_SECONDS)
        for rnd in range(1, self.rounds + 1):
            if self.finished:
                return
            await self._play_round(rnd)
        await self._finish()

    async def _play_round(self, rnd: int) -> None:
        self.round_no = rnd
        double = rnd == self.rounds
        q = self.questions[rnd - 1]
        round_topic = None
        if self.is_tournament:
            rt = self.round_topics[rnd - 1]
            round_topic = {"name": rt["name"], "icon": rt["icon"], "color": rt["color"]}

        self.round_answers = {}
        self.round_done = asyncio.Event()
        await self._broadcast({"type": "countdown", "round": rnd, "rounds": self.rounds,
                               "seconds": COUNTDOWN_SECONDS, "double": double,
                               "round_topic": round_topic})
        await asyncio.sleep(COUNTDOWN_SECONDS)
        if self.finished:
            return

        self.round_start = time.monotonic()
        await self._broadcast({"type": "question", "round": rnd, "rounds": self.rounds,
                               "q": q["q"], "choices": q["choices"],
                               "duration": QUESTION_SECONDS, "double": double,
                               "round_topic": round_topic})
        try:
            await asyncio.wait_for(self.round_done.wait(),
                                   timeout=QUESTION_SECONDS + ANSWER_GRACE)
        except TimeoutError:
            pass
        if self.finished:
            return

        for pid, (choice, elapsed) in self.round_answers.items():
            self.scores[pid] += score_for_answer(choice == q["answer"], elapsed, double)
        ranking = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
        results = [{"id": pid, "name": self.names[pid], "total": total,
                    "answered": pid in self.round_answers}
                   for pid, total in ranking]
        for me in self._connected():
            ans = self.round_answers.get(me.player_id)
            gained = 0
            if ans is not None:
                gained = score_for_answer(ans[0] == q["answer"], ans[1], double)
            await me.emit({"type": "party_reveal", "round": rnd, "correct": q["answer"],
                           "double": double, "results": results,
                           "you": {"choice": ans[0] if ans else None, "points": gained}})
        await asyncio.sleep(REVEAL_SECONDS)

    def submit_answer(self, pid: str, rnd: int, choice: int) -> bool:
        if self.finished or rnd != self.round_no or pid in self.round_answers:
            return False
        if pid not in self.active:
            return False
        elapsed = time.monotonic() - self.round_start
        if elapsed > QUESTION_SECONDS + ANSWER_GRACE:
            return False
        if not isinstance(choice, int) or not (0 <= choice <= 3):
            return False
        self.round_answers[pid] = (choice, min(elapsed, QUESTION_SECONDS))
        if len(self.round_answers) >= len(self._connected()):
            self.round_done.set()
        return True

    async def remove_player(self, pid: str) -> None:
        self.active.discard(pid)
        if not self.finished:
            await self._broadcast({"type": "party_left", "player_id": pid,
                                   "name": self.names.get(pid, "?")})
            if len(self.active) < 2:
                await self._finish()
            elif len(self.round_answers) >= len(self._connected()) and self._connected():
                self.round_done.set()

    async def _finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.round_done.set()
        ranking = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
        top = ranking[0][1] if ranking else 0
        winners = [pid for pid, s in ranking if s == top]
        podium = [{"id": pid, "name": self.names[pid], "score": s,
                   "winner": s == top and top > 0}
                  for pid, s in ranking]
        for me in self.players:
            if me.send is None:
                continue
            score = self.scores[me.player_id]
            result = ("draw" if len(winners) > 1 else "win") if me.player_id in winners else "loss"
            xp_gain = Game.xp_for(result, score, self.difficulty)
            before = qbank.level_info(store.get_topic_stats(me.player_id, self.topic_id)["xp"])
            stats = store.record_game(me.player_id, self.topic_id, xp_gain, result, score)
            after = qbank.level_info(stats["xp"])
            await me.emit({"type": "party_over", "game_id": self.id,
                           "podium": podium, "your_id": me.player_id,
                           "result": result, "xp_gained": xp_gain,
                           "level_before": before, "level_after": after,
                           "topic_stats": stats, "level_up": after["level"] > before["level"]})

    async def forfeit_all(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()


class Party:
    """Salon d'attente avant une partie de groupe."""

    def __init__(self, code: str, host: Participant, spec: dict):
        self.code = code
        self.host_id = host.player_id
        self.spec = spec               # {kind, topic?/topics?, difficulty, rounds?}
        self.members: list[Participant] = [host]
        self.started = False

    def roster(self) -> list[dict]:
        return [{"id": p.player_id, "name": p.name, "is_host": p.player_id == self.host_id}
                for p in self.members]

    def has(self, pid: str) -> bool:
        return any(p.player_id == pid for p in self.members)


# ─── Lobby : matchmaking, salons privés, revanche ───────────────────────────

class Lobby:
    def __init__(self) -> None:
        self.games: dict[str, Game] = {}
        # (topic, difficulté) -> file d'attente
        self.queues: dict[tuple[str, int], list[Participant]] = {}
        self.queue_timers: dict[str, asyncio.Task] = {}     # player_id -> fallback bot
        # code -> (topic, difficulté, hôte)
        self.rooms: dict[str, tuple[str, int, Participant]] = {}
        # Présence : player_id -> Participant connecté
        self.online: dict[str, Participant] = {}
        # Défis d'ami en attente : from_pid -> {"to","topic","difficulty","name"}
        self.friend_challenges: dict[str, dict] = {}
        # Parties de groupe
        self.parties: dict[str, Party] = {}                 # code -> Party (avant lancement)
        self.party_games: dict[str, PartyGame] = {}         # game_id -> PartyGame en cours

    # ── Démarrage de partie ────────────────────────────────────────────────

    async def start_game(self, topic_id: str | None, p1: Participant, p2: Participant,
                         bot_accuracy: float = 0.7,
                         difficulty: int = qbank.DEFAULT_DIFFICULTY,
                         rounds: int = ROUNDS,
                         topics: list[str] | None = None) -> Game:
        # Un joueur ne peut être que dans une partie à la fois : si une
        # partie active traîne (autre onglet, client figé), on la clôt.
        for p in (p1, p2):
            if not p.is_bot:
                stale = self.active_game_of(p.player_id)
                if stale is not None:
                    await stale.forfeit(p.player_id)
        game = Game(topic_id, p1, p2, bot_accuracy=bot_accuracy,
                    difficulty=difficulty, rounds=rounds, topics=topics)
        self.games[game.id] = game
        game.task = asyncio.create_task(self._run_and_cleanup(game))
        return game

    async def start_bot_tournament(self, human: Participant, topics: list[str],
                                   difficulty: int = qbank.DEFAULT_DIFFICULTY) -> Game:
        # Force du bot : moyenne des niveaux du joueur sur les thèmes du tournoi.
        levels = [qbank.level_info(store.get_topic_stats(human.player_id, t)["xp"])["level"]
                  for t in topics]
        bot, accuracy = make_bot(sum(levels) // max(1, len(levels)))
        return await self.start_game(None, human, bot, bot_accuracy=accuracy,
                                     difficulty=difficulty, topics=topics)

    async def _run_and_cleanup(self, game: Game) -> None:
        try:
            await game.run()
        finally:
            # On garde la partie 5 min pour la revanche, puis on la purge.
            await asyncio.sleep(3 if FAST else 300)
            self.games.pop(game.id, None)

    async def start_bot_game(self, topic_id: str, human: Participant,
                             difficulty: int = qbank.DEFAULT_DIFFICULTY,
                             rounds: int = ROUNDS) -> Game:
        level = qbank.level_info(store.get_topic_stats(human.player_id, topic_id)["xp"])["level"]
        bot, accuracy = make_bot(level)
        return await self.start_game(topic_id, human, bot, bot_accuracy=accuracy,
                                     difficulty=difficulty, rounds=rounds)

    # ── Matchmaking « Partie rapide » ──────────────────────────────────────

    async def find_match(self, topic_id: str, me: Participant,
                         difficulty: int = qbank.DEFAULT_DIFFICULTY,
                         rounds: int = ROUNDS) -> None:
        key = (topic_id, difficulty, rounds)
        queue = self.queues.setdefault(key, [])
        queue[:] = [p for p in queue if p.send is not None]  # purge les morts
        for waiting in queue:
            if waiting.player_id != me.player_id:
                queue.remove(waiting)
                self._cancel_queue_timer(waiting.player_id)
                await self.start_game(topic_id, waiting, me,
                                      difficulty=difficulty, rounds=rounds)
                return
        if me not in queue:
            queue.append(me)
        await me.emit({"type": "queued", "topic_id": topic_id,
                       "difficulty": difficulty, "rounds": rounds})
        self._cancel_queue_timer(me.player_id)
        self.queue_timers[me.player_id] = asyncio.create_task(
            self._bot_fallback(topic_id, me, difficulty, rounds))

    async def _bot_fallback(self, topic_id: str, me: Participant,
                            difficulty: int, rounds: int) -> None:
        try:
            await asyncio.sleep(MATCHMAKING_BOT_FALLBACK)
        except asyncio.CancelledError:
            return
        queue = self.queues.get((topic_id, difficulty, rounds), [])
        if me in queue:
            queue.remove(me)
            await self.start_bot_game(topic_id, me, difficulty=difficulty, rounds=rounds)

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
            # nouvelles questions — comme l'original (tournoi préservé).
            await self.start_game(game.topic_id, game.players[0], game.players[1],
                                  bot_accuracy=game.bot_accuracy,
                                  difficulty=game.difficulty, rounds=game.rounds,
                                  topics=game.tournament_topics)

    async def decline_rematch(self, game_id: str, pid: str) -> None:
        game = self.games.get(game_id)
        if game is None:
            return
        opp = game.opponent_of(pid)
        game.rematch_votes.discard(pid)
        await opp.emit({"type": "rematch_declined", "game_id": game_id})

    # ── Présence & défis d'amis ────────────────────────────────────────────

    async def set_online(self, me: Participant) -> None:
        self.online[me.player_id] = me
        for friend in store.list_friends(me.player_id):
            fp = self.online.get(friend["id"])
            if fp is not None:
                await fp.emit({"type": "friend_presence",
                               "friend_id": me.player_id, "online": True})

    async def set_offline(self, pid: str) -> None:
        self.online.pop(pid, None)
        self.friend_challenges.pop(pid, None)
        # Annule les défis qui visaient ce joueur
        for frm in [f for f, c in self.friend_challenges.items() if c["to"] == pid]:
            self.friend_challenges.pop(frm, None)
        for friend in store.list_friends(pid):
            fp = self.online.get(friend["id"])
            if fp is not None:
                await fp.emit({"type": "friend_presence",
                               "friend_id": pid, "online": False})

    def friend_online_ids(self, pid: str) -> list[str]:
        return [f["id"] for f in store.list_friends(pid) if f["id"] in self.online]

    async def _start_from_spec(self, spec: dict, p1: Participant, p2: Participant) -> None:
        if spec.get("kind") == "tournoi":
            await self.start_game(None, p1, p2, difficulty=spec["difficulty"],
                                  topics=spec["topics"])
        else:
            await self.start_game(spec["topic"], p1, p2,
                                  difficulty=spec["difficulty"], rounds=spec["rounds"])

    async def challenge_friend_spec(self, me: Participant, friend_id: str,
                                    spec: dict, label: str) -> None:
        """``spec`` décrit la partie (solo thème ou tournoi). Défi mutuel
        identique en attente → lancement immédiat ; sinon invitation."""
        other = self.friend_challenges.get(friend_id)
        if other and other["to"] == me.player_id and other["spec"] == spec:
            self.friend_challenges.pop(friend_id, None)
            self.friend_challenges.pop(me.player_id, None)
            target = self.online.get(friend_id)
            if target is not None:
                await self._start_from_spec(spec, target, me)
            return
        self.friend_challenges[me.player_id] = {
            "to": friend_id, "spec": spec, "name": me.name, "label": label,
        }
        await me.emit({"type": "challenge_sent", "friend_id": friend_id})
        target = self.online.get(friend_id)
        if target is not None:
            await target.emit({"type": "challenge_received", "from_id": me.player_id,
                               "from_name": me.name, "label": label})

    async def accept_challenge(self, me: Participant, from_id: str) -> None:
        chal = self.friend_challenges.get(from_id)
        if not chal or chal["to"] != me.player_id:
            await me.emit({"type": "challenge_gone", "from_id": from_id})
            return
        challenger = self.online.get(from_id)
        if challenger is None:
            self.friend_challenges.pop(from_id, None)
            await me.emit({"type": "challenge_gone", "from_id": from_id})
            return
        self.friend_challenges.pop(from_id, None)
        self.friend_challenges.pop(me.player_id, None)
        await self._start_from_spec(chal["spec"], challenger, me)

    async def cancel_challenge(self, me: Participant) -> None:
        chal = self.friend_challenges.pop(me.player_id, None)
        if chal:
            target = self.online.get(chal["to"])
            if target is not None:
                await target.emit({"type": "challenge_cancelled", "from_id": me.player_id})

    async def decline_challenge(self, me: Participant, from_id: str) -> None:
        chal = self.friend_challenges.get(from_id)
        if chal and chal["to"] == me.player_id:
            self.friend_challenges.pop(from_id, None)
            challenger = self.online.get(from_id)
            if challenger is not None:
                await challenger.emit({"type": "challenge_declined",
                                       "friend_id": me.player_id})

    # ── Parties de groupe (2 à MAX_PARTY joueurs) ──────────────────────────

    def _new_party_code(self) -> str:
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        while code in self.parties:
            code = "".join(secrets.choice(alphabet) for _ in range(4))
        return code

    def _party_of(self, pid: str) -> Party | None:
        for party in self.parties.values():
            if party.has(pid):
                return party
        return None

    async def create_party(self, host: Participant, spec: dict) -> None:
        await self.leave_party(host)  # une seule à la fois
        code = self._new_party_code()
        self.parties[code] = Party(code, host, spec)
        await host.emit({"type": "party_created", "code": code,
                         "members": self.parties[code].roster(),
                         "is_host": True, "spec_label": spec.get("label", "")})

    async def join_party(self, guest: Participant, code: str) -> None:
        party = self.parties.get((code or "").strip().upper())
        if party is None or party.started:
            await guest.emit({"type": "party_not_found"})
            return
        if len(party.members) >= MAX_PARTY:
            await guest.emit({"type": "party_error", "message": "Salon complet."})
            return
        await self.leave_party(guest)
        if not party.has(guest.player_id):
            party.members.append(guest)
        for p in party.members:
            await p.emit({"type": "party_update", "code": party.code,
                          "members": party.roster(),
                          "is_host": p.player_id == party.host_id,
                          "spec_label": party.spec.get("label", "")})

    async def leave_party(self, me: Participant) -> None:
        party = self._party_of(me.player_id)
        if party is None:
            return
        party.members = [p for p in party.members if p.player_id != me.player_id]
        if me.player_id == party.host_id or not party.members:
            # L'hôte s'en va → le salon se dissout.
            self.parties.pop(party.code, None)
            for p in party.members:
                await p.emit({"type": "party_closed"})
        else:
            for p in party.members:
                await p.emit({"type": "party_update", "code": party.code,
                              "members": party.roster(),
                              "is_host": p.player_id == party.host_id,
                              "spec_label": party.spec.get("label", "")})

    async def start_party(self, me: Participant) -> None:
        party = self._party_of(me.player_id)
        if party is None or party.host_id != me.player_id or party.started:
            return
        if len(party.members) < 2:
            await me.emit({"type": "party_error", "message": "Il faut au moins 2 joueurs."})
            return
        party.started = True
        self.parties.pop(party.code, None)
        spec = party.spec
        if spec.get("kind") == "tournoi":
            game = PartyGame(party.members, None, spec["difficulty"], 0, topics=spec["topics"])
        else:
            game = PartyGame(party.members, spec["topic"], spec["difficulty"], spec["rounds"])
        self.party_games[game.id] = game
        game.task = asyncio.create_task(self._run_party(game))

    async def _run_party(self, game: PartyGame) -> None:
        try:
            await game.run()
        finally:
            await asyncio.sleep(3 if FAST else 300)
            self.party_games.pop(game.id, None)

    def active_party_game_of(self, pid: str) -> PartyGame | None:
        for g in self.party_games.values():
            if not g.finished and pid in g.active:
                return g
        return None

    # ── Déconnexions ───────────────────────────────────────────────────────

    async def handle_disconnect(self, me: Participant) -> None:
        self.cancel_find(me)
        self.close_rooms_of(me.player_id)
        await self.leave_party(me)
        pg = self.active_party_game_of(me.player_id)
        if pg is not None:
            await pg.remove_player(me.player_id)
        await self.set_offline(me.player_id)
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
