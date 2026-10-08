"""users `guild_settings` domain (async psycopg).

Per-guild feature switches that only the guild LEADER (Census rank_id 0)
or a site admin may change. One boolean column per setting: the DB default
makes "no row" and "default" identical, so readers never merge defaults
themselves, and a later setting is the well-worn ADD COLUMN path.

``officers_can_delete_parses`` (default ON): a leader can turn it off so
that only the leader, admins and each parse's own uploader may delete.
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

DEFAULT_GUILD_SETTINGS: dict[str, bool] = {"officers_can_delete_parses": True}


class GuildSettingsStore(PgStoreBase):
    """Schema DDL is owned by db/migrations/0001_users.sql; methods check
    out pooled connections scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    async def get_settings(self, world: str, guild_name: str) -> dict:
        """``{officers_can_delete_parses, updated_by, updated_at}`` — the
        defaults (and ``None`` audit fields) when the guild has no row."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_settings"], (world, guild_name)) as cur:
                row = await cur.fetchone()
        if row is None:
            return {**DEFAULT_GUILD_SETTINGS, "updated_by": None, "updated_at": None}
        return {
            "officers_can_delete_parses": bool(row["officers_can_delete_parses"]),
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }

    async def upsert_settings(
        self, world: str, guild_name: str, *, officers_can_delete_parses: bool, updated_by: str
    ) -> dict:
        """Write the guild's switches and return the stored state."""
        async with self._db() as db:
            await db.execute(
                _SQL["upsert_settings"],
                (world, guild_name, 1 if officers_can_delete_parses else 0, updated_by),
            )
            await db.commit()
        return await self.get_settings(world, guild_name)

    async def officers_can_delete_parses(self, world: str, guild_names: Iterable[str]) -> dict[str, bool]:
        """One IN-query for the hot paths (the /parses permission pass and
        the delete routes): ``{guild_name: flag}`` for every name asked,
        absent guilds ``True``. Empty input returns ``{}`` without opening a
        connection."""
        names = sorted({g for g in guild_names if g})
        if not names:
            return {}
        flags = dict.fromkeys(names, True)
        async with self._read() as db:
            async with await db.execute(_SQL["select_delete_flags"], (world, names)) as cur:
                for row in await cur.fetchall():
                    flags[row["guild_name"]] = bool(row["officers_can_delete_parses"])
        return flags


# The shared default instance — every runtime consumer goes through this.
store = GuildSettingsStore()
