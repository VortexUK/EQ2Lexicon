"""users.db discord_guild_links helpers (async aiosqlite).

Maps a Discord server (guild) to an EQ2 (world, guild_name) pair — the
bot's per-Discord-guild context. Configured in Discord via the /lexicon
command group (manage_guild gated); consumed by every world-aware bot
command (backend/bot/guild_context.py) and by the Phase 3 voice-attendance
poller (voice_channel_id = the raid voice channel to snapshot).

Mirrors the downloads domain: per-call connections via
``AsyncStoreBase._db()``; tests re-point ``store.path`` (conftest does it
for every ``ALL_STORES`` entry). The bot imports this store directly — no
facade aliases.
"""

from __future__ import annotations

from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class DiscordLinksStore(AsyncStoreBase):
    """users.db `discord_guild_links` domain. Schema is owned by the package
    orchestrator (backend.server.db.init_db)."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def upsert_link(self, discord_guild_id: str, world: str, guild_name: str, linked_by: str) -> None:
        """Create or update a Discord-guild → EQ2-guild mapping. Relinking
        preserves any configured voice channel (see the SQL comment)."""
        async with self._db() as db:
            await db.execute(_SQL["upsert_link"], (discord_guild_id, world, guild_name, linked_by))
            await db.commit()

    async def set_voice_channel(self, discord_guild_id: str, voice_channel_id: str | None) -> bool:
        """Set (or with None clear) the raid voice channel. Returns False
        when the Discord guild isn't linked yet — callers tell the officer
        to /lexicon link first."""
        async with self._db() as db:
            cur = await db.execute(_SQL["set_voice_channel"], (voice_channel_id, discord_guild_id))
            await db.commit()
            return cur.rowcount > 0

    async def get_link(self, discord_guild_id: str) -> dict | None:
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_link"], (discord_guild_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_link(self, discord_guild_id: str) -> bool:
        async with self._db() as db:
            cur = await db.execute(_SQL["delete_link"], (discord_guild_id,))
            await db.commit()
            return cur.rowcount > 0

    async def list_voice_links(self) -> list[dict]:
        """Every link with voice polling configured — the voice poller's
        per-tick work list."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_voice_links"]) as cur:
                return [dict(r) for r in await cur.fetchall()]


# The shared default instance — every runtime consumer goes through this.
store = DiscordLinksStore()
