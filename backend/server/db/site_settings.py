"""users.db `site_settings` domain (async aiosqlite).

Site-wide, admin-editable string knobs — as opposed to the per-server rows
in `servers`. First key: ``discord_invite_url`` (the "Join our Discord
community" link in the footer and on the Support page). Absent row == unset.
"""

from __future__ import annotations

from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

DISCORD_INVITE_URL_KEY = "discord_invite_url"


class SiteSettingsStore(AsyncStoreBase):
    """Schema is owned by the package orchestrator (backend.server.db.init_db);
    methods open per-call connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def get_setting(self, key: str) -> str | None:
        async with self._db() as db:
            async with db.execute(_SQL["select_setting"], (key,)) as cur:
                row = await cur.fetchone()
        return None if row is None else str(row[0])

    async def set_setting(self, key: str, value: str | None, *, updated_by: str) -> None:
        """Write a setting; ``None`` or an empty string removes the row."""
        async with self._db() as db:
            if value is None or not value.strip():
                await db.execute(_SQL["delete_setting"], (key,))
            else:
                await db.execute(_SQL["upsert_setting"], (key, value.strip(), updated_by))
            await db.commit()

    async def all_settings(self) -> dict[str, str]:
        async with self._db() as db:
            async with db.execute(_SQL["select_all"]) as cur:
                return {str(k): str(v) for k, v in await cur.fetchall()}


# The shared default instance — every runtime consumer goes through this.
store = SiteSettingsStore()
