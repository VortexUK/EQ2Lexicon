"""ACT trigger endpoints.

Surface:
  GET    /api/zones/{zone}/encounters/{position}/triggers              (list, public)
  POST   /api/zones/{zone}/encounters/{position}/triggers              (create, editor)
  GET    /api/zones/{zone}/encounters/{position}/triggers/{id}         (single, public)
  PUT    /api/zones/{zone}/encounters/{position}/triggers/{id}         (update, editor)
  DELETE /api/zones/{zone}/encounters/{position}/triggers/{id}         (delete, editor)
  GET    /api/zones/{zone}/encounters/{position}/triggers/{id}/export.xml  (single XML)
  GET    /api/zones/{zone}/encounters/{position}/triggers/export.xml       (all XML)
  POST   /api/zones/{zone}/encounters/{position}/triggers/import-xml   (paste import)
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend.eq2db import raids as raids_db
from backend.server.api.act._shared import (
    SpellTimerEntry,
    TriggerEntry,
    _resolve_encounter,
    _spell_row_to_entry,
    _trigger_row_to_entry,
)
from backend.server.api.act.xml_export import (
    build_xml,
    safe_filename,
    spell_timers_referenced_by,
)
from backend.server.api.act.xml_import import parse_import_xml
from backend.server.auth_deps import require_editor
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser

router = APIRouter(tags=["act_triggers"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TriggerUpsertRequest(BaseModel):
    """Body for create + update. ``regex`` is required; everything else
    defaults to ACT's "minimal trigger" shape."""

    regex: str = Field(..., min_length=1)
    label: str | None = None
    notes: str | None = None
    position: int = 0
    active: bool = True
    sound_data: str = ""
    sound_type: int = Field(3, ge=0, le=3)
    category_restrict: bool = False
    category: str | None = None
    timer: bool = False
    timer_name: str | None = None
    tabbed: bool = False


class ImportXmlRequest(BaseModel):
    """Wrap the XML in a JSON body so the call site is plain JSON like the
    rest of the API. The ACT plugin's "Share Edit Triggers" right-click
    yields a single element; nothing stops the user from pasting a fuller
    chunk if they want to batch-import."""

    xml: str = Field(..., min_length=1)


class ImportXmlResponse(BaseModel):
    triggers_added: int
    triggers_skipped_existing: int
    spell_timers_added: int
    triggers: list[TriggerEntry]
    spell_timers: list[SpellTimerEntry]


# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------


def _trigger_already_exists_sync(encounter_id: int, regex: str, sound_data: str) -> int | None:
    """Idempotency: same (encounter, regex, sound) is treated as identity.
    Returns the existing row id, or None."""
    if not raids_db.DB_PATH.exists():
        return None
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM act_triggers WHERE raid_encounter_id = ? AND regex = ? AND sound_data = ?",
            (encounter_id, regex, sound_data),
        ).fetchone()
    return int(row[0]) if row else None


def _spell_timer_id_for_name_sync(encounter_id: int, name: str) -> int | None:
    """Find an existing spell timer's id by lowercase name within the
    encounter — UNIQUE (encounter_id, name_lower) makes this safe."""
    if not raids_db.DB_PATH.exists():
        return None
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM act_spell_timers WHERE raid_encounter_id = ? AND name_lower = ?",
            (encounter_id, name.lower()),
        ).fetchone()
    return int(row[0]) if row else None


# ---------------------------------------------------------------------------
# Trigger endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/zones/{zone_name}/encounters/{position}/triggers",
    response_model=list[TriggerEntry],
)
async def list_triggers(zone_name: str, position: int) -> list[TriggerEntry]:
    """All triggers for an encounter, ordered by curator position then id."""
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    rows = await run_sync(raids_db.list_act_triggers_for_encounter, encounter_id)
    return [_trigger_row_to_entry(r) for r in rows]


@router.get(
    "/zones/{zone_name}/encounters/{position}/triggers/export.xml",
    response_class=Response,
)
async def export_all_triggers(zone_name: str, position: int) -> Response:
    """Bundle every trigger + every spell timer for this encounter into a
    single ACT-importable file. Both trigger-referenced and standalone
    spell timers are included so ACT picks up timers that fire off a
    skill/combat-art via native name-matching."""
    canonical_zone, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    triggers = await run_sync(raids_db.list_act_triggers_for_encounter, encounter_id)
    spells = await run_sync(raids_db.list_act_spell_timers_for_encounter, encounter_id)
    # Emit EVERY spell timer for this encounter — both the ones a trigger
    # references and standalone ones (which fire off ACT's native skill/CA
    # name-match). The table's UNIQUE(encounter, name_lower) keeps <Spell>
    # rows unique without extra dedup.
    xml = build_xml(triggers, spells)
    filename = safe_filename(f"{canonical_zone}-{mob_name}-triggers.xml")
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/zones/{zone_name}/encounters/{position}/triggers/{trigger_id}/export.xml",
    response_class=Response,
)
async def export_trigger(zone_name: str, position: int, trigger_id: int) -> Response:
    """A single trigger plus, when ``timer=true``, the one spell timer it
    references. Useful for sharing just one mechanic without dragging the
    whole boss's trigger pack."""
    canonical_zone, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    trigger = await run_sync(raids_db.get_act_trigger, trigger_id)
    if trigger is None or trigger["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Trigger not found")

    spells: list[dict] = []
    if trigger.get("timer") and trigger.get("timer_name"):
        all_spells = await run_sync(raids_db.list_act_spell_timers_for_encounter, encounter_id)
        spells = spell_timers_referenced_by([trigger], all_spells)

    xml = build_xml([trigger], spells)
    label = trigger.get("label") or trigger.get("sound_data") or "trigger"
    filename = safe_filename(f"{canonical_zone}-{mob_name}-{label}.xml")
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/zones/{zone_name}/encounters/{position}/triggers/{trigger_id}",
    response_model=TriggerEntry,
)
async def get_trigger(zone_name: str, position: int, trigger_id: int) -> TriggerEntry:
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    trigger = await run_sync(raids_db.get_act_trigger, trigger_id)
    if trigger is None or trigger["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return _trigger_row_to_entry(trigger)


@router.post(
    "/zones/{zone_name}/encounters/{position}/triggers",
    response_model=TriggerEntry,
    status_code=201,
)
async def create_trigger(
    zone_name: str,
    position: int,
    body: TriggerUpsertRequest,
    user: SessionUser = Depends(require_editor),
) -> TriggerEntry:
    """Append a new trigger. ``category`` defaults to the encounter's mob
    name on save — when ACT imports the file it groups triggers under that
    name."""
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    category = body.category or mob_name

    def _write() -> int:
        conn = raids_db.init_db()
        try:
            return raids_db.upsert_act_trigger(
                conn,
                raid_encounter_id=encounter_id,
                regex=body.regex,
                position=body.position,
                label=body.label,
                notes=body.notes,
                active=body.active,
                sound_data=body.sound_data,
                sound_type=body.sound_type,
                category_restrict=body.category_restrict,
                category=category,
                timer=body.timer,
                timer_name=body.timer_name,
                tabbed=body.tabbed,
                edited_by=user["id"],
            )
        finally:
            conn.close()

    new_id = await run_sync(_write)
    row = await run_sync(raids_db.get_act_trigger, new_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to load freshly-created trigger")
    return _trigger_row_to_entry(row)


@router.put(
    "/zones/{zone_name}/encounters/{position}/triggers/{trigger_id}",
    response_model=TriggerEntry,
)
async def update_trigger(
    zone_name: str,
    position: int,
    trigger_id: int,
    body: TriggerUpsertRequest,
    user: SessionUser = Depends(require_editor),
) -> TriggerEntry:
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)

    # Verify the trigger belongs to the resolved encounter (avoid letting
    # an editor edit a trigger on another boss by guessing IDs).
    existing = await run_sync(raids_db.get_act_trigger, trigger_id)
    if existing is None or existing["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Trigger not found")

    category = body.category or mob_name

    def _write() -> int:
        conn = raids_db.init_db()
        try:
            return raids_db.upsert_act_trigger(
                conn,
                trigger_id=trigger_id,
                raid_encounter_id=encounter_id,
                regex=body.regex,
                position=body.position,
                label=body.label,
                notes=body.notes,
                active=body.active,
                sound_data=body.sound_data,
                sound_type=body.sound_type,
                category_restrict=body.category_restrict,
                category=category,
                timer=body.timer,
                timer_name=body.timer_name,
                tabbed=body.tabbed,
                edited_by=user["id"],
            )
        finally:
            conn.close()

    await run_sync(_write)
    row = await run_sync(raids_db.get_act_trigger, trigger_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to load updated trigger")
    return _trigger_row_to_entry(row)


@router.delete(
    "/zones/{zone_name}/encounters/{position}/triggers/{trigger_id}",
    status_code=200,
)
async def delete_trigger(
    zone_name: str,
    position: int,
    trigger_id: int,
    user: SessionUser = Depends(require_editor),  # noqa: ARG001 — auth check
) -> dict:
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    existing = await run_sync(raids_db.get_act_trigger, trigger_id)
    if existing is None or existing["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Trigger not found")

    def _delete() -> bool:
        conn = raids_db.init_db()
        try:
            return raids_db.delete_act_trigger(conn, trigger_id)
        finally:
            conn.close()

    removed = await run_sync(_delete)
    if not removed:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# XML paste-import endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/zones/{zone_name}/encounters/{position}/triggers/import-xml",
    response_model=ImportXmlResponse,
    status_code=201,
)
async def import_triggers_xml(
    zone_name: str,
    position: int,
    body: ImportXmlRequest,
    user: SessionUser = Depends(require_editor),
) -> ImportXmlResponse:
    """Paste-import path: parse one or more ``<Trigger>`` / ``<Spell>``
    elements (ACT's verbose XML or its "shareable" short-attribute form)
    and write them to this encounter.

    Behaviour:
      * Each ``<Trigger>``'s ``Category`` is restamped to the encounter's
        mob name (matches the manual create flow), but the source
        category is preserved in ``notes`` so the import provenance isn't
        lost.
      * Triggers with the same ``(regex, sound_data)`` as an existing row
        on this encounter are **skipped** rather than duplicated — the
        endpoint is safely re-callable.
      * Spell timers are upserted by name (UNIQUE within encounter), so
        re-importing the same ``<Spell>`` refreshes its fields rather than
        409'ing.
    """
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)

    triggers, spell_timers = parse_import_xml(body.xml)

    triggers_added: list[int] = []
    triggers_skipped = 0
    spell_timers_added: list[int] = []

    def _write_triggers() -> tuple[list[int], int]:
        added: list[int] = []
        skipped = 0
        conn = raids_db.init_db()
        try:
            for t in triggers:
                existing_id = _trigger_already_exists_sync(encounter_id, t["regex"], t["sound_data"])
                if existing_id is not None:
                    skipped += 1
                    continue
                source_cat = t.get("category") or ""
                notes = f"Imported via paste-XML (Category={source_cat!r})" if source_cat else "Imported via paste-XML"
                new_id = raids_db.upsert_act_trigger(
                    conn,
                    raid_encounter_id=encounter_id,
                    regex=t["regex"],
                    position=0,
                    label=None,
                    notes=notes,
                    active=t["active"],
                    sound_data=t["sound_data"],
                    sound_type=t["sound_type"],
                    category_restrict=t["category_restrict"],
                    category=mob_name,  # restamp; original lives in notes
                    timer=t["timer"],
                    timer_name=t["timer_name"],
                    tabbed=t["tabbed"],
                    edited_by=user["id"],
                )
                added.append(new_id)
        finally:
            conn.close()
        return added, skipped

    def _write_spell_timers() -> list[int]:
        added: list[int] = []
        conn = raids_db.init_db()
        try:
            for s in spell_timers:
                if not s["name"]:
                    continue
                existing_id = _spell_timer_id_for_name_sync(encounter_id, s["name"])
                new_id = raids_db.upsert_act_spell_timer(
                    conn,
                    timer_id=existing_id,
                    raid_encounter_id=encounter_id,
                    name=s["name"],
                    timer_duration_s=s["timer_duration_s"],
                    checked=s["checked"],
                    only_master_ticks=s["only_master_ticks"],
                    restrict=s["restrict"],
                    absolute_=s["absolute_"],
                    start_wav=s["start_wav"],
                    warning_wav=s["warning_wav"],
                    warning_value=s["warning_value"],
                    radial_display=s["radial_display"],
                    modable=s["modable"],
                    tooltip=s["tooltip"],
                    fill_color=s["fill_color"],
                    panel1=s["panel1"],
                    panel2=s["panel2"],
                    remove_value=s["remove_value"],
                    category=mob_name,  # restamp
                    restrict_category=s["restrict_category"],
                    edited_by=user["id"],
                )
                added.append(new_id)
        finally:
            conn.close()
        return added

    triggers_added, triggers_skipped = await run_sync(_write_triggers)
    spell_timers_added = await run_sync(_write_spell_timers)

    # Hydrate the response with the freshly-written rows (in insert order).
    trigger_rows: list[TriggerEntry] = []
    for tid in triggers_added:
        row = await run_sync(raids_db.get_act_trigger, tid)
        if row is not None:
            trigger_rows.append(_trigger_row_to_entry(row))

    spell_rows: list[SpellTimerEntry] = []
    for sid in spell_timers_added:
        row = await run_sync(raids_db.get_act_spell_timer, sid)
        if row is not None:
            spell_rows.append(_spell_row_to_entry(row))

    return ImportXmlResponse(
        triggers_added=len(triggers_added),
        triggers_skipped_existing=triggers_skipped,
        spell_timers_added=len(spell_timers_added),
        triggers=trigger_rows,
        spell_timers=spell_rows,
    )
