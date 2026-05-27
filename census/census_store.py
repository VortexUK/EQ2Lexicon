"""Persistent, deploy-surviving store of the last-known character + guild
lookups. The web request path serves from here (via the in-memory cache) and
never blocks on Census; background refreshes merge in fresh data "keep best
known" — a sparse Census response never nulls out good data.

Mirrors parses/db.py: CENSUS_DB_PATH env override, WAL, idempotent _MIGRATIONS.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _db_path() -> Path:
    env = os.getenv("CENSUS_DB_PATH")
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
