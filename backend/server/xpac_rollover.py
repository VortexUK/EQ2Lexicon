"""Automatic expansion rollover — the countdown banner's other half.

Admins set ``next_xpac`` + ``next_xpac_dt`` on a server row (the frontend
shows the countdown). A small lifespan loop here checks every minute: once
the instant passes, the row flips in place —

  * ``current_xpac``  = next_xpac
  * ``max_level``     = the expansion's level cap (XPAC_MAX_LEVEL; kept as
                        game facts in code — unknown short codes keep the
                        existing cap and log a warning)
  * ``current_xpac_started_dt`` = the scheduled instant (NOT "now": a
                        restart that misses midnight must not shift the
                        rankings era-lock cutoff)
  * ``next_xpac`` / ``next_xpac_dt`` cleared (banner disappears)

then the in-memory server registry reloads so ``current_world()`` consumers
see the new era immediately. The stamped instant is the rankings era-lock
cutoff (see rankings.py): out-of-era raid parses ingested after it stop
entering leaderboards, while uploads, parse pages, attendance and
census-driven guild progression continue untouched.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from backend.server.core.audit_log import audit_log
from backend.server.db.servers import store as servers_db
from backend.server.server_context import load_registry

_log = logging.getLogger(__name__)

POLL_INTERVAL_S = 60

#: Level cap per expansion short code — stable game facts (TLE progression).
XPAC_MAX_LEVEL: dict[str, int] = {
    "Vanilla": 50,  # zones.db's short for the base game
    "Classic": 50,
    "DoF": 60,
    "KoS": 70,
    "EoF": 70,
    "RoK": 80,
    "TSO": 80,
    "SF": 90,
    "DoV": 90,
    "AoD": 90,
    "CoE": 95,
    "ToV": 95,
    "AoM": 100,
    "ToT": 100,
    "KA": 100,
    "PoP": 110,
}


def parse_dt(value: str | None) -> datetime | None:
    """Lenient ISO parse; naive values are treated as UTC. None/garbage -> None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def rollover_due(row: dict, now: datetime) -> bool:
    """Pure: does this server row's countdown read zero?"""
    if not row.get("next_xpac"):
        return False
    when = parse_dt(row.get("next_xpac_dt"))
    return when is not None and when <= now


def _apply_rollovers_sync(now: datetime) -> list[dict]:
    """Flip every due server row; returns the rows that actually flipped."""
    flipped: list[dict] = []
    for row in servers_db.list_servers_sync():
        if not rollover_due(row, now):
            continue
        new_xpac = row["next_xpac"]
        max_level = XPAC_MAX_LEVEL.get(new_xpac)
        if max_level is None:
            _log.warning(
                "[xpac-rollover] no level cap known for %r — keeping %s's cap at %s",
                new_xpac,
                row["world"],
                row["max_level"],
            )
            max_level = row["max_level"]
        if servers_db.apply_xpac_rollover_sync(
            row["world"],
            current_xpac=new_xpac,
            max_level=max_level,
            started_dt=row["next_xpac_dt"],
        ):
            flipped.append({**row, "current_xpac": new_xpac, "max_level": max_level})
    return flipped


async def check_rollovers(now: datetime | None = None) -> list[dict]:
    """One poll step (injectable now for tests). Reloads the registry when
    anything flipped."""
    now = now or datetime.now(tz=UTC)
    flipped = await asyncio.to_thread(_apply_rollovers_sync, now)
    for row in flipped:
        audit_log(
            "xpac_rollover",
            actor="system",
            world=row["world"],
            xpac=row["current_xpac"],
            max_level=row["max_level"],
        )
        _log.info(
            "[xpac-rollover] %s is now on %s (level cap %s) — era-lock cutoff %s",
            row["world"],
            row["current_xpac"],
            row["max_level"],
            row["next_xpac_dt"],
        )
    if flipped:
        await asyncio.to_thread(load_registry)
    return flipped


async def poll_loop() -> None:
    """Lifespan task: check every minute, survive individual failures."""
    while True:
        try:
            await check_rollovers()
        except Exception:
            _log.exception("[xpac-rollover] poll failed")
        await asyncio.sleep(POLL_INTERVAL_S)
