"""users.db character_favorites helpers (async aiosqlite).

A favourite is a per-user bookmark of a (character_name, world) pair — NOT
ownership; it carries no guild or claim implications. Mirrors the raid_schedule
domain: ``path: Path = DB_PATH`` on every public function so tests can inject a
temp DB. Callers validate + capitalise ``character_name`` before calling in.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class FavoritesStore(AsyncStoreBase):
    """users.db `favorites` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db); methods open per-call
    connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def add_favorite(self, discord_id: str, character_name: str, world: str, cap: int) -> bool:
        """Add a favourite, atomically enforcing the per-user-per-world ``cap``.

        Returns False when nothing was inserted — either the favourite already
        existed (idempotent) or the cap was reached. The guard lives in the INSERT
        itself so concurrent requests cannot race a check-then-insert past the cap;
        callers disambiguate the False case via ``is_favorited``."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["insert_favorite_capped"],
                (discord_id, character_name, world, discord_id, world, cap),
            )
            await db.commit()
            return cur.rowcount > 0

    async def remove_favorite(self, discord_id: str, character_name: str, world: str) -> bool:
        """Remove a favourite. Returns False when there was nothing to remove."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(_SQL["delete_favorite"], (discord_id, character_name, world))
            await db.commit()
            return cur.rowcount > 0

    async def count_favorites_for_character(self, character_name: str, world: str) -> int:
        """How many users favourited this character. Kept separate from the
        membership lookup so the API layer can cache the count and genuinely skip
        this query (and its connection) on a cache hit."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(_SQL["count_for_character"], (character_name, world)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def is_favorited(self, discord_id: str, character_name: str, world: str) -> bool:
        """Point lookup on the UNIQUE index — has this user favourited this character?"""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(_SQL["select_is_favorited"], (discord_id, character_name, world)) as cur:
                return await cur.fetchone() is not None

    async def count_user_favorites(self, discord_id: str, world: str) -> int:
        """How many characters this user has favourited on this world."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(_SQL["count_for_user"], (discord_id, world)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def list_favorites(self, discord_id: str, world: str) -> list[dict]:
        """The user's favourites on this world, newest first."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SQL["select_for_user"], (discord_id, world)) as cur:
                return [dict(r) for r in await cur.fetchall()]


# The shared default instance — every runtime consumer goes through this.
store = FavoritesStore()
