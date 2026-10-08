"""Daily census-existence sweep over guild recruitment listings.

Each recruiting guild is looked up in Census BY ID: renamed → stored name
refreshed; not found → delisted (``recruiting=0``, never deleted); lookup
error → skipped. Disbandment is only inferred from a POSITIVE "Census
answered: not found", so a flake can never delist a live guild.
"""

from __future__ import annotations

import asyncio
import logging

from backend.server import census_health
from backend.server.core.audit_log import audit_log
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.validation import validate_guild_name
from backend.server.db.guild_recruitment import store as recruitment_db
from backend.server.metrics import mark_loop_ok

_log = logging.getLogger(__name__)

_STARTUP_DELAY_S = 15 * 60
_INTERVAL_S = 24 * 3600
_PER_GUILD_PACING_S = 1.0


async def run_recruitment_sweep() -> dict:
    """One full pass; returns counters for the log line."""
    checked = renamed = delisted = 0
    if census_health.is_down():
        return {"checked": 0, "renamed": 0, "delisted": 0, "skipped": "census down"}
    worlds = await recruitment_db.list_listed_worlds()
    if not worlds:
        return {"checked": 0, "renamed": 0, "delisted": 0}
    async with shared_census_client() as client:
        for world in worlds:
            for row in await recruitment_db.list_listed_ids(world):
                census_ok, name = await client.get_guild_name_by_id(row["guild_id"])
                if not census_ok:
                    continue
                checked += 1
                if name is None:
                    if await recruitment_db.delist(world, row["guild_id"]):
                        delisted += 1
                        audit_log(
                            "guild_recruitment_delisted",
                            actor="system",
                            world=world,
                            guild=row["guild_name"],
                            guild_id=row["guild_id"],
                            reason="guild_not_in_census",
                        )
                elif name != row["guild_name"]:
                    # A census name that fails our own shape rules never
                    # enters the table — leave the old name standing.
                    if validate_guild_name(name) and await recruitment_db.set_guild_name(world, row["guild_id"], name):
                        renamed += 1
                        audit_log(
                            "guild_recruitment_renamed",
                            actor="system",
                            world=world,
                            guild_id=row["guild_id"],
                            old_name=row["guild_name"],
                            new_name=name,
                        )
                await asyncio.sleep(_PER_GUILD_PACING_S)
    return {"checked": checked, "renamed": renamed, "delisted": delisted}


async def sweep_loop() -> None:
    """Lifespan background task. CancelledError bubbles out of the sleeps
    for deterministic shutdown — no try/except around them."""
    await asyncio.sleep(_STARTUP_DELAY_S)
    while True:
        try:
            result = await run_recruitment_sweep()
            if result.get("renamed") or result.get("delisted"):
                _log.info("[recruitment-sweep] %s", result)
        except Exception:
            _log.exception("[recruitment-sweep] sweep failed")
        else:
            mark_loop_ok("recruitment_sweep")
        await asyncio.sleep(_INTERVAL_S)
