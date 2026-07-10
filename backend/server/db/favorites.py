"""users.db character_favorites helpers (async aiosqlite).

A favourite is a per-user bookmark of a (character_name, world) pair — NOT
ownership; it carries no guild or claim implications. Mirrors the raid_schedule
domain: ``path: Path = DB_PATH`` on every public function so tests can inject a
temp DB. Callers validate + capitalise ``character_name`` before calling in.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


async def add_favorite(discord_id: str, character_name: str, world: str, cap: int, path: Path = DB_PATH) -> bool:
    """Add a favourite, atomically enforcing the per-user-per-world ``cap``.

    Returns False when nothing was inserted — either the favourite already
    existed (idempotent) or the cap was reached. The guard lives in the INSERT
    itself so concurrent requests cannot race a check-then-insert past the cap;
    callers disambiguate the False case via ``is_favorited``."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            _SQL["insert_favorite_capped"],
            (discord_id, character_name, world, discord_id, world, cap),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_favorite(discord_id: str, character_name: str, world: str, path: Path = DB_PATH) -> bool:
    """Remove a favourite. Returns False when there was nothing to remove."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(_SQL["delete_favorite"], (discord_id, character_name, world))
        await db.commit()
        return cur.rowcount > 0


async def count_favorites_for_character(character_name: str, world: str, path: Path = DB_PATH) -> int:
    """How many users favourited this character. Kept separate from the
    membership lookup so the API layer can cache the count and genuinely skip
    this query (and its connection) on a cache hit."""
    async with aiosqlite.connect(path) as db:
        async with db.execute(_SQL["count_for_character"], (character_name, world)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def is_favorited(discord_id: str, character_name: str, world: str, path: Path = DB_PATH) -> bool:
    """Point lookup on the UNIQUE index — has this user favourited this character?"""
    async with aiosqlite.connect(path) as db:
        async with db.execute(_SQL["select_is_favorited"], (discord_id, character_name, world)) as cur:
            return await cur.fetchone() is not None


async def count_user_favorites(discord_id: str, world: str, path: Path = DB_PATH) -> int:
    """How many characters this user has favourited on this world."""
    async with aiosqlite.connect(path) as db:
        async with db.execute(_SQL["count_for_user"], (discord_id, world)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def list_favorites(discord_id: str, world: str, path: Path = DB_PATH) -> list[dict]:
    """The user's favourites on this world, newest first."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(_SQL["select_for_user"], (discord_id, world)) as cur:
            return [dict(r) for r in await cur.fetchall()]
