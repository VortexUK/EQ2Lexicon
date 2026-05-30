"""
Legacy local-only ingest path. Kept as a dev/debug tool.

The PRIMARY ingest path in production is the ACT plugin posting to
`POST /api/parses/ingest` (see `web/routes/parses.py:ingest_parse`).
This module reads ACT's SQLite ODBC export off disk and writes it
into `parses.db` directly — useful when:

  * You want to backfill historical fights from an `act_export.db` you
    already have on disk without going through the upload path.
  * You're debugging a payload-shape question and want to bypass HTTP.
  * The remote site is down and you want local-only continuity.

Driven by the `scripts/parses/*.py` CLIs (`ingest.py`, `list_encounters.py`,
`show_encounter.py`). No web/Discord-bot code calls these functions.

Idempotent — each ACT encid is checked against `ingest_log` before insertion.
The full copy of one encounter runs in a single transaction so an interrupted
ingest leaves no half-written fights behind.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from parses import act_reader
from parses import db as parses_db

_log = logging.getLogger(__name__)


def _default_uploader() -> str:
    """Identifier written to encounters.uploaded_by for each ingest.

    v1 local-only: read from PARSES_UPLOADER env var, falling back to
    'local'. Phase 3 (web upload) will derive this from the authenticated
    Discord user instead.
    """
    return (os.getenv("PARSES_UPLOADER") or "local").strip() or "local"


def _resolve_guild_sync(uploader: str) -> str | None:
    """Look up `uploader`'s current guild via Census. One synchronous wrapper
    around the async CensusClient — used at ingest start so every encounter
    in the run gets the same stamped guild_name.

    Returns None for:
      * the 'local' placeholder (no character to look up)
      * Census lookup failure (network error, character not found)
      * character is unguilded
    The caller treats all three the same — guild_name stays NULL on the row.
    """
    if not uploader or uploader == "local":
        return None

    import asyncio

    from census.client import CensusClient
    from census.config import SERVICE_ID, WORLD

    async def _go() -> str | None:
        client = CensusClient(service_id=SERVICE_ID)
        try:
            return await client.get_character_guild_name(uploader, WORLD)
        finally:
            await client.close()

    try:
        return asyncio.run(_go())
    except Exception as exc:
        _log.warning("Census guild lookup failed for %r: %s", uploader, exc)
        return None


@dataclass(frozen=True)
class IngestStats:
    encounters_new: int = 0
    encounters_skipped: int = 0
    combatants: int = 0
    damage_types: int = 0
    attack_types: int = 0
    errors: int = 0


def ingest_once(
    act_db_path: Path = act_reader.ACT_DB_PATH,
    parses_db_path: Path = parses_db.DB_PATH,
    source_dsn: str = "eq2act",
    uploaded_by: str | None = None,
) -> IngestStats:
    """One pass: copy any new encounters from `act_db_path` into `parses_db_path`."""
    if not act_db_path.exists():
        _log.warning("ACT export DB not present at %s; nothing to ingest.", act_db_path)
        return IngestStats()

    uploader = uploaded_by or _default_uploader()
    # Resolve guild once per ingest run — every encounter gets the same stamp.
    # NULL is fine (uploader='local', Census error, or unguilded).
    guild_name = _resolve_guild_sync(uploader)
    if guild_name:
        _log.info("Uploader %s → guild %s", uploader, guild_name)
    elif uploader != "local":
        _log.info("Uploader %s → no guild (or lookup failed)", uploader)

    new = 0
    skipped = 0
    n_combatants = 0
    n_damage_types = 0
    n_attack_types = 0
    errors = 0

    parses_conn = parses_db.init_db(parses_db_path)
    act_conn = act_reader.open_act_db(act_db_path)
    try:
        encids = act_reader.list_encounter_ids(act_conn)
        for encid in encids:
            if parses_db.is_ingested(parses_conn, encid):
                skipped += 1
                continue

            enc = act_reader.get_encounter(act_conn, encid)
            if enc is None:
                # Half-written or unparseable — skip silently and try again next pass.
                continue
            combatants = act_reader.get_combatants(act_conn, encid)
            if not combatants:
                continue
            damage_types = act_reader.get_damage_types(act_conn, encid)
            attack_types = act_reader.get_attack_types(act_conn, encid)

            ingested_at = int(time.time())
            try:
                with parses_conn:
                    encounter_id = parses_db.insert_encounter(
                        parses_conn,
                        enc,
                        source_dsn=source_dsn,
                        ingested_at=ingested_at,
                        uploaded_by=uploader,
                        guild_name=guild_name,
                    )
                    name_to_id = parses_db.insert_combatants_bulk(
                        parses_conn,
                        encounter_id,
                        combatants,
                    )
                    n_dt = parses_db.insert_damage_types_bulk(
                        parses_conn,
                        name_to_id,
                        damage_types,
                    )
                    n_at = parses_db.insert_attack_types_bulk(
                        parses_conn,
                        name_to_id,
                        attack_types,
                    )
                    parses_db.mark_ingested(
                        parses_conn,
                        encid,
                        encounter_id,
                        source_dsn=source_dsn,
                        ingested_at=ingested_at,
                    )
                new += 1
                n_combatants += len(combatants)
                n_damage_types += n_dt
                n_attack_types += n_at
                _log.info(
                    "Ingested encounter %s (%s, %d combatants).",
                    encid,
                    enc.title,
                    len(combatants),
                )
            except Exception:
                errors += 1
                _log.exception("Failed to ingest encounter %s", encid)
    finally:
        act_conn.close()
        parses_conn.close()

    return IngestStats(
        encounters_new=new,
        encounters_skipped=skipped,
        combatants=n_combatants,
        damage_types=n_damage_types,
        attack_types=n_attack_types,
        errors=errors,
    )


def watch(
    interval_s: float = 5.0,
    act_db_path: Path = act_reader.ACT_DB_PATH,
    parses_db_path: Path = parses_db.DB_PATH,
    source_dsn: str = "eq2act",
    uploaded_by: str | None = None,
) -> None:
    """Poll ACT's DB every `interval_s` seconds. Ctrl-C to stop."""
    _log.info(
        "Watching %s every %.1fs (writing to %s, uploaded_by=%s). Ctrl-C to stop.",
        act_db_path,
        interval_s,
        parses_db_path,
        uploaded_by or _default_uploader(),
    )
    try:
        while True:
            stats = ingest_once(act_db_path, parses_db_path, source_dsn, uploaded_by)
            if stats.encounters_new or stats.errors:
                _log.info("Ingest tick: new=%d errors=%d", stats.encounters_new, stats.errors)
            time.sleep(interval_s)
    except KeyboardInterrupt:
        _log.info("Watcher stopped by user.")


def backfill_guild_names(
    parses_db_path: Path = parses_db.DB_PATH,
) -> int:
    """For each distinct uploaded_by where any encounter has guild_name NULL,
    resolve the guild via Census (one call per uploader, not per encounter)
    and UPDATE all their rows. Skips uploader='local' (no character to look up).

    Returns the total number of encounter rows updated.
    """
    if not parses_db_path.exists():
        _log.warning("Parses DB not present at %s; nothing to backfill.", parses_db_path)
        return 0

    conn = parses_db.init_db(parses_db_path)
    try:
        uploaders = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT uploaded_by FROM encounters "
                "WHERE guild_name IS NULL AND uploaded_by != 'local' AND uploaded_by != ''"
            ).fetchall()
        ]
        if not uploaders:
            _log.info("Backfill: no uploaders need guild resolution.")
            return 0

        total_updated = 0
        for uploader in uploaders:
            guild = _resolve_guild_sync(uploader)
            if guild is None:
                _log.info("Backfill: no guild resolved for %s — leaving NULL.", uploader)
                continue
            with conn:
                cur = conn.execute(
                    "UPDATE encounters SET guild_name = ? WHERE uploaded_by = ? AND guild_name IS NULL",
                    (guild, uploader),
                )
                _log.info("Backfill: %s → %s (%d rows updated)", uploader, guild, cur.rowcount)
                total_updated += cur.rowcount
        return total_updated
    finally:
        conn.close()
