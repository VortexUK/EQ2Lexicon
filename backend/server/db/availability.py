"""users.db per-user raid-availability calendar (async aiosqlite).

Only non-default days are stored — an absent row means Available, so a
player who never touches the calendar is always available (per the raid-
planning design). Global per user: a player is AFK on a date regardless of
which character or guild is involved.

Per-call connections via the shared ``AsyncStoreBase._db()``; tests
re-point ``store.path``.
"""

from __future__ import annotations

from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

#: Statuses that get a row. "available" is the implicit default (row deleted).
STORED_STATUSES = ("tentative", "afk")


class AvailabilityStore(AsyncStoreBase):
    """users.db `user_availability` domain. Schema/migrations are owned by
    the package orchestrator (backend.server.db.init_db)."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def get_range(self, discord_id: str, from_day: str, to_day: str) -> dict[str, str]:
        """{YYYY-MM-DD: 'tentative'|'afk'} for the window. Absent = available."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_range"], (discord_id, from_day, to_day)) as cur:
                return {r["day"]: r["status"] for r in await cur.fetchall()}

    async def set_days(self, discord_id: str, days: dict[str, str]) -> None:
        """Bulk-set days. ``available`` deletes the row (back to default);
        ``tentative``/``afk`` upsert. Date-window validation is the route
        layer's job (this persists what it's given)."""
        async with self._db() as db:
            for day, status in days.items():
                if status == "available":
                    await db.execute(_SQL["delete_day"], (discord_id, day))
                elif status in STORED_STATUSES:
                    await db.execute(_SQL["upsert_day"], (discord_id, day, status))
                else:
                    raise ValueError(f"status must be available/tentative/afk, got {status!r}")
            await db.commit()

    async def statuses_for_day(self, day: str) -> dict[str, str]:
        """{discord_id: status} for every user with a non-default entry on
        ``day`` — the raid planner overlays this onto the claims map."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_statuses_for_day"], (day,)) as cur:
                return {r["discord_id"]: r["status"] for r in await cur.fetchall()}


# The shared default instance — every runtime consumer goes through this.
store = AvailabilityStore()
