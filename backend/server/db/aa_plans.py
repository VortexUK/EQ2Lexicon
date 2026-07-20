"""users.db aa_plans helpers (async aiosqlite).

Saved AA planner builds — owned by a Discord user, pinned to the character
they were planned from, shareable read-only via the always-minted
``share_slug``. Mirrors the raid_schedule domain: per-call connections via
the shared ``AsyncStoreBase._db()``; tests re-point ``store.path``.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class AAPlansStore(AsyncStoreBase):
    """users.db `aa_plans` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db)."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def list_plans(self, discord_id: str, world: str, character_name: str) -> list[dict]:
        """The user's plans for one character, newest-updated first (summary
        rows — allocations excluded to keep the list light)."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_plans_for_character"], (discord_id, world, character_name)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def count_plans(self, discord_id: str, world: str, character_name: str) -> int:
        async with self._db() as db:
            async with db.execute(_SQL["count_plans_for_character"], (discord_id, world, character_name)) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0

    async def get_plan(self, plan_id: int) -> dict | None:
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_plan"], (plan_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_plan_by_slug(self, slug: str) -> dict | None:
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_plan_by_slug"], (slug,)) as cur:
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
            await db.commit()
            plan_id = cur.lastrowid
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
