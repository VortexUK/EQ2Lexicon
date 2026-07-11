"""users.db raid-planning helpers (async aiosqlite).

Two concerns, both officer-curated and guild-member-viewable:

  * ``raid_roster_roles`` — which guild characters are raiders / raid alts
    (guild-scoped: a character is on the roster for the guild, then placed
    into any team's layout).
  * ``raid_placements`` — where each rostered character sits in a team's
    4×6 group grid (or the sitout strip). Keyed by ``team_index`` because
    the raid-schedule editor regenerates team rows on save (ids are not
    stable); the schedule PUT prunes placements for removed teams.

Per-call connections via the shared ``AsyncStoreBase._db()``; tests
re-point ``store.path``.
"""

from __future__ import annotations

from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

VALID_ROLES = ("raider", "raid_alt")


class RaidPlanningStore(AsyncStoreBase):
    """users.db `raid_planning` domain. Schema/migrations are owned by the
    package orchestrator (backend.server.db.init_db)."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    # ── Roster roles ─────────────────────────────────────────────────────────

    async def get_roles(self, world: str, guild_name: str) -> list[dict]:
        """All roster designations for a guild: [{character_name, role, ...}]."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_roles"], (world, guild_name)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def set_role(
        self,
        world: str,
        guild_name: str,
        character_name: str,
        role: str | None,
        updated_by: str,
    ) -> None:
        """Set (or with ``role=None`` clear) a character's roster designation.

        Clearing the role also removes the character from every team layout —
        a character that is no longer a raider can't keep occupying a slot.
        """
        if role is not None and role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
        async with self._db() as db:
            if role is None:
                await db.execute(_SQL["delete_role"], (world, guild_name, character_name))
                await db.execute(_SQL["delete_placements_for_character"], (world, guild_name, character_name))
            else:
                await db.execute(
                    _SQL["upsert_role"],
                    (world, guild_name, character_name, role, updated_by),
                )
            await db.commit()

    # ── Team placements ──────────────────────────────────────────────────────

    async def get_placements(self, world: str, guild_name: str, team_index: int) -> list[dict]:
        """One team's layout: [{character_name, group_num, slot, sitout}]."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_placements"], (world, guild_name, team_index)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def replace_placements(
        self,
        world: str,
        guild_name: str,
        team_index: int,
        placements: list[dict],
        updated_by: str,
    ) -> None:
        """Transactionally replace a team's whole layout.

        Each placement dict: ``{character_name, group_num|None, slot|None,
        sitout}``. Structural validation (groups 1-4, slots 0-5, no double
        booking) is the route layer's job; this just persists.
        """
        async with self._db() as db:
            try:
                await db.execute("BEGIN")
                await db.execute(_SQL["delete_placements_for_team"], (world, guild_name, team_index))
                for p in placements:
                    await db.execute(
                        _SQL["insert_placement"],
                        (
                            world,
                            guild_name,
                            team_index,
                            p["character_name"],
                            p.get("group_num"),
                            p.get("slot"),
                            1 if p.get("sitout") else 0,
                            updated_by,
                        ),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def prune_placements_beyond(self, world: str, guild_name: str, team_count: int) -> None:
        """Drop layouts for team indexes that no longer exist after a
        schedule save (teams removed or the list shortened)."""
        async with self._db() as db:
            await db.execute(_SQL["prune_placements_beyond"], (world, guild_name, team_count))
            await db.commit()

    async def roles_for_world(self, world: str) -> dict[str, str]:
        """{character_name_lower: role} across every guild on the world —
        the home-page availability panel checks the viewer's claimed
        characters against this to decide whether to show itself."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_roles_for_world"], (world,)) as cur:
                return {r["name_lower"]: r["role"] for r in await cur.fetchall()}

    # ── Player mapping ───────────────────────────────────────────────────────

    async def claims_map(self, world: str) -> dict[str, str]:
        """{character_name_lower: discord_id} for every approved claim on the
        world — who plays whom, for availability overlay + the duplicate-
        player warning."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_claims_for_world"], (world,)) as cur:
                return {r["name_lower"]: r["discord_id"] for r in await cur.fetchall()}


# The shared default instance — every runtime consumer goes through this.
store = RaidPlanningStore()
