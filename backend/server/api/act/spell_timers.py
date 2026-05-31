"""ACT spell timer endpoints.

Surface:
  GET    /api/zones/{zone}/encounters/{position}/spell-timers          (list)
  POST   /api/zones/{zone}/encounters/{position}/spell-timers          (create, editor)
  PUT    /api/zones/{zone}/encounters/{position}/spell-timers/{id}     (update, editor)
  DELETE /api/zones/{zone}/encounters/{position}/spell-timers/{id}     (delete, editor)
  GET    /api/zones/{zone}/encounters/{position}/spell-timers/{id}/export.xml  (single XML)
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend.eq2db import raids as raids_db
from backend.server.api.act._shared import (
    SpellTimerEntry,
    _resolve_encounter,
    _spell_row_to_entry,
)
from backend.server.api.act.xml_export import build_xml, safe_filename
from backend.server.auth_deps import require_editor
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser

router = APIRouter(tags=["act_triggers"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SpellTimerUpsertRequest(BaseModel):
    """Body for create + update."""

    name: str = Field(..., min_length=1)
    timer_duration_s: int = Field(..., gt=0)
    checked: bool = False
    only_master_ticks: bool = False
    restrict: bool = False
    absolute: bool = False
    start_wav: str = ""
    warning_wav: str = ""
    warning_value: int = 10
    radial_display: bool = False
    modable: bool = False
    tooltip: str = ""
    fill_color: int = -16776961
    panel1: bool = True
    panel2: bool = False
    remove_value: int = -15
    category: str | None = None
    restrict_category: bool = False


# ---------------------------------------------------------------------------
# Spell timer endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/zones/{zone_name}/encounters/{position}/spell-timers",
    response_model=list[SpellTimerEntry],
)
async def list_spell_timers(zone_name: str, position: int) -> list[SpellTimerEntry]:
    """All spell timers defined for this encounter."""
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    rows = await run_sync(raids_db.list_act_spell_timers_for_encounter, encounter_id)
    return [_spell_row_to_entry(r) for r in rows]


@router.get(
    "/zones/{zone_name}/encounters/{position}/spell-timers/{timer_id}/export.xml",
    response_class=Response,
)
async def export_spell_timer(zone_name: str, position: int, timer_id: int) -> Response:
    """A single spell timer as a paste-friendly XML chunk. Lets curators
    share one standalone timer without bundling the encounter's full
    trigger pack — the mirror of export_trigger above."""
    canonical_zone, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    timer = await run_sync(raids_db.get_act_spell_timer, timer_id)
    if timer is None or timer["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Spell timer not found")

    xml = build_xml([], [timer])
    filename = safe_filename(f"{canonical_zone}-{mob_name}-{timer['name']}.xml")
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/zones/{zone_name}/encounters/{position}/spell-timers",
    response_model=SpellTimerEntry,
    status_code=201,
)
async def create_spell_timer(
    zone_name: str,
    position: int,
    body: SpellTimerUpsertRequest,
    user: SessionUser = Depends(require_editor),
) -> SpellTimerEntry:
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    category = body.category or mob_name

    def _write() -> int:
        conn = raids_db.init_db()
        try:
            return raids_db.upsert_act_spell_timer(
                conn,
                raid_encounter_id=encounter_id,
                name=body.name,
                timer_duration_s=body.timer_duration_s,
                checked=body.checked,
                only_master_ticks=body.only_master_ticks,
                restrict=body.restrict,
                absolute_=body.absolute,
                start_wav=body.start_wav,
                warning_wav=body.warning_wav,
                warning_value=body.warning_value,
                radial_display=body.radial_display,
                modable=body.modable,
                tooltip=body.tooltip,
                fill_color=body.fill_color,
                panel1=body.panel1,
                panel2=body.panel2,
                remove_value=body.remove_value,
                category=category,
                restrict_category=body.restrict_category,
                edited_by=user["id"],
            )
        finally:
            conn.close()

    try:
        new_id = await run_sync(_write)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"A spell timer named {body.name!r} already exists for this encounter",
        ) from exc

    row = await run_sync(raids_db.get_act_spell_timer, new_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to load freshly-created spell timer")
    return _spell_row_to_entry(row)


@router.put(
    "/zones/{zone_name}/encounters/{position}/spell-timers/{timer_id}",
    response_model=SpellTimerEntry,
)
async def update_spell_timer(
    zone_name: str,
    position: int,
    timer_id: int,
    body: SpellTimerUpsertRequest,
    user: SessionUser = Depends(require_editor),
) -> SpellTimerEntry:
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    existing = await run_sync(raids_db.get_act_spell_timer, timer_id)
    if existing is None or existing["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Spell timer not found")

    category = body.category or mob_name

    def _write() -> int:
        conn = raids_db.init_db()
        try:
            return raids_db.upsert_act_spell_timer(
                conn,
                timer_id=timer_id,
                raid_encounter_id=encounter_id,
                name=body.name,
                timer_duration_s=body.timer_duration_s,
                checked=body.checked,
                only_master_ticks=body.only_master_ticks,
                restrict=body.restrict,
                absolute_=body.absolute,
                start_wav=body.start_wav,
                warning_wav=body.warning_wav,
                warning_value=body.warning_value,
                radial_display=body.radial_display,
                modable=body.modable,
                tooltip=body.tooltip,
                fill_color=body.fill_color,
                panel1=body.panel1,
                panel2=body.panel2,
                remove_value=body.remove_value,
                category=category,
                restrict_category=body.restrict_category,
                edited_by=user["id"],
            )
        finally:
            conn.close()

    try:
        await run_sync(_write)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Renaming to {body.name!r} would clash with another timer for this encounter",
        ) from exc

    row = await run_sync(raids_db.get_act_spell_timer, timer_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to load updated spell timer")
    return _spell_row_to_entry(row)


@router.delete(
    "/zones/{zone_name}/encounters/{position}/spell-timers/{timer_id}",
    status_code=200,
)
async def delete_spell_timer(
    zone_name: str,
    position: int,
    timer_id: int,
    user: SessionUser = Depends(require_editor),  # noqa: ARG001 — auth check
) -> dict:
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    existing = await run_sync(raids_db.get_act_spell_timer, timer_id)
    if existing is None or existing["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Spell timer not found")

    def _delete() -> bool:
        conn = raids_db.init_db()
        try:
            return raids_db.delete_act_spell_timer(conn, timer_id)
        finally:
            conn.close()

    removed = await run_sync(_delete)
    if not removed:
        raise HTTPException(status_code=404, detail="Spell timer not found")
    return {"ok": True}
