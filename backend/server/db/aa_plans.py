"""users aa_plans helpers (async psycopg).

Saved AA planner builds — owned by a Discord user, pinned to the character
they were planned from, shareable read-only via the always-minted
``share_slug``. Mirrors the raid_schedule domain: per-call connections via
the shared ``PgStoreBase._db()``; tests re-point ``store.schema``.
"""

from __future__ import annotations

import secrets

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class AAPlansStore(PgStoreBase):
    """Schema DDL is owned by db/migrations/0001_users.sql; methods check
    out pooled connections scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    async def list_plans(self, discord_id: str, world: str, character_name: str) -> list[dict]:
        """The user's plans for one character, newest-updated first (summary
        rows — allocations excluded to keep the list light)."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_plans_for_character"], (discord_id, world, character_name)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def count_plans(self, discord_id: str, world: str, character_name: str) -> int:
        async with self._read() as db:
            async with await db.execute(_SQL["count_plans_for_character"], (discord_id, world, character_name)) as cur:
                row = await cur.fetchone()
                return int(row["n"]) if row else 0

    async def get_plan(self, plan_id: int) -> dict | None:
        async with self._read() as db:
            async with await db.execute(_SQL["select_plan"], (plan_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_plan_by_slug(self, slug: str) -> dict | None:
        async with self._read() as db:
            async with await db.execute(_SQL["select_plan_by_slug"], (slug,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def create_plan(
        self,
        discord_id: str,
        world: str,
        character_name: str,
        name: str,
        xpac: str | None,
        allocations_json: str,
    ) -> dict:
        """Insert a plan (share slug minted here) and return the full row."""
        slug = secrets.token_urlsafe(9)
        async with self._db() as db:
            cur = await db.execute(
                _SQL["insert_plan"],
                (discord_id, world, character_name, name, xpac, allocations_json, slug),
            )
            row = await cur.fetchone()
            await db.commit()
            plan_id = row["id"] if row else 0
        plan = await self.get_plan(int(plan_id or 0))
        if plan is None:  # pragma: no cover — insert+select on one path
            raise RuntimeError("aa_plan insert did not persist")
        return plan

    async def update_plan(
        self,
        plan_id: int,
        discord_id: str,
        *,
        name: str,
        xpac: str | None,
        allocations_json: str,
    ) -> bool:
        """Owner-scoped full update. Returns False when the plan isn't theirs."""
        async with self._db() as db:
            cur = await db.execute(_SQL["update_plan"], (name, allocations_json, xpac, plan_id, discord_id))
            await db.commit()
            return cur.rowcount > 0

    async def delete_plan(self, plan_id: int, discord_id: str) -> bool:
        async with self._db() as db:
            cur = await db.execute(_SQL["delete_plan"], (plan_id, discord_id))
            await db.commit()
            return cur.rowcount > 0


# The shared default instance — every runtime consumer goes through this.
store = AAPlansStore()
