"""Spell audit: keep parses with out-of-era spells OFF THE RANKINGS.

A 2026-10 TLE event bug let Ancient-tier spells drop and scribe — tiers no
character on these servers can have legitimately (Master is the era's
ceiling; Grandmaster is a legitimate per-10-levels pick). A character found
with one is flagged, and the rule is kill-level: every parse since the
cutoff the character played in is barred from the rankings and a tamper
report is filed per parse so admins see it in the tamper working set.

THIS NEVER TOUCHES UPLOADS OR VISIBILITY. A barred parse is stored, listed
and viewable like any other; it is only stamped ``ranking_barred_at`` so it
can never be a fight's ranking primary. Nothing here hides, deletes or
refuses a parse.

A character Census cannot show us at all is unverifiable and is flagged the
same way with reason ``census_hidden``; that flag lifts itself the first
time a sweep sees the character clean. An ``out_of_era_spells`` flag is
sticky until an admin clears it.

Independently, every parse started inside the incident window
(SPELL_AUDIT_SINCE .. SPELL_AUDIT_UNTIL) is stamped at ingest and does not
rank while the filter is on.

EVERYTHING IS REVERSIBLE: SPELL_AUDIT_ENABLED=0 lifts every bar at startup
(lift_all_sync) and all parses rank again.

Detection: the sweep pulls the Census spell list of every player in a
winning kill since the cutoff, at most once a day per character, paced so
it never crowds the request path. A Census miss is retried once and only
counts as "hidden" while Census is healthy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from backend.census import failures
from backend.census.models import SpellEntry
from backend.server import census_health
from backend.server.constants import (
    SPELL_AUDIT_DISALLOWED_TIERS,
    SPELL_AUDIT_ENABLED,
    SPELL_AUDIT_SINCE_TS,
    SPELL_AUDIT_UNTIL_TS,
)
from backend.server.core.audit_log import audit_log
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.executor import run_sync
from backend.server.metrics import mark_loop_ok
from backend.server.parses import fights
from backend.server.parses.db import store as parses_db

_log = logging.getLogger(__name__)

#: Default flagged_by.
SOURCE = "spell-audit"
#: Flag reasons.
REASON_SPELLS = "out_of_era_spells"
REASON_HIDDEN = "census_hidden"
REASON_MANUAL = "manual"
#: Tamper-report reason code (the frontend labels it).
TAMPER_REASON = f"server_{REASON_SPELLS}"
#: encounters.ranking_barred_reason values.
INCIDENT_REASON = "spell_exploit_window"
FLAG_BAR_REASON = "flagged_character"

_STARTUP_DELAY_S = 2 * 60
_INTERVAL_S = 6 * 3600
RESCAN_AFTER_S = 24 * 3600
_PACING_S = 1.0
_MISS_RETRY_S = 2.0
_SCAN_BATCH = 2000


def since_ts() -> int:
    return SPELL_AUDIT_SINCE_TS


def in_incident_window(started_at: int) -> bool:
    """True while the exploit embargo covers ``started_at``: from the cutoff
    until SPELL_AUDIT_UNTIL (open-ended while unset)."""
    if not SPELL_AUDIT_ENABLED or started_at < SPELL_AUDIT_SINCE_TS:
        return False
    return SPELL_AUDIT_UNTIL_TS is None or started_at < SPELL_AUDIT_UNTIL_TS


def disallowed_tiers() -> frozenset[str]:
    return frozenset(t.lower() for t in SPELL_AUDIT_DISALLOWED_TIERS)


def offending_spells(entries: list[SpellEntry]) -> list[dict]:
    """The scribed spells whose tier is out of era, as JSON-ready dicts."""
    bad = disallowed_tiers()
    return [
        {"name": e.name, "tier": e.tier, "level": e.level} for e in entries if (e.tier or "").strip().lower() in bad
    ]


def _uploader_discord_id(source_dsn: str | None) -> str:
    dsn = source_dsn or ""
    return dsn.removeprefix("plugin:") if dsn.startswith("plugin:") else ""


# ── Enforcement: stamp + report. Never hides, never deletes. ─────────────────


def _bar_and_report(conn: Any, world: str, e: dict, flagged: list[str], details: Any, now: int) -> None:
    """Stamp one stored parse as never-ranking (kept if already stamped),
    refresh its fight so it stops being the ranking primary, and file the
    tamper report. The parse stays visible."""
    parses_db.bar_encounter_from_rankings(conn, e["id"], reason=FLAG_BAR_REASON, now=now)
    fight_id = fights.fight_id_of(conn, e["id"])
    if fight_id is not None:
        fights.refresh_fight(conn, fight_id)
    payload = {
        "encounter_id": e["id"],
        "act_encid": e["act_encid"],
        "flagged_characters": flagged,
        "details": details,
        "enforced_at": now,
    }
    parses_db.insert_tamper_report(
        conn,
        world=world,
        act_encid=e["act_encid"],
        title=e["title"] or "",
        zone=e["zone"],
        started_at=e["started_at"],
        ended_at=e["ended_at"],
        duration_s=e["duration_s"],
        total_damage=e["total_damage"],
        encdps=e["encdps"],
        reason=TAMPER_REASON,
        reported_at=now,
        uploader_logger_name=e.get("uploaded_by") or "",
        uploader_discord_id=_uploader_discord_id(e.get("source_dsn")),
        uploader_discord_name="",
        guild_name=e.get("guild_name"),
        payload_json=json.dumps(payload, separators=(",", ":")),
    )


def enforce_sync(world: str, name_lower: str, details_json: str | None, *, now: int | None = None) -> dict[str, int]:
    """Bar from the rankings every parse since the cutoff that ``name_lower``
    played in, and file a tamper report per parse. Idempotent: parses that
    already carry the report are skipped. Returns ``{"barred", "reports"}``."""
    now = int(time.time()) if now is None else now
    details = json.loads(details_json) if details_json else None
    n = 0
    conn = parses_db.init_db()
    try:
        for e in parses_db.encounters_with_player_since(conn, world, name_lower, since_ts(), TAMPER_REASON):
            _bar_and_report(conn, world, e, [name_lower], details, now)
            n += 1
        conn.commit()
    finally:
        conn.close()
    return {"barred": n, "reports": n}


def bar_uploaded_parse_sync(world: str, encounter_id: int, flagged: list[str]) -> bool:
    """A just-stored upload with flagged participants: keep it, bar it from
    the rankings, file the tamper report."""
    conn = parses_db.init_db()
    try:
        e = parses_db.get_encounter_for_audit(conn, encounter_id, world)
        if e is None:
            return False
        _bar_and_report(conn, world, e, flagged, None, int(time.time()))
        conn.commit()
        return True
    finally:
        conn.close()


def flag_sync(
    world: str, name: str, *, reason: str, details: dict | list | None, by: str = SOURCE, now: int | None = None
) -> dict[str, Any]:
    """Record (or re-activate) a flag and enforce it."""
    now = int(time.time()) if now is None else now
    name_lower = name.lower()
    details_json = json.dumps(details, separators=(",", ":")) if details is not None else None
    conn = parses_db.init_db()
    try:
        existing = parses_db.get_flagged_character(conn, world, name_lower)
        was_active = existing is not None and existing.get("cleared_at") is None
        parses_db.upsert_flagged_character(
            conn, world=world, name=name, reason=reason, details=details_json, flagged_at=now, flagged_by=by
        )
        conn.commit()
    finally:
        conn.close()
    counters = enforce_sync(world, name_lower, details_json, now=now)
    if not was_active:
        audit_log(
            "character_flagged",
            actor=by,
            world=world,
            character=name,
            reason=reason,
            barred=counters["barred"],
            reports=counters["reports"],
        )
    return {"new": not was_active, **counters}


def clear_sync(world: str, name: str, *, by: str, now: int | None = None) -> dict[str, Any]:
    """Clear a flag. Parses barred only because of a flag (not the incident
    window, whose stamp is permanent) rank again unless another active flag
    still covers them. Returns ``{"cleared": bool, "restored": n}``."""
    now = int(time.time()) if now is None else now
    name_lower = name.lower()
    restored = 0
    conn = parses_db.init_db()
    try:
        cleared = parses_db.clear_flagged_character(conn, world, name_lower, cleared_at=now, cleared_by=by)
        if cleared:
            for e in parses_db.encounters_barred_with_player(conn, world, name_lower, FLAG_BAR_REASON):
                if parses_db.has_other_active_flag(conn, world, e["id"], name_lower):
                    continue
                if parses_db.unbar_encounter(conn, e["id"], FLAG_BAR_REASON):
                    restored += 1
                    if e.get("fight_id") is not None:
                        fights.refresh_fight(conn, e["fight_id"])
        conn.commit()
    finally:
        conn.close()
    if cleared:
        audit_log("character_flag_cleared", actor=by, world=world, character=name, restored=restored)
    return {"cleared": cleared, "restored": restored}


async def _refresh_after_change(world: str) -> None:
    """Ranking primaries moved: rebuild this world's kills dataset."""
    from backend.server.api.rankings import _kills_background_refresh  # noqa: PLC0415 — avoid import cycle

    await _kills_background_refresh(world)


async def flag_character(world: str, name: str, *, reason: str, details: dict | list | None, by: str) -> dict:
    result = await run_sync(flag_sync, world, name, reason=reason, details=details, by=by)
    if result["barred"]:
        asyncio.create_task(_refresh_after_change(world))
    return result


async def clear_character(world: str, name: str, *, by: str) -> dict:
    result = await run_sync(clear_sync, world, name, by=by)
    if result["restored"]:
        asyncio.create_task(_refresh_after_change(world))
    return result


async def flagged_participants(world: str, names: list[str]) -> list[str]:
    """Which of ``names`` carry an active flag on ``world`` (lower-cased)."""
    wanted = sorted({n.strip().lower() for n in names if n and n.strip()})
    if not wanted or not SPELL_AUDIT_ENABLED:
        return []

    def _query() -> list[str]:
        conn = parses_db.init_db()
        try:
            return parses_db.active_flags_among(conn, world, wanted)
        finally:
            conn.close()

    return await run_sync(_query)


def lift_all_sync() -> dict[str, int]:
    """Remove EVERY ranking bar this feature placed (window stamps and flag
    bars) and recompute the affected fights, so all those parses rank again.
    Flags and tamper reports are left as records. Idempotent."""
    reasons = [INCIDENT_REASON, FLAG_BAR_REASON]
    conn = parses_db.init_db()
    try:
        fight_ids = parses_db.fights_with_audit_bars(conn, reasons)
        unbarred = parses_db.unbar_all_for_reasons(conn, reasons)
        for fid in fight_ids:
            fights.refresh_fight(conn, fid)
        conn.commit()
    finally:
        conn.close()
    if unbarred:
        audit_log("spell_audit_lifted", actor=SOURCE, parses=unbarred, fights=len(fight_ids))
    return {"unbarred": unbarred, "fights": len(fight_ids)}


# ── The sweep ────────────────────────────────────────────────────────────────


async def scan_character(client: Any, world: str, name: str) -> list[dict] | None:
    """Census spell list → offending spells. None when Census has no record
    for the character (unverifiable): a miss is retried once, and only
    counts while Census is healthy — a transient failure raises instead."""
    spells = await client.get_character_spells(name, world)
    if spells is None:
        await asyncio.sleep(_MISS_RETRY_S)
        spells = await client.get_character_spells(name, world)
    if spells is None:
        if failures.tripped() or census_health.is_down():
            raise RuntimeError("census unavailable")
        return None
    return offending_spells(spells.entries)


async def _apply_scan(world: str, row: dict, bad: list[dict] | None) -> str:
    active = row.get("active_reason")
    if bad is None:
        if active != REASON_HIDDEN:
            await flag_character(
                world,
                row["name"],
                reason=REASON_HIDDEN,
                details={"note": "Census has no record of this character; spells cannot be verified"},
                by=SOURCE,
            )
        return "hidden"
    if bad:
        await flag_character(world, row["name"], reason=REASON_SPELLS, details={"spells": bad}, by=SOURCE)
        return "flagged"
    if active == REASON_HIDDEN:
        await clear_character(world, row["name"], by=SOURCE)
    return "clean"


async def run_spell_audit(*, force_rescan: bool = False) -> dict[str, Any]:
    """One pass over every world with winning kills since the cutoff."""
    if census_health.is_down():
        return {"scanned": 0, "flagged": 0, "hidden": 0, "errors": 0, "skipped": "census down"}
    now = int(time.time())
    # A forced pass re-checks everything, including rows stamped this very second.
    rescan_before = now + 1 if force_rescan else now - RESCAN_AFTER_S

    def _candidates() -> dict[str, list[dict]]:
        conn = parses_db.init_db()
        try:
            out: dict[str, list[dict]] = {}
            for world in parses_db.worlds_with_winning_kills_since(conn, since_ts()):
                out[world] = parses_db.spell_scan_candidates(conn, world, since_ts(), rescan_before, _SCAN_BATCH)
            return out
        finally:
            conn.close()

    by_world = await run_sync(_candidates)
    scanned = flagged = hidden = errors = 0
    async with shared_census_client() as client:
        for world, rows in by_world.items():
            for row in rows:
                if census_health.is_down():
                    _log.warning("[spell-audit] Census went down mid-sweep — stopping after %d scans", scanned)
                    return {
                        "scanned": scanned,
                        "flagged": flagged,
                        "hidden": hidden,
                        "errors": errors,
                        "skipped": "census down",
                    }
                try:
                    bad = await scan_character(client, world, row["name"])
                except Exception as exc:
                    _log.warning("[spell-audit] scan failed for %s/%s: %s", world, row["name"], exc)
                    errors += 1
                    await run_sync(_record_scan, world, row["name_lower"], now, "error")
                    await asyncio.sleep(_PACING_S)
                    continue
                scanned += 1
                result = await _apply_scan(world, row, bad)
                flagged += result == "flagged"
                hidden += result == "hidden"
                await run_sync(_record_scan, world, row["name_lower"], now, result)
                await asyncio.sleep(_PACING_S)
    return {"scanned": scanned, "flagged": flagged, "hidden": hidden, "errors": errors}


def _record_scan(world: str, name_lower: str, scanned_at: int, result: str) -> None:
    conn = parses_db.init_db()
    try:
        parses_db.record_spell_scan(conn, world, name_lower, scanned_at, result)
        conn.commit()
    finally:
        conn.close()


async def audit_loop() -> None:
    """Lifespan background task (leader-only). With SPELL_AUDIT_ENABLED=0
    it lifts every bar once and exits."""
    if not SPELL_AUDIT_ENABLED:
        try:
            result = await run_sync(lift_all_sync)
            _log.info("[spell-audit] disabled — bars lifted: %s", result)
            if result["unbarred"]:
                from backend.server.api.rankings import prewarm_rankings_kills  # noqa: PLC0415

                await prewarm_rankings_kills()
        except Exception:
            _log.exception("[spell-audit] lift failed")
        return
    await asyncio.sleep(_STARTUP_DELAY_S)
    while True:
        try:
            result = await run_spell_audit()
            if result.get("scanned") or result.get("flagged") or result.get("hidden"):
                _log.info("[spell-audit] %s", result)
        except Exception:
            _log.exception("[spell-audit] sweep failed")
        mark_loop_ok("spell_audit")
        await asyncio.sleep(_INTERVAL_S)
