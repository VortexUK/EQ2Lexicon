"""users ``guild_recruitment`` domain (async psycopg).

Per-guild recruitment profiles: officer-editable, publicly viewable. Rows
are keyed by the CENSUS GUILD ID so a profile (and its logo) survives a
guild rename; ``guild_name`` is a lookup/display column refreshed on every
officer save and by the daily census sweep, which also auto-delists guilds
whose id no longer exists in census. An absent row reads as "not
recruiting" with an empty profile, so callers never merge defaults. The
200x200-max WebP logo lives in a bytea column — transactional with its
attribution columns and inside the same Postgres backups as everything
else.

Audit/erasure notes: ``updated_by`` and ``logo_uploaded_by`` are Discord
ids and are tombstoned by the erasure sweep; the logo blob itself is the
GUILD's asset and stays. ``contacts_json`` holds in-game character names
(public Census game data) — deliberately outside erasure scope.
"""

from __future__ import annotations

import json

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

_EMPTY_PROFILE: dict = {
    "guild_id": None,
    "recruiting": False,
    "description": "",
    "classes": [],
    "tags": [],
    "contacts": [],
    "discord_url": None,
    "updated_by": None,
    "updated_at": None,
    "has_logo": False,
    "logo_uploaded_by": None,
    "logo_uploaded_at": None,
}


def _loads_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


class GuildRecruitmentStore(PgStoreBase):
    """Schema DDL is owned by db/migrations/0001_users.sql; methods check
    out pooled connections scoped to ``self.schema``. The logo bytea is
    only ever read by :meth:`get_logo` — profile/list reads carry a
    ``has_logo`` flag instead. Every write that (re)binds a name to an id
    first purges rows holding that name under a different id (census name
    ownership is unique per world, so such rows are provably stale — a
    disbanded guild whose name was recycled)."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    async def get_profile(self, world: str, guild_name: str) -> dict:
        """The guild's profile with JSON columns decoded; the empty-profile
        defaults when the guild has no row."""
        async with self._db(row_factory=True) as db:
            async with await db.execute(_SQL["select_profile"], (world, guild_name)) as cur:
                row = await cur.fetchone()
        if row is None:
            return dict(_EMPTY_PROFILE)
        return {
            "guild_id": row["guild_id"],
            "recruiting": bool(row["recruiting"]),
            "description": row["description"] or "",
            "classes": _loads_list(row["classes_json"]),
            "tags": _loads_list(row["tags_json"]),
            "contacts": _loads_list(row["contacts_json"]),
            "discord_url": row["discord_url"],
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
            "has_logo": bool(row["has_logo"]),
            "logo_uploaded_by": row["logo_uploaded_by"],
            "logo_uploaded_at": row["logo_uploaded_at"],
        }

    async def upsert_profile(
        self,
        world: str,
        guild_id: int,
        guild_name: str,
        *,
        recruiting: bool,
        description: str,
        classes: list[str],
        tags: list[str],
        contacts: list[str],
        discord_url: str | None,
        updated_by: str,
    ) -> dict:
        """Write the PROFILE columns (never the logo) and return the stored
        state. A renamed guild's save lands on its existing id-keyed row and
        refreshes ``guild_name``."""
        async with self._db() as db:
            await db.execute(_SQL["purge_name_conflicts"], (world, guild_name, guild_id))
            await db.execute(
                _SQL["upsert_profile"],
                (
                    world,
                    guild_id,
                    guild_name,
                    1 if recruiting else 0,
                    description,
                    json.dumps(classes),
                    json.dumps(tags),
                    json.dumps(contacts),
                    discord_url,
                    updated_by,
                ),
            )
            await db.commit()
        return await self.get_profile(world, guild_name)

    async def get_logo(self, world: str, guild_name: str) -> dict | None:
        """``{logo, logo_media_type, logo_uploaded_at}`` or ``None`` when the
        guild has no row or no logo."""
        async with self._db(row_factory=True) as db:
            async with await db.execute(_SQL["select_logo"], (world, guild_name)) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            # psycopg returns bytea as memoryview — materialise to bytes at
            # the store boundary so Response(content=...) keeps working.
            "logo": bytes(row["logo"]),
            "logo_media_type": row["logo_media_type"],
            "logo_uploaded_at": row["logo_uploaded_at"],
        }

    async def set_logo(
        self, world: str, guild_id: int, guild_name: str, *, logo: bytes, media_type: str, uploaded_by: str
    ) -> int:
        """Store the re-encoded logo (LOGO columns only — a guild with no
        profile row gets one at the schema defaults). Returns the stored
        ``logo_uploaded_at``."""
        async with self._db() as db:
            await db.execute(_SQL["purge_name_conflicts"], (world, guild_name, guild_id))
            await db.execute(_SQL["upsert_logo"], (world, guild_id, guild_name, logo, media_type, uploaded_by))
            await db.commit()
        stored = await self.get_logo(world, guild_name)
        return int(stored["logo_uploaded_at"]) if stored else 0

    async def clear_logo(self, world: str, guild_name: str) -> bool:
        """Remove the logo; ``True`` when one existed (so no-op deletes skip
        the audit line)."""
        async with self._db() as db:
            cur = await db.execute(_SQL["clear_logo"], (world, guild_name))
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def list_recruiting(self, world: str) -> list[dict]:
        """Every recruiting guild in the world (newest edit first), JSON
        decoded, no blobs."""
        async with self._db(row_factory=True) as db:
            async with await db.execute(_SQL["select_recruiting"], (world,)) as cur:
                rows = await cur.fetchall()
        return [
            {
                "guild_id": row["guild_id"],
                "guild_name": row["guild_name"],
                "description": row["description"] or "",
                "classes": _loads_list(row["classes_json"]),
                "tags": _loads_list(row["tags_json"]),
                "contacts": _loads_list(row["contacts_json"]),
                "discord_url": row["discord_url"],
                "updated_at": row["updated_at"],
                "has_logo": bool(row["has_logo"]),
                "logo_uploaded_at": row["logo_uploaded_at"],
            }
            for row in rows
        ]

    # -- daily census sweep support -------------------------------------

    async def list_listed_worlds(self) -> list[str]:
        """Every world with at least one recruiting=1 row."""
        async with self._db() as db:
            async with await db.execute(_SQL["select_listed_worlds"]) as cur:
                rows = await cur.fetchall()
        return [row["world"] for row in rows]

    async def list_listed_ids(self, world: str) -> list[dict]:
        """``[{guild_id, guild_name}]`` for every row with recruiting=1 —
        the sweep's work list."""
        async with self._db(row_factory=True) as db:
            async with await db.execute(_SQL["select_listed_ids"], (world,)) as cur:
                rows = await cur.fetchall()
        return [{"guild_id": row["guild_id"], "guild_name": row["guild_name"]} for row in rows]

    async def set_guild_name(self, world: str, guild_id: int, guild_name: str) -> bool:
        """Sweep rename-follow: re-point the row at the guild's CURRENT
        census name. ``True`` when the name actually changed."""
        async with self._db() as db:
            await db.execute(_SQL["purge_name_conflicts"], (world, guild_name, guild_id))
            cur = await db.execute(_SQL["update_guild_name"], (guild_name, world, guild_id, guild_name))
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def delist(self, world: str, guild_id: int) -> bool:
        """Sweep auto-delist for a guild whose census id no longer exists.
        Sets recruiting=0 (never deletes — a census flake must not destroy a
        profile + logo); ``True`` when the row was actually delisted."""
        async with self._db() as db:
            cur = await db.execute(_SQL["delist_guild"], (world, guild_id))
            await db.commit()
            return (cur.rowcount or 0) > 0


# The shared default instance — every runtime consumer goes through this.
store = GuildRecruitmentStore()
