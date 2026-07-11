"""users.db item_watch table helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the item watch domain. Per-call connections open via the
shared ``AsyncStoreBase._db()``; tests re-point ``store.path``.
"""

from __future__ import annotations

from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class ItemWatchStore(AsyncStoreBase):
    """users.db `item_watch` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db); methods open per-call
    connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def add_item_watch(
        self,
        guild_name: str,
        character_name: str,
        item_id: int,
        item_name: str,
        added_by: str,
        added_by_name: str,
        world: str = "Varsoon",
    ) -> dict:
        """
        Add a new item watch entry.
        Raises ValueError on duplicate (same world + guild + character + item_id).
        Returns the new row dict.
        """
        async with self._db(row_factory=True) as db:
            try:
                cur = await db.execute(
                    _SQL["add_watch"],
                    (world, guild_name, character_name, item_id, item_name, added_by, added_by_name),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise ValueError(f"'{item_name}' is already being watched for {character_name}.") from exc
                raise
            new_id = cur.lastrowid
            await db.commit()
            async with db.execute(_SQL["find_by_id"], (new_id,)) as cur2:
                row = await cur2.fetchone()
        assert row is not None, "INSERT succeeded but SELECT returned nothing"
        return dict(row)

    async def list_item_watches(
        self,
        guild_name: str,
        world: str = "Varsoon",
    ) -> list[dict]:
        """Return all item watch entries for a guild on a given server, ordered by added_at descending."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["list_for_guild"], (guild_name, world)) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def remove_item_watch(
        self,
        watch_id: int,
        guild_name: str,
        world: str = "Varsoon",
    ) -> bool:
        """Delete an item watch entry. Scoped to guild_name and world for safety. Returns True if deleted."""
        async with self._db() as db:
            cur = await db.execute(_SQL["remove_watch"], (watch_id, guild_name, world))
            deleted = cur.rowcount > 0
            await db.commit()
        return deleted

    async def update_item_watch_check(
        self,
        watch_id: int,
        seen: bool,
    ) -> None:
        """
        Record the result of an equipment check.
        Updates last_checked_at always.
        Updates last_seen_at (and first_seen_at if not yet set) only when seen=True.
        """
        sql = _SQL["update_seen"] if seen else _SQL["update_unseen"]
        async with self._db() as db:
            await db.execute(sql, (watch_id,))
            await db.commit()


# The shared default instance — every runtime consumer goes through this.
store = ItemWatchStore()
