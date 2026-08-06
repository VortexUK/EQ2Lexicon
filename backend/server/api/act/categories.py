"""Contributor-defined trigger CATEGORIES — "Death Saves", "Cures", … —
general-purpose trigger/timer groups not tied to any boss.

Storage is deliberately boring: each category is a synthetic encounter
under the ``General`` raid zone (see ``_shared.GENERAL_ZONE``), so every
existing per-encounter route works on it unchanged — the frontend editors
hit ``/api/zones/General/encounters/{position}/triggers`` etc., XML
export/import just works, and the app's ``/api/act/pack`` ships categories
as a "General" zone that old clients already know how to file (the app's
``Category ?? mob`` fallback names the group after the category). This
router only manages the category rows themselves."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.eq2db.raids import catalogue as raids_db
from backend.server.api.act._shared import (
    GENERAL_ZONE,
    _ensure_raids_db_inited,
)
from backend.server.auth_deps import require_editor
from backend.server.core.audit_log import audit_log
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser

_log = logging.getLogger(__name__)

router = APIRouter(tags=["act-categories"])


class CategoryEntry(BaseModel):
    name: str
    position: int
    trigger_count: int = 0
    spell_timer_count: int = 0


class CategoryUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


def _list_categories_sync() -> list[dict]:
    _ensure_raids_db_inited()
    with sqlite3.connect(raids_db.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT e.mob_name AS name, e.position,
                      (SELECT COUNT(*) FROM act_triggers t WHERE t.raid_encounter_id = e.id) AS trigger_count,
                      (SELECT COUNT(*) FROM act_spell_timers s WHERE s.raid_encounter_id = e.id) AS spell_timer_count
               FROM raid_encounters e
               JOIN raid_zones z ON z.id = e.raid_zone_id
               WHERE z.zone_name_lower = ?
               ORDER BY e.position, e.mob_name""",
            (GENERAL_ZONE.lower(),),
        ).fetchall()
        return [dict(r) for r in rows]


def _create_category_sync(name: str) -> tuple[int, str] | None:
    """Create the category (and the General zone row on first use).
    Returns (position, name) or None when the name already exists."""
    _ensure_raids_db_inited()
    with sqlite3.connect(raids_db.path) as conn:
        conn.row_factory = sqlite3.Row
        zrow = conn.execute(
            "SELECT id FROM raid_zones WHERE zone_name_lower = ?",
            (GENERAL_ZONE.lower(),),
        ).fetchone()
        zone_id = (
            zrow["id"]
            if zrow is not None
            else raids_db.upsert_raid_zone(
                conn,
                zone_name=GENERAL_ZONE,
                expansion_short=GENERAL_ZONE,
                source=raids_db.SOURCE_MANUAL,
            )
        )
        exists = conn.execute(
            "SELECT 1 FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
            (zone_id, name.lower()),
        ).fetchone()
        if exists is not None:
            return None
        position = conn.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM raid_encounters WHERE raid_zone_id = ?",
            (zone_id,),
        ).fetchone()[0]
        raids_db.upsert_raid_encounter(
            conn,
            raid_zone_id=zone_id,
            mob_name=name,
            position=int(position),
            strategy_md=None,
            source=raids_db.SOURCE_MANUAL,
        )
        return int(position), name


def _rename_category_sync(position: int, new_name: str) -> bool | None:
    """Rename by position. True = renamed, False = position unknown,
    None = the new name collides with an existing category."""
    _ensure_raids_db_inited()
    with sqlite3.connect(raids_db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT e.mob_name FROM raid_encounters e
               JOIN raid_zones z ON z.id = e.raid_zone_id
               WHERE z.zone_name_lower = ? AND e.position = ?""",
            (GENERAL_ZONE.lower(), position),
        ).fetchone()
        if row is None:
            return False
        collision = conn.execute(
            """SELECT 1 FROM raid_encounters e
               JOIN raid_zones z ON z.id = e.raid_zone_id
               WHERE z.zone_name_lower = ? AND e.mob_name_lower = ? AND e.position != ?""",
            (GENERAL_ZONE.lower(), new_name.lower(), position),
        ).fetchone()
        if collision is not None:
            return None
        return raids_db.rename_raid_encounter_if_exists(
            conn,
            zone_name=GENERAL_ZONE,
            old_mob_name=row["mob_name"],
            new_mob_name=new_name,
        )


def _delete_category_sync(position: int) -> bool:
    _ensure_raids_db_inited()
    with sqlite3.connect(raids_db.path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT e.mob_name FROM raid_encounters e
               JOIN raid_zones z ON z.id = e.raid_zone_id
               WHERE z.zone_name_lower = ? AND e.position = ?""",
            (GENERAL_ZONE.lower(), position),
        ).fetchone()
        if row is None:
            return False
        return raids_db.delete_raid_encounter_by_zone_mob(conn, zone_name=GENERAL_ZONE, mob_name=row["mob_name"])


@router.get("/act/categories", response_model=list[CategoryEntry])
async def list_categories() -> list[CategoryEntry]:
    """Every contributor-defined category with its content counts —
    includes empty ones (the pack omits those; this list must not)."""
    rows = await run_sync(_list_categories_sync)
    return [CategoryEntry(**r) for r in rows]


@router.post("/act/categories", response_model=CategoryEntry, status_code=201)
async def create_category(
    body: CategoryUpsertRequest,
    user: SessionUser = Depends(require_editor),
) -> CategoryEntry:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Category name must not be blank")
    created = await run_sync(_create_category_sync, name)
    if created is None:
        raise HTTPException(status_code=409, detail="A category with that name already exists")
    position, canonical = created
    audit_log("act_category_created", actor=user["id"], name=canonical)
    return CategoryEntry(name=canonical, position=position)


@router.put("/act/categories/{position}", response_model=CategoryEntry)
async def rename_category(
    position: int,
    body: CategoryUpsertRequest,
    user: SessionUser = Depends(require_editor),
) -> CategoryEntry:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Category name must not be blank")
    renamed = await run_sync(_rename_category_sync, position, name)
    if renamed is None:
        raise HTTPException(status_code=409, detail="A category with that name already exists")
    if not renamed:
        raise HTTPException(status_code=404, detail="Category not found")
    audit_log("act_category_renamed", actor=user["id"], name=name, position=position)
    return CategoryEntry(name=name, position=position)


@router.delete("/act/categories/{position}")
async def delete_category(
    position: int,
    user: SessionUser = Depends(require_editor),
) -> dict:
    """Deletes the category AND its triggers/timers (FK cascade) — the
    frontend confirms before calling."""
    deleted = await run_sync(_delete_category_sync, position)
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found")
    audit_log("act_category_deleted", actor=user["id"], position=position)
    return {"ok": True}
