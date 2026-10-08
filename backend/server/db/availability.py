"""users per-user raid-availability calendar (async psycopg).

Only non-default days are stored — an absent row means Available, so a
player who never touches the calendar is always available (per the raid-
planning design). Global per user: a player is AFK on a date regardless of
which character or guild is involved.

Per-call connections via the shared ``PgStoreBase._db()``; tests
re-point ``store.schema``.
"""

from __future__ import annotations

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

#: The two non-default statuses. "available" is the default — but it IS
#: stored once a row has ever been set, carrying its edit stamp, so a
#: newer "available" can override an older tentative/afk from the other
#: side of the planner merge (officer vs player). Readers that surface
#: calendars filter it back out; readers that check for "afk" are
#: unaffected by its presence.
STORED_STATUSES = ("tentative", "afk")


def merge_availability(
    char_times: dict[str, tuple[str, int]],
    user_times: dict[str, tuple[str, int]],
    char_to_user: dict[str, str],
) -> dict[str, str]:
    """Final per-character availability: the NEWER of the officer's
    character entry and the owning player's self-declaration wins, ties
    going to the player. Pure — both inputs come from the *_with_times
    readers; ``char_to_user`` maps character_name_lower → discord id.
    Values may include 'available' (an explicit newest-wins clear) —
    display consumers drop those after merging."""
    merged = dict(char_times)
    for n, uid in char_to_user.items():
        u = user_times.get(uid)
        if u is None:
            continue
        c = merged.get(n)
        if c is None or u[1] >= c[1]:
            merged[n] = u
    return {n: s for n, (s, _) in merged.items()}


class AvailabilityStore(PgStoreBase):
    """Schema DDL is owned by db/migrations/0001_users.sql; methods check
    out pooled connections scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    async def get_range(self, discord_id: str, from_day: str, to_day: str) -> dict[str, str]:
        """{YYYY-MM-DD: 'tentative'|'afk'} for the window. Absent = available."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_range"], (discord_id, from_day, to_day)) as cur:
                return {r["day"]: r["status"] for r in await cur.fetchall()}

    async def set_days(self, discord_id: str, days: dict[str, str]) -> None:
        """Bulk-set days. All three statuses upsert (with an edit stamp) —
        ``available`` is stored rather than deleted so a player's explicit
        "I'm back" beats an older officer AFK in the newest-wins merge.
        ``get_range`` filters it out, so the calendar still renders it as
        the default. Date-window validation is the route layer's job."""
        async with self._db() as db:
            for day, status in days.items():
                if status in ("available", *STORED_STATUSES):
                    await db.execute(_SQL["upsert_day"], (discord_id, day, status))
                else:
                    raise ValueError(f"status must be available/tentative/afk, got {status!r}")
            await db.commit()

    async def statuses_for_day(self, day: str) -> dict[str, str]:
        """{discord_id: status} for every user with a non-default entry on
        ``day`` — the raid planner overlays this onto the claims map."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_statuses_for_day"], (day,)) as cur:
                return {r["discord_id"]: r["status"] for r in await cur.fetchall()}

    async def statuses_for_day_with_times(self, day: str) -> dict[str, tuple[str, int]]:
        """{discord_id: (status, updated_at)} — for the newest-edit-wins
        merge against officer character entries. Rows without a stamp
        carry updated_at 0, so any stamped officer edit beats them."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_statuses_for_day_with_times"], (day,)) as cur:
                return {r["discord_id"]: (r["status"], r["updated_at"]) for r in await cur.fetchall()}

    async def set_character_days(self, world: str, character_name: str, days: dict[str, str], *, set_by: str) -> None:
        """Officer-set per-CHARACTER calendar — for raiders who never use
        the site and so can't declare their own, AND for correcting a stale
        self-declaration (newest edit wins in the planner merge). Unlike
        :meth:`set_days`, ``available`` is STORED — deleting it would
        unmask an older player AFK the officer just cleared."""
        lower = character_name.lower()
        async with self._db() as db:
            for day, status in days.items():
                if status in ("available", *STORED_STATUSES):
                    await db.execute(_SQL["char_upsert_day"], (world, lower, day, status, set_by))
                else:
                    raise ValueError(f"status must be available/tentative/afk, got {status!r}")
            await db.commit()

    async def char_statuses_for_day(self, world: str, day: str) -> dict[str, str]:
        """{character_name_lower: status} for officer-set entries on ``day``
        (``available`` rows included — consumers checking for 'afk' are
        unaffected; the planner merge drops them after precedence)."""
        async with self._read() as db:
            async with await db.execute(_SQL["char_statuses_for_day"], (world, day)) as cur:
                return {r["character_name"]: r["status"] for r in await cur.fetchall()}

    async def char_statuses_for_day_with_times(self, world: str, day: str) -> dict[str, tuple[str, int]]:
        """{character_name_lower: (status, updated_at)} — the officer half
        of the newest-edit-wins planner merge."""
        async with self._read() as db:
            async with await db.execute(_SQL["char_statuses_for_day_with_times"], (world, day)) as cur:
                return {r["character_name"]: (r["status"], r["updated_at"]) for r in await cur.fetchall()}


# The shared default instance — every runtime consumer goes through this.
store = AvailabilityStore()
