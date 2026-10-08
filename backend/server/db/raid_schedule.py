"""users-schema raid_teams / raid_slots helpers (psycopg).

Officer-editable, publicly-viewable guild raid schedules. A team carries a
``raids`` list of its slots; ``days`` is a real integer[] column (ISO
weekdays) — no CSV round-trip anywhere.
"""

from __future__ import annotations

from typing import Any

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class RaidScheduleStore(PgStoreBase):
    """users-schema `raid_schedule` domain. Schema DDL is owned by
    db/migrations/0001_users.sql; methods check out pooled connections
    scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    @staticmethod
    async def _teams_with_slots(db: Any, teams: list[dict]) -> list[dict]:
        for t in teams:
            async with await db.execute(_SQL["select_slots"], (t["id"],)) as cur:
                t["raids"] = [dict(r) for r in await cur.fetchall()]
        return teams

    async def get_schedule(self, world: str, guild_name: str) -> list[dict]:
        """Return this guild's raid teams (ordered) each with a ``raids`` list."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_teams"], (world, guild_name)) as cur:
                teams = [dict(r) for r in await cur.fetchall()]
            return await RaidScheduleStore._teams_with_slots(db, teams)

    async def replace_schedule(
        self,
        world: str,
        guild_name: str,
        teams: list[dict[str, Any]],
        updated_by: str,
    ) -> None:
        """Transactionally replace a guild's whole schedule.

        Each team dict: ``{name, primary_tz, twitch_login|None, raids: [{days,
        start_min, end_min, label|None}]}``. Bounded sizes (≤4 teams, ≤4 raids) are
        validated by the caller; this just persists. team_index / slot_index are
        assigned from list order.
        """
        async with self._db() as db:
            try:
                # psycopg opens the transaction implicitly on first execute.
                await db.execute(_SQL["delete_slots_for_guild"], (world, guild_name))
                await db.execute(_SQL["delete_teams_for_guild"], (world, guild_name))
                for team_index, team in enumerate(teams):
                    cur = await db.execute(
                        _SQL["insert_team"],
                        (
                            world,
                            guild_name,
                            team_index,
                            team["name"],
                            team["primary_tz"],
                            team.get("twitch_login"),
                            updated_by,
                        ),
                    )
                    _row = await cur.fetchone()
                    team_id = _row["id"] if _row else None
                    for slot_index, raid in enumerate(team.get("raids", [])):
                        await db.execute(
                            _SQL["insert_slot"],
                            (
                                team_id,
                                slot_index,
                                raid["days"],
                                raid["start_min"],
                                raid["end_min"],
                                raid.get("label"),
                            ),
                        )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def list_all_teams_with_twitch(self) -> list[dict]:
        """Every team across all worlds/guilds that has a twitch_login, each with
        its raids. Used by the Twitch-live poller (Part 2)."""
        async with self._read() as db:
            async with await db.execute(_SQL["select_teams_with_twitch"]) as cur:
                teams = [dict(r) for r in await cur.fetchall()]
            return await RaidScheduleStore._teams_with_slots(db, teams)


# The shared default instance — every runtime consumer goes through this.
store = RaidScheduleStore()
