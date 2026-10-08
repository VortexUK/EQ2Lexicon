"""users download_events helpers (async psycopg).

Records which signed-in users clicked each direct-download link on the
Downloads page (parser installer / portable zip / ACT plugin dll). The whole
app is behind the login gate, so every click carries a session.
``UNIQUE(discord_id, slug)`` keeps it one row per user per asset, so the public
count is distinct-downloaders — one person re-clicking never inflates it.

Mirrors the favorites domain: per-call connections via ``PgStoreBase._db()``;
tests re-point ``store.schema`` (conftest does it for every ``ALL_STORES`` entry).
"""

from __future__ import annotations

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class DownloadsStore(PgStoreBase):
    """Schema DDL is owned by db/migrations/0001_users.sql; methods check
    out pooled connections scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    async def record_download(self, discord_id: str, slug: str) -> bool:
        """Record that a user clicked a download. Idempotent per (user, slug):
        returns False when this user already recorded this slug."""
        async with self._db() as db:
            cur = await db.execute(_SQL["insert_download"], (discord_id, slug))
            await db.commit()
            return cur.rowcount > 0

    async def count_for_slug(self, slug: str) -> int:
        """Distinct downloaders for one slug."""
        async with self._read() as db:
            async with await db.execute(_SQL["count_for_slug"], (slug,)) as cur:
                row = await cur.fetchone()
                return row["n"] if row else 0

    async def counts(self) -> dict[str, int]:
        """Distinct-downloader count for every slug that has at least one row."""
        async with self._read() as db:
            async with await db.execute(_SQL["count_all"]) as cur:
                return {row["slug"]: row["n"] for row in await cur.fetchall()}


# The shared default instance — every runtime consumer goes through this.
store = DownloadsStore()
