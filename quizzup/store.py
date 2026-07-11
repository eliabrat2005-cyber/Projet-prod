"""Persistance SQLite : joueurs, XP/stats par thème, classements.

Zéro dépendance externe (sqlite3 stdlib). Un seul fichier de DB à côté
du package (``quizzup/quizzup.db``), surchageable via ``QUIZZUP_DB``.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

_DB_PATH = Path(os.environ.get("QUIZZUP_DB", Path(__file__).parent / "quizzup.db"))

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topic_stats (
                player_id  TEXT NOT NULL,
                topic_id   TEXT NOT NULL,
                xp         INTEGER NOT NULL DEFAULT 0,
                games      INTEGER NOT NULL DEFAULT 0,
                wins       INTEGER NOT NULL DEFAULT 0,
                losses     INTEGER NOT NULL DEFAULT 0,
                draws      INTEGER NOT NULL DEFAULT 0,
                best_score INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (player_id, topic_id)
            );
            CREATE TABLE IF NOT EXISTS seen_questions (
                player_id TEXT NOT NULL,
                topic_id  TEXT NOT NULL,
                qhash     TEXT NOT NULL,
                seen_at   TEXT NOT NULL,
                PRIMARY KEY (player_id, topic_id, qhash)
            );
            """
        )
        _conn.commit()
    return _conn


# ─── Joueurs ────────────────────────────────────────────────────────────────

def create_player(name: str) -> dict:
    pid = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    with _lock:
        _db().execute(
            "INSERT INTO players (id, name, created_at) VALUES (?, ?, ?)",
            (pid, name, now),
        )
        _db().commit()
    return {"id": pid, "name": name}


def get_player(pid: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT id, name FROM players WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def rename_player(pid: str, name: str) -> None:
    with _lock:
        _db().execute("UPDATE players SET name = ? WHERE id = ?", (name, pid))
        _db().commit()


# ─── Questions déjà vues (anti-répétition) ──────────────────────────────────

def get_seen_questions(pid: str, topic_id: str) -> set[str]:
    with _lock:
        rows = _db().execute(
            "SELECT qhash FROM seen_questions WHERE player_id = ? AND topic_id = ?",
            (pid, topic_id),
        ).fetchall()
    return {r["qhash"] for r in rows}


def record_seen_questions(pid: str, topic_id: str, hashes: list[str]) -> None:
    now = datetime.now(UTC).isoformat()
    with _lock:
        _db().executemany(
            "INSERT OR REPLACE INTO seen_questions (player_id, topic_id, qhash, seen_at)"
            " VALUES (?, ?, ?, ?)",
            [(pid, topic_id, h, now) for h in hashes],
        )
        _db().commit()


def reset_seen_questions(pid: str, topic_id: str) -> None:
    """Cycle terminé (tout le thème a été vu) : on repart de zéro."""
    with _lock:
        _db().execute(
            "DELETE FROM seen_questions WHERE player_id = ? AND topic_id = ?",
            (pid, topic_id),
        )
        _db().commit()


# ─── Stats par thème ────────────────────────────────────────────────────────

def get_topic_stats(pid: str, topic_id: str) -> dict:
    with _lock:
        row = _db().execute(
            "SELECT xp, games, wins, losses, draws, best_score FROM topic_stats"
            " WHERE player_id = ? AND topic_id = ?",
            (pid, topic_id),
        ).fetchone()
    if row:
        return dict(row)
    return {"xp": 0, "games": 0, "wins": 0, "losses": 0, "draws": 0, "best_score": 0}


def all_topic_stats(pid: str) -> dict[str, dict]:
    with _lock:
        rows = _db().execute(
            "SELECT topic_id, xp, games, wins, losses, draws, best_score"
            " FROM topic_stats WHERE player_id = ?",
            (pid,),
        ).fetchall()
    return {r["topic_id"]: dict(r) for r in rows}


def record_game(pid: str, topic_id: str, xp_gain: int, result: str, score: int) -> dict:
    """Enregistre un match (result ∈ win/loss/draw) et renvoie les stats à jour."""
    win = 1 if result == "win" else 0
    loss = 1 if result == "loss" else 0
    draw = 1 if result == "draw" else 0
    with _lock:
        _db().execute(
            """
            INSERT INTO topic_stats (player_id, topic_id, xp, games, wins, losses, draws, best_score)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT (player_id, topic_id) DO UPDATE SET
                xp = xp + excluded.xp,
                games = games + 1,
                wins = wins + excluded.wins,
                losses = losses + excluded.losses,
                draws = draws + excluded.draws,
                best_score = MAX(best_score, excluded.best_score)
            """,
            (pid, topic_id, xp_gain, win, loss, draw, score),
        )
        _db().commit()
    return get_topic_stats(pid, topic_id)


def leaderboard(topic_id: str, limit: int = 20) -> list[dict]:
    with _lock:
        rows = _db().execute(
            """
            SELECT p.id AS player_id, p.name, s.xp, s.games, s.wins
            FROM topic_stats s JOIN players p ON p.id = s.player_id
            WHERE s.topic_id = ? AND s.games > 0
            ORDER BY s.xp DESC, s.wins DESC
            LIMIT ?
            """,
            (topic_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def global_profile(pid: str) -> dict:
    with _lock:
        row = _db().execute(
            "SELECT COALESCE(SUM(xp),0) AS xp, COALESCE(SUM(games),0) AS games,"
            " COALESCE(SUM(wins),0) AS wins, COALESCE(SUM(losses),0) AS losses,"
            " COALESCE(SUM(draws),0) AS draws"
            " FROM topic_stats WHERE player_id = ?",
            (pid,),
        ).fetchone()
    return dict(row)
