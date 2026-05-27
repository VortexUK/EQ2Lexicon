"""Persistent, deploy-surviving store of the last-known character + guild
lookups. The web request path serves from here (via the in-memory cache) and
never blocks on Census; background refreshes merge in fresh data "keep best
known" — a sparse Census response never nulls out good data.

Mirrors parses/db.py: DB_CENSUS_PATH env override, WAL, idempotent _MIGRATIONS.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


def _db_path() -> Path:
    env = os.getenv("DB_CENSUS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "census" / "census.db"


DB_PATH: Path = _db_path()

_CREATE_CHARACTERS = """
CREATE TABLE IF NOT EXISTS characters (
    name_lower       TEXT    NOT NULL,
    world            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    level            INTEGER,
    guild_name       TEXT,
    data_json        TEXT    NOT NULL,
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);
"""

_CREATE_GUILDS = """
CREATE TABLE IF NOT EXISTS guilds (
    name_lower       TEXT    NOT NULL,
    world            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    data_json        TEXT    NOT NULL,
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);
"""

_MIGRATIONS: list[str] = []  # future schema bumps appended here


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables if missing. Returns an open connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(_CREATE_CHARACTERS)
    conn.execute(_CREATE_GUILDS)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def upsert_character(
    conn: sqlite3.Connection,
    name: str,
    world: str,
    data: dict,
    *,
    resolved: bool,
    now: int | None = None,
) -> None:
    """Merge-store a character. When ``resolved`` is False the call is a no-op
    (keep best-known: never overwrite a good row with a sparse one, and never
    insert a sparse first-sight row). When True, replace the record + stamp
    last_resolved_at."""
    if not resolved:
        return
    ts = int(time.time()) if now is None else now
    conn.execute(
        """
        INSERT INTO characters (name_lower, world, name, level, guild_name, data_json, last_resolved_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name_lower, world) DO UPDATE SET
            name=excluded.name, level=excluded.level, guild_name=excluded.guild_name,
            data_json=excluded.data_json, last_resolved_at=excluded.last_resolved_at,
            updated_at=excluded.updated_at
        """,
        (name.lower(), world, name, data.get("level"), data.get("guild_name"), json.dumps(data), ts, ts),
    )
    conn.commit()


def get_character(conn: sqlite3.Connection, name: str, world: str) -> dict | None:
    """Return {data, last_resolved_at} or None."""
    row = conn.execute(
        "SELECT data_json, last_resolved_at FROM characters WHERE name_lower=? AND world=?",
        (name.lower(), world),
    ).fetchone()
    if row is None:
        return None
    return {"data": json.loads(row[0]), "last_resolved_at": row[1]}


def upsert_guild(conn: sqlite3.Connection, name: str, world: str, data: dict, *, now: int | None = None) -> None:
    """Store the guild roster blob (member names+ranks + info). Always replaces —
    the roster list is reliable from Census regardless of member login recency."""
    ts = int(time.time()) if now is None else now
    conn.execute(
        """
        INSERT INTO guilds (name_lower, world, name, data_json, last_resolved_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name_lower, world) DO UPDATE SET
            name=excluded.name, data_json=excluded.data_json,
            last_resolved_at=excluded.last_resolved_at, updated_at=excluded.updated_at
        """,
        (name.lower(), world, name, json.dumps(data), ts, ts),
    )
    conn.commit()


def get_guild(conn: sqlite3.Connection, name: str, world: str) -> dict | None:
    row = conn.execute(
        "SELECT data_json, last_resolved_at FROM guilds WHERE name_lower=? AND world=?",
        (name.lower(), world),
    ).fetchone()
    if row is None:
        return None
    return {"data": json.loads(row[0]), "last_resolved_at": row[1]}
