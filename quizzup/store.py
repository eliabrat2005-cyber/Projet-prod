"""Persistance SQLite : joueurs, XP/stats par thème, classements.

Zéro dépendance externe (sqlite3 stdlib). Un seul fichier de DB à côté
du package (``quizzup/quizzup.db``), surchageable via ``QUIZZUP_DB``.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans caractères ambigus

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
            CREATE TABLE IF NOT EXISTS friendships (
                a_id       TEXT NOT NULL,
                b_id       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (a_id, b_id)
            );
            CREATE TABLE IF NOT EXISTS head_to_head (
                a_id   TEXT NOT NULL,
                b_id   TEXT NOT NULL,
                a_wins INTEGER NOT NULL DEFAULT 0,
                b_wins INTEGER NOT NULL DEFAULT 0,
                draws  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (a_id, b_id)
            );
            """
        )
        # Colonne code ami (migration idempotente sur DB existante)
        cols = {r["name"] for r in _conn.execute("PRAGMA table_info(players)")}
        if "friend_code" not in cols:
            _conn.execute("ALTER TABLE players ADD COLUMN friend_code TEXT")
        _conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_players_friend_code"
            " ON players(friend_code) WHERE friend_code IS NOT NULL"
        )
        _conn.commit()
    return _conn


# ─── Amis ───────────────────────────────────────────────────────────────────

def _canon(a: str, b: str) -> tuple[str, str, bool]:
    """Paire ordonnée (x, y) + True si ``a`` est le premier (x)."""
    return (a, b, True) if a <= b else (b, a, False)


def get_or_create_friend_code(pid: str) -> str | None:
    """Code ami permanent du joueur (6 caractères), généré à la demande."""
    with _lock:
        row = _db().execute("SELECT friend_code FROM players WHERE id = ?", (pid,)).fetchone()
        if row is None:
            return None
        if row["friend_code"]:
            return row["friend_code"]
        for _ in range(20):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
            try:
                _db().execute("UPDATE players SET friend_code = ? WHERE id = ?", (code, pid))
                _db().commit()
                return code
            except sqlite3.IntegrityError:
                continue
    return None


def find_by_friend_code(code: str) -> dict | None:
    code = (code or "").strip().upper()
    if not code:
        return None
    with _lock:
        row = _db().execute(
            "SELECT id, name FROM players WHERE friend_code = ?", (code,)
        ).fetchone()
    return dict(row) if row else None


def are_friends(a: str, b: str) -> bool:
    x, y, _ = _canon(a, b)
    with _lock:
        row = _db().execute(
            "SELECT 1 FROM friendships WHERE a_id = ? AND b_id = ?", (x, y)
        ).fetchone()
    return row is not None


def add_friend_by_code(pid: str, code: str) -> dict | None:
    """Ajoute l'ami identifié par ``code`` (réciproque immédiate). Renvoie
    le dict {id, name} de l'ami, ou None si code inconnu / soi-même."""
    friend = find_by_friend_code(code)
    if friend is None or friend["id"] == pid:
        return None
    x, y, _ = _canon(pid, friend["id"])
    now = datetime.now(UTC).isoformat()
    with _lock:
        _db().execute(
            "INSERT OR IGNORE INTO friendships (a_id, b_id, created_at) VALUES (?, ?, ?)",
            (x, y, now),
        )
        _db().commit()
    return friend


def list_friends(pid: str) -> list[dict]:
    """Amis du joueur avec le bilan tête-à-tête (de son point de vue)."""
    with _lock:
        rows = _db().execute(
            """
            SELECT p.id AS id, p.name AS name,
                   f.a_id AS a_id, f.b_id AS b_id
            FROM friendships f
            JOIN players p ON p.id = CASE WHEN f.a_id = ? THEN f.b_id ELSE f.a_id END
            WHERE f.a_id = ? OR f.b_id = ?
            ORDER BY p.name COLLATE NOCASE
            """,
            (pid, pid, pid),
        ).fetchall()
        out = []
        for r in rows:
            fid = r["id"]
            x, y, pid_is_x = _canon(pid, fid)
            h = _db().execute(
                "SELECT a_wins, b_wins, draws FROM head_to_head WHERE a_id = ? AND b_id = ?",
                (x, y),
            ).fetchone()
            if h:
                my = h["a_wins"] if pid_is_x else h["b_wins"]
                opp = h["b_wins"] if pid_is_x else h["a_wins"]
                draws = h["draws"]
            else:
                my = opp = draws = 0
            out.append({"id": fid, "name": r["name"],
                        "wins": my, "losses": opp, "draws": draws})
    return out


def record_friend_result(a: str, b: str, result_for_a: str) -> None:
    """Incrémente le tête-à-tête si (a, b) sont amis. ``result_for_a`` ∈ win/loss/draw."""
    if not are_friends(a, b):
        return
    x, y, a_is_x = _canon(a, b)
    if result_for_a == "draw":
        col, inc = "draws", 1
    else:
        a_won = result_for_a == "win"
        winner_is_x = a_won if a_is_x else not a_won
        col = "a_wins" if winner_is_x else "b_wins"
        inc = 1
    with _lock:
        _db().execute(
            f"""
            INSERT INTO head_to_head (a_id, b_id, a_wins, b_wins, draws)
            VALUES (?, ?, {1 if col == 'a_wins' else 0}, {1 if col == 'b_wins' else 0},
                    {1 if col == 'draws' else 0})
            ON CONFLICT (a_id, b_id) DO UPDATE SET {col} = {col} + {inc}
            """,
            (x, y),
        )
        _db().commit()


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
