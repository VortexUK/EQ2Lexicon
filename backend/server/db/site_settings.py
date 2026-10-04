"""users-schema `site_settings` domain (psycopg).

Site-wide, admin-editable string knobs — as opposed to the per-server rows
in `servers`. First key: ``discord_invite_url`` (the "Join our Discord
community" link in the footer and on the Support page). Absent row == unset.
"""

from __future__ import annotations

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

DISCORD_INVITE_URL_KEY = "discord_invite_url"


class SiteSettingsStore(PgStoreBase):
    """Schema DDL is owned by db/migrations/0001_users.sql; methods check
    out pooled connections scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    async def get_setting(self, key: str) -> str | None:
        async with self._db() as db:
            async with await db.execute(_SQL["select_setting"], (key,)) as cur:
                row = await cur.fetchone()
        return None if row is None else str(row["value"])

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
            async with await db.execute(_SQL["select_all"]) as cur:
                return {str(r["key"]): str(r["value"]) for r in await cur.fetchall()}


# The shared default instance — every runtime consumer goes through this.
store = SiteSettingsStore()
