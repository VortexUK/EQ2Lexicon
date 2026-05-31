"""Shared models + encounter-resolution helpers used by triggers.py and
spell_timers.py. Kept in _shared.py to avoid circular imports between the two
endpoint modules (each imports from here, not from each other)."""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel

from backend.eq2db import raids as raids_db
from backend.eq2db import zones as zones_db
from backend.server.core.executor import run_sync

# ---------------------------------------------------------------------------
# Models (Pydantic responses)
# ---------------------------------------------------------------------------


class TriggerEntry(BaseModel):
    """Mirrors a row of ``act_triggers``. Field names match XML attribute
    names converted to snake_case so the serialiser is a 1:1 map."""

    id: int
    raid_encounter_id: int
    position: int
    label: str | None = None
    notes: str | None = None
    active: bool
    regex: str
    sound_data: str
    sound_type: int
    category_restrict: bool
    category: str | None = None
    timer: bool
    timer_name: str | None = None
    tabbed: bool
    last_edited_at: int | None = None
    last_edited_by: str | None = None
    created_at: int


class SpellTimerEntry(BaseModel):
    """Mirrors a row of ``act_spell_timers``. ``absolute_`` is named with
    the trailing underscore in the DB to dodge SQLite reserved-keyword
    risk, but the API + XML use the plain ``absolute`` attribute."""

    id: int
    raid_encounter_id: int
    name: str
    checked: bool
    timer_duration_s: int
    only_master_ticks: bool
    restrict: bool
    absolute: bool  # surfaces the DB's `absolute_` column under the XML name
    start_wav: str
    warning_wav: str
    warning_value: int
    radial_display: bool
    modable: bool
    tooltip: str
    fill_color: int
    panel1: bool
    panel2: bool
    remove_value: int
    category: str | None = None
    restrict_category: bool
    last_edited_at: int | None = None
    last_edited_by: str | None = None
    created_at: int


# ---------------------------------------------------------------------------
# Sync helpers — encounter resolution + DB shapes
# ---------------------------------------------------------------------------

_RAIDS_DB_INIT_DONE = False


def _ensure_raids_db_inited() -> None:
    """Call raids_db.init_db() at most once per process.

    raids_db.init_db() is idempotent (CREATE TABLE IF NOT EXISTS), but
    calling it on every trigger read is wasteful. The module-level flag
    short-circuits after the first invocation.
    """
    global _RAIDS_DB_INIT_DONE
    if not _RAIDS_DB_INIT_DONE:
        raids_db.init_db().close()
        _RAIDS_DB_INIT_DONE = True


def _resolve_encounter_sync(zone_name: str, position: int) -> tuple[str, str, int] | None:
    """Map ``(zone_name, position)`` -> ``(canonical_zone, mob_name, encounter_id)``.

    Returns None when the zone or position is unknown. The zone-canonicalisation
    matches `raid_strategies._resolve_curator_encounter` — alias-lookup safe.

    The encounter_id lookup goes against ``raids_db`` (where act_triggers
    actually live), not zones_db. If the raids_db row doesn't exist yet
    (encounter never had a strategy written), we lazy-create it via the
    same upsert_raid_encounter pattern the strategy editor uses, so the
    first trigger save doesn't need a pre-existing strategy."""
    z = zones_db.find_by_name(zone_name)
    if z is None:
        return None
    canonical_zone = z["name"]
    mob_name: str | None = None
    for boss in z.get("bosses", []):
        if int(boss.get("position", -1)) == position:
            mob_name = boss["encounter_name"]
            break
    if mob_name is None:
        return None

    # Find or lazy-create the raids_db rows.
    #
    # _ensure_raids_db_inited() is idempotent (CREATE TABLE IF NOT EXISTS)
    # and is the only thing that ensures the act_triggers / act_spell_timers
    # tables exist on an older raids.db that was seeded before they were
    # added to the schema. Without this, a viewer hitting the GET endpoint
    # against a stale DB sees "no such table: act_triggers" → 500.
    # After the first call per process it is a no-op (module-level flag).
    _ensure_raids_db_inited()

    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        zrow = conn.execute(
            "SELECT id FROM raid_zones WHERE zone_name_lower = ?",
            (canonical_zone.lower(),),
        ).fetchone()
        zone_id: int
        if zrow is None:
            # Lazy-create the raid_zones row (same pattern as the
            # strategy editor's first PUT).
            zone_id = raids_db.upsert_raid_zone(
                conn,
                zone_name=canonical_zone,
                expansion_short=z["expansion_short"],
                source=raids_db.SOURCE_MANUAL,
            )
        else:
            zone_id = zrow["id"]

        erow = conn.execute(
            "SELECT id FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
            (zone_id, mob_name.lower()),
        ).fetchone()
        if erow is None:
            # Lazy-create the encounter row with no strategy. Triggers are
            # the first content for this boss in raids_db — fine, the
            # strategy field stays NULL.
            encounter_id = raids_db.upsert_raid_encounter(
                conn,
                raid_zone_id=zone_id,
                mob_name=mob_name,
                position=position,
                strategy_md=None,
                source=raids_db.SOURCE_MANUAL,
            )
        else:
            encounter_id = erow["id"]

    return canonical_zone, mob_name, encounter_id


async def _resolve_encounter(zone_name: str, position: int) -> tuple[str, str, int]:
    """Async wrapper that 404s on miss. Used by every endpoint here."""
    resolved = await run_sync(_resolve_encounter_sync, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    return resolved


# ---------------------------------------------------------------------------
# DB row → Pydantic model helpers
# ---------------------------------------------------------------------------


def _trigger_row_to_entry(row: dict) -> TriggerEntry:
    """DB row -> Pydantic response. The DB stores booleans as 0/1 INTs."""
    return TriggerEntry(
        id=row["id"],
        raid_encounter_id=row["raid_encounter_id"],
        position=row["position"],
        label=row["label"],
        notes=row["notes"],
        active=bool(row["active"]),
        regex=row["regex"],
        sound_data=row["sound_data"],
        sound_type=row["sound_type"],
        category_restrict=bool(row["category_restrict"]),
        category=row["category"],
        timer=bool(row["timer"]),
        timer_name=row["timer_name"],
        tabbed=bool(row["tabbed"]),
        last_edited_at=row["last_edited_at"],
        last_edited_by=row["last_edited_by"],
        created_at=row["created_at"],
    )


def _spell_row_to_entry(row: dict) -> SpellTimerEntry:
    """DB row -> Pydantic response. ``absolute_`` -> ``absolute`` for the API."""
    return SpellTimerEntry(
        id=row["id"],
        raid_encounter_id=row["raid_encounter_id"],
        name=row["name"],
        checked=bool(row["checked"]),
        timer_duration_s=row["timer_duration_s"],
        only_master_ticks=bool(row["only_master_ticks"]),
        restrict=bool(row["restrict"]),
        absolute=bool(row["absolute_"]),
        start_wav=row["start_wav"],
        warning_wav=row["warning_wav"],
        warning_value=row["warning_value"],
        radial_display=bool(row["radial_display"]),
        modable=bool(row["modable"]),
        tooltip=row["tooltip"],
        fill_color=row["fill_color"],
        panel1=bool(row["panel1"]),
        panel2=bool(row["panel2"]),
        remove_value=row["remove_value"],
        category=row["category"],
        restrict_category=bool(row["restrict_category"]),
        last_edited_at=row["last_edited_at"],
        last_edited_by=row["last_edited_by"],
        created_at=row["created_at"],
    )
