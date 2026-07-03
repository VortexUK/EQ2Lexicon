"""users.db raid_teams / raid_slots helpers (async aiosqlite).

Officer-editable, publicly-viewable guild raid schedules. Mirrors the
item_watch domain: ``path: Path = DB_PATH`` on every public function so tests
can inject a temp DB. A team carries a ``raids`` list of its slots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


async def _teams_with_slots(db: aiosqlite.Connection, teams: list[dict]) -> list[dict]:
    for t in teams:
        async with db.execute(_SQL["select_slots"], (t["id"],)) as cur:
            t["raids"] = [dict(r) for r in await cur.fetchall()]
    return teams


async def get_schedule(world: str, guild_name: str, path: Path = DB_PATH) -> list[dict]:
    """Return this guild's raid teams (ordered) each with a ``raids`` list."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(_SQL["select_teams"], (world, guild_name)) as cur:
            teams = [dict(r) for r in await cur.fetchall()]
        return await _teams_with_slots(db, teams)


async def replace_schedule(
    world: str,
    guild_name: str,
    teams: list[dict[str, Any]],
    updated_by: str,
    path: Path = DB_PATH,
) -> None:
    """Transactionally replace a guild's whole schedule.

    Each team dict: ``{name, primary_tz, twitch_login|None, raids: [{days,
    start_min, end_min, label|None}]}``. Bounded sizes (≤4 teams, ≤4 raids) are
    validated by the caller; this just persists. team_index / slot_index are
    assigned from list order.
    """
    async with aiosqlite.connect(path) as db:
        try:
            await db.execute("BEGIN")
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
                team_id = cur.lastrowid
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


async def list_all_teams_with_twitch(path: Path = DB_PATH) -> list[dict]:
    """Every team across all worlds/guilds that has a twitch_login, each with
    its raids. Used by the Twitch-live poller (Part 2)."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(_SQL["select_teams_with_twitch"]) as cur:
            teams = [dict(r) for r in await cur.fetchall()]
        return await _teams_with_slots(db, teams)
