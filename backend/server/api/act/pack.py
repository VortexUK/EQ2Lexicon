"""The EQ2Parser sync surface: the whole curated trigger/timer library as
one structured JSON pack.

  GET /api/act/pack

Era → zone → encounter → triggers + spell timers, including the
enrichment fields (damage_type, control_effect, cooldown_seconds) that
the ACT XML exports deliberately omit. The ``version`` stamp is derived
from the newest edit + row counts, so a client can poll cheaply and
re-sync only when it changes.

Public + read-only: this is reference data, same visibility as the raid
strategy pages themselves.
"""

from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter
from pydantic import BaseModel

from backend.eq2db.raids import _ACT_SPELL_TIMER_COLS, _ACT_TRIGGER_COLS
from backend.eq2db.raids import catalogue as raids_db
from backend.server.api.act._shared import (
    SpellTimerEntry,
    TriggerEntry,
    _ensure_raids_db_inited,
    _spell_row_to_entry,
    _trigger_row_to_entry,
)
from backend.server.core.executor import run_sync

router = APIRouter(tags=["act_triggers"])


class PackEncounter(BaseModel):
    mob: str
    position: int
    triggers: list[TriggerEntry]
    spell_timers: list[SpellTimerEntry]


class PackZone(BaseModel):
    zone: str
    expansion: str
    encounters: list[PackEncounter]


class ActPack(BaseModel):
    version: str
    generated_at: int
    trigger_count: int
    spell_timer_count: int
    zones: list[PackZone]


def _build_pack_sync() -> ActPack:
    _ensure_raids_db_inited()
    with sqlite3.connect(raids_db.path) as conn:
        conn.row_factory = sqlite3.Row
        encounters = conn.execute(
            """
            SELECT e.id AS encounter_id, e.mob_name, e.position,
                   z.zone_name, z.expansion_short
            FROM raid_encounters e
            JOIN raid_zones z ON z.id = e.raid_zone_id
            ORDER BY z.zone_name, e.position, e.mob_name
            """
        ).fetchall()
        triggers = conn.execute(
            f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers ORDER BY raid_encounter_id, position, id"
        ).fetchall()
        timers = conn.execute(
            f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers ORDER BY raid_encounter_id, name"
        ).fetchall()

    by_encounter_triggers: dict[int, list[TriggerEntry]] = {}
    newest = 0
    for row in triggers:
        entry = _trigger_row_to_entry(dict(row))
        by_encounter_triggers.setdefault(entry.raid_encounter_id, []).append(entry)
        newest = max(newest, entry.last_edited_at or 0, entry.created_at)
    by_encounter_timers: dict[int, list[SpellTimerEntry]] = {}
    for row in timers:
        entry = _spell_row_to_entry(dict(row))
        by_encounter_timers.setdefault(entry.raid_encounter_id, []).append(entry)
        newest = max(newest, entry.last_edited_at or 0, entry.created_at)

    zones: dict[str, PackZone] = {}
    for enc in encounters:
        enc_id = enc["encounter_id"]
        enc_triggers = by_encounter_triggers.get(enc_id, [])
        enc_timers = by_encounter_timers.get(enc_id, [])
        # Only encounters that actually carry ACT content — the pack is
        # the trigger library, not the strategy index.
        if not enc_triggers and not enc_timers:
            continue
        zone = zones.setdefault(
            enc["zone_name"],
            PackZone(zone=enc["zone_name"], expansion=enc["expansion_short"] or "", encounters=[]),
        )
        zone.encounters.append(
            PackEncounter(
                mob=enc["mob_name"],
                position=enc["position"],
                triggers=enc_triggers,
                spell_timers=enc_timers,
            )
        )

    trigger_count = len(triggers)
    timer_count = len(timers)
    return ActPack(
        version=f"{newest}.{trigger_count}.{timer_count}",
        generated_at=int(time.time()),
        trigger_count=trigger_count,
        spell_timer_count=timer_count,
        zones=list(zones.values()),
    )


@router.get("/act/pack", response_model=ActPack)
async def get_act_pack() -> ActPack:
    """Everything curated, grouped era/zone/mob, enrichment included."""
    return await run_sync(_build_pack_sync)
