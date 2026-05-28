"""
ACT triggers + spell timers per encounter.

Surface:
  GET    /api/zones/{zone}/encounters/{position}/triggers              (list, public)
  POST   /api/zones/{zone}/encounters/{position}/triggers              (create, editor)
  GET    /api/zones/{zone}/encounters/{position}/triggers/{id}         (single, public)
  PUT    /api/zones/{zone}/encounters/{position}/triggers/{id}         (update, editor)
  DELETE /api/zones/{zone}/encounters/{position}/triggers/{id}         (delete, editor)
  GET    /api/zones/{zone}/encounters/{position}/triggers/{id}/export.xml  (single XML)
  GET    /api/zones/{zone}/encounters/{position}/triggers/export.xml       (all XML)

  GET    /api/zones/{zone}/encounters/{position}/spell-timers          (list)
  POST   /api/zones/{zone}/encounters/{position}/spell-timers          (create, editor)
  PUT    /api/zones/{zone}/encounters/{position}/spell-timers/{id}     (update, editor)
  DELETE /api/zones/{zone}/encounters/{position}/spell-timers/{id}     (delete, editor)
  GET    /api/zones/{zone}/encounters/{position}/spell-timers/{id}/export.xml  (single XML)

The XML exports round-trip against ACT's ``spell_timers.xml`` import format —
drop the file into Advanced Combat Tracker's Custom Triggers import and it
loads without manual edits. Each export bundles both a `<Trigger>` and (if
``timer=true``) a matching `<Spell>` so the import is self-contained.
"""

from __future__ import annotations

import asyncio
import sqlite3
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from census import raids_db, zones_db
from web.auth_deps import require_editor

router = APIRouter(tags=["act_triggers"])


# ---------------------------------------------------------------------------
# Models
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
# Sync helpers — encounter resolution + DB shapes
# ---------------------------------------------------------------------------


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
    # ALWAYS call init_db() (not just on the file-missing branch) — it's
    # idempotent (CREATE TABLE IF NOT EXISTS) and is the only thing that
    # ensures the act_triggers / act_spell_timers tables exist on an older
    # raids.db that was seeded before they were added to the schema. Without
    # this, a viewer hitting the GET endpoint against a stale DB sees
    # "no such table: act_triggers" → 500.
    raids_db.init_db().close()

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
    resolved = await asyncio.get_event_loop().run_in_executor(None, _resolve_encounter_sync, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    return resolved


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


# ---------------------------------------------------------------------------
# XML serialisation (ACT spell_timers.xml format)
# ---------------------------------------------------------------------------


def _bool_attr(b: bool | int) -> str:
    """ACT serialises booleans as `True`/`False` (capitalised, .NET style)."""
    return "True" if b else "False"


def _trigger_to_xml(trigger: dict) -> str:
    """Render one `act_triggers` row as a <Trigger ... /> element. The
    attribute *order* matches ACT's own output — not strictly required for
    parsing, but it makes XML diffs against an authored file cleaner."""
    return (
        "<Trigger "
        f"Active={quoteattr(_bool_attr(trigger['active']))} "
        f"Regex={quoteattr(trigger['regex'])} "
        f"SoundData={quoteattr(trigger['sound_data'] or '')} "
        f'SoundType="{int(trigger["sound_type"])}" '
        f"CategoryRestrict={quoteattr(_bool_attr(trigger['category_restrict']))} "
        f"Category={quoteattr(trigger['category'] or '')} "
        f"Timer={quoteattr(_bool_attr(trigger['timer']))} "
        f"TimerName={quoteattr(trigger['timer_name'] or '')} "
        f"Tabbed={quoteattr(_bool_attr(trigger['tabbed']))} />"
    )


def _spell_to_xml(spell: dict) -> str:
    """Render one `act_spell_timers` row as a <Spell ... /> element."""
    return (
        "<Spell "
        f"Checked={quoteattr(_bool_attr(spell['checked']))} "
        f"Name={quoteattr(spell['name'])} "
        f'Timer="{int(spell["timer_duration_s"])}" '
        f"OnlyMasterTicks={quoteattr(_bool_attr(spell['only_master_ticks']))} "
        f"Restrict={quoteattr(_bool_attr(spell['restrict']))} "
        f"Absolute={quoteattr(_bool_attr(spell['absolute_']))} "
        f"StartWav={quoteattr(spell['start_wav'] or '')} "
        f"WarningWav={quoteattr(spell['warning_wav'] or '')} "
        f'WarningValue="{int(spell["warning_value"])}" '
        f"RadialDisplay={quoteattr(_bool_attr(spell['radial_display']))} "
        f"Modable={quoteattr(_bool_attr(spell['modable']))} "
        f"Tooltip={quoteattr(spell['tooltip'] or '')} "
        f'FillColor="{int(spell["fill_color"])}" '
        f"Panel1={quoteattr(_bool_attr(spell['panel1']))} "
        f"Panel2={quoteattr(_bool_attr(spell['panel2']))} "
        f'RemoveValue="{int(spell["remove_value"])}" '
        f"Category={quoteattr(spell['category'] or '')} "
        f"RestrictCategory={quoteattr(_bool_attr(spell['restrict_category']))} />"
    )


def _build_xml(triggers: list[dict], spells: list[dict]) -> str:
    """Assemble the full ACT-compatible XML document.

    Both lists may be empty; an empty document is valid and parses cleanly
    in ACT (it just adds nothing on import)."""
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<Config>",
        "    <CustomTriggers>",
    ]
    for t in triggers:
        parts.append("        " + _trigger_to_xml(t))
    parts.append("    </CustomTriggers>")
    parts.append("    <SpellTimers>")
    for s in spells:
        parts.append("        " + _spell_to_xml(s))
    parts.append("    </SpellTimers>")
    parts.append("    <SettingsSerializer />")
    parts.append("</Config>")
    return "\n".join(parts) + "\n"


def _safe_filename(s: str) -> str:
    """Make a string safe for an HTTP Content-Disposition filename. ACT
    doesn't care about the name when importing, but the browser save
    dialog defaults to it."""
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    return out.strip("_") or "trigger"


def _spell_timers_referenced_by(triggers: list[dict], all_spells: list[dict]) -> list[dict]:
    """Pick out the spell timers a list of triggers actually references via
    `timer_name`. Dedup by lower-cased name (the table's UNIQUE key) so the
    export doesn't emit duplicate <Spell> rows when multiple triggers share
    one timer."""
    referenced: set[str] = {(t["timer_name"] or "").lower() for t in triggers if t.get("timer") and t.get("timer_name")}
    if not referenced:
        return []
    by_lower = {s["name"].lower(): s for s in all_spells}
    out: list[dict] = []
    for name_lower in referenced:
        s = by_lower.get(name_lower)
        if s is not None:
            out.append(s)
    return out


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
    rows = await asyncio.get_event_loop().run_in_executor(None, raids_db.list_act_triggers_for_encounter, encounter_id)
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
    loop = asyncio.get_event_loop()
    triggers = await loop.run_in_executor(None, raids_db.list_act_triggers_for_encounter, encounter_id)
    spells = await loop.run_in_executor(None, raids_db.list_act_spell_timers_for_encounter, encounter_id)
    # Emit EVERY spell timer for this encounter — both the ones a trigger
    # references and standalone ones (which fire off ACT's native skill/CA
    # name-match). The table's UNIQUE(encounter, name_lower) keeps <Spell>
    # rows unique without extra dedup.
    xml = _build_xml(triggers, spells)
    filename = _safe_filename(f"{canonical_zone}-{mob_name}-triggers.xml")
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
    loop = asyncio.get_event_loop()
    trigger = await loop.run_in_executor(None, raids_db.get_act_trigger, trigger_id)
    if trigger is None or trigger["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Trigger not found")

    spells: list[dict] = []
    if trigger.get("timer") and trigger.get("timer_name"):
        all_spells = await loop.run_in_executor(None, raids_db.list_act_spell_timers_for_encounter, encounter_id)
        spells = _spell_timers_referenced_by([trigger], all_spells)

    xml = _build_xml([trigger], spells)
    label = trigger.get("label") or trigger.get("sound_data") or "trigger"
    filename = _safe_filename(f"{canonical_zone}-{mob_name}-{label}.xml")
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/zones/{zone_name}/encounters/{position}/spell-timers/{timer_id}/export.xml",
    response_class=Response,
)
async def export_spell_timer(zone_name: str, position: int, timer_id: int) -> Response:
    """A single spell timer as a paste-friendly XML chunk. Lets curators
    share one standalone timer without bundling the encounter's full
    trigger pack — the mirror of export_trigger above."""
    canonical_zone, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    loop = asyncio.get_event_loop()
    timer = await loop.run_in_executor(None, raids_db.get_act_spell_timer, timer_id)
    if timer is None or timer["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Spell timer not found")

    xml = _build_xml([], [timer])
    filename = _safe_filename(f"{canonical_zone}-{mob_name}-{timer['name']}.xml")
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
    trigger = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_trigger, trigger_id)
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
    user: dict = Depends(require_editor),
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

    new_id = await asyncio.get_event_loop().run_in_executor(None, _write)
    row = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_trigger, new_id)
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
    user: dict = Depends(require_editor),
) -> TriggerEntry:
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)

    # Verify the trigger belongs to the resolved encounter (avoid letting
    # an editor edit a trigger on another boss by guessing IDs).
    existing = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_trigger, trigger_id)
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

    await asyncio.get_event_loop().run_in_executor(None, _write)
    row = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_trigger, trigger_id)
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
    user: dict = Depends(require_editor),  # noqa: ARG001 — auth check
) -> dict:
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    existing = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_trigger, trigger_id)
    if existing is None or existing["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Trigger not found")

    def _delete() -> bool:
        conn = raids_db.init_db()
        try:
            return raids_db.delete_act_trigger(conn, trigger_id)
        finally:
            conn.close()

    removed = await asyncio.get_event_loop().run_in_executor(None, _delete)
    if not removed:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"ok": True}


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
    rows = await asyncio.get_event_loop().run_in_executor(
        None, raids_db.list_act_spell_timers_for_encounter, encounter_id
    )
    return [_spell_row_to_entry(r) for r in rows]


@router.post(
    "/zones/{zone_name}/encounters/{position}/spell-timers",
    response_model=SpellTimerEntry,
    status_code=201,
)
async def create_spell_timer(
    zone_name: str,
    position: int,
    body: SpellTimerUpsertRequest,
    user: dict = Depends(require_editor),
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
        new_id = await asyncio.get_event_loop().run_in_executor(None, _write)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"A spell timer named {body.name!r} already exists for this encounter",
        ) from exc

    row = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_spell_timer, new_id)
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
    user: dict = Depends(require_editor),
) -> SpellTimerEntry:
    _, mob_name, encounter_id = await _resolve_encounter(zone_name, position)
    existing = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_spell_timer, timer_id)
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
        await asyncio.get_event_loop().run_in_executor(None, _write)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Renaming to {body.name!r} would clash with another timer for this encounter",
        ) from exc

    row = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_spell_timer, timer_id)
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
    user: dict = Depends(require_editor),  # noqa: ARG001 — auth check
) -> dict:
    _, _, encounter_id = await _resolve_encounter(zone_name, position)
    existing = await asyncio.get_event_loop().run_in_executor(None, raids_db.get_act_spell_timer, timer_id)
    if existing is None or existing["raid_encounter_id"] != encounter_id:
        raise HTTPException(status_code=404, detail="Spell timer not found")

    def _delete() -> bool:
        conn = raids_db.init_db()
        try:
            return raids_db.delete_act_spell_timer(conn, timer_id)
        finally:
            conn.close()

    removed = await asyncio.get_event_loop().run_in_executor(None, _delete)
    if not removed:
        raise HTTPException(status_code=404, detail="Spell timer not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# XML paste-import
# ---------------------------------------------------------------------------
#
# Accepts both ACT's verbose XML (``<Trigger Regex="..." ...>``, what
# spell_timers.xml exports) AND the "shareable" short-attribute format you
# get from ACT's right-click → Copy as Shareable XML
# (``<Trigger R="..." SD="..." ST="3" CR="F" C="..." T="T" TN="..." Ta="F" />``).
#
# The input can be:
#   * A single ``<Trigger />`` or ``<Spell />`` element (the usual paste).
#   * Multiple sibling elements at the top level.
#   * A wrapping ``<root>``, ``<Triggers>``, ``<CustomTriggers>``, or full
#     ``<Config>`` block.
# The parser wraps the input in a synthetic root if needed so ElementTree
# can parse it either way.


def _import_bool(s: str | None, default: bool = False) -> bool:
    """ACT booleans come in three forms across the long/short variants:
    ``True``/``False``, ``T``/``F``, ``true``/``false`` (occasionally
    ``1``/``0``). Accept all of them."""
    if s is None:
        return default
    v = s.strip().lower()
    if v in ("true", "t", "1", "yes"):
        return True
    if v in ("false", "f", "0", "no"):
        return False
    return default


def _import_int(s: str | None, default: int = 0) -> int:
    try:
        return int((s or "").strip())
    except (TypeError, ValueError):
        return default


def _trigger_from_element(el: ET.Element) -> dict:
    """Map a ``<Trigger>`` element (verbose or short form) to the keyword
    args of ``raids_db.upsert_act_trigger``. ``category`` is kept as-is here
    so the route layer can decide whether to restamp to the mob name."""

    def attr(*keys: str, default: str | None = None) -> str | None:
        for k in keys:
            v = el.get(k)
            if v is not None:
                return v
        return default

    return {
        "active": _import_bool(attr("Active", "A"), default=True),
        "regex": attr("Regex", "R", default="") or "",
        "sound_data": attr("SoundData", "SD", default="") or "",
        "sound_type": _import_int(attr("SoundType", "ST"), default=3),
        "category_restrict": _import_bool(attr("CategoryRestrict", "CR")),
        "category": attr("Category", "C"),
        "timer": _import_bool(attr("Timer", "T")),
        "timer_name": (attr("TimerName", "TN") or "").strip() or None,
        "tabbed": _import_bool(attr("Tabbed", "Ta")),
    }


def _spell_from_element(el: ET.Element) -> dict:
    """Map a ``<Spell>`` element to upsert_act_spell_timer kwargs.

    Accepts both ACT's verbose form (``Name``/``Timer``/``OnlyMasterTicks`` …
    as written by ``spell_timers.xml``) and the "shareable" short form ACT's
    right-click → Copy as Shareable XML produces:

      ``N`` Name, ``T`` Timer, ``OM`` OnlyMasterTicks, ``R`` Restrict,
      ``A`` Absolute, ``SW`` StartWav, ``WW`` WarningWav, ``WV`` WarningValue,
      ``RD`` RadialDisplay, ``M`` Modable, ``Tt`` Tooltip, ``FC`` FillColor,
      ``P1`` Panel1, ``P2`` Panel2, ``RV`` RemoveValue, ``C`` Category,
      ``RC`` RestrictCategory, ``Ch`` Checked.

    Single-letter keys are reused across element types (Trigger's ``R`` =
    Regex vs Spell's ``R`` = Restrict, Trigger's ``T`` = Timer bool vs
    Spell's ``T`` = duration int) — that's only safe because Trigger and
    Spell elements are parsed by separate functions."""

    def attr(*keys: str, default: str | None = None) -> str | None:
        for k in keys:
            v = el.get(k)
            if v is not None:
                return v
        return default

    return {
        "name": (attr("Name", "N") or "").strip(),
        "checked": _import_bool(attr("Checked", "Ch")),
        "timer_duration_s": _import_int(attr("Timer", "T"), default=30),
        "only_master_ticks": _import_bool(attr("OnlyMasterTicks", "OMT", "OM")),
        "restrict": _import_bool(attr("Restrict", "R")),
        "absolute_": _import_bool(attr("Absolute", "Abs", "A")),
        "start_wav": attr("StartWav", "SW", default="") or "",
        "warning_wav": attr("WarningWav", "WW", default="") or "",
        "warning_value": _import_int(attr("WarningValue", "WV"), default=10),
        "radial_display": _import_bool(attr("RadialDisplay", "RD")),
        "modable": _import_bool(attr("Modable", "M")),
        "tooltip": attr("Tooltip", "Tt", default="") or "",
        "fill_color": _import_int(attr("FillColor", "FC"), default=-16776961),
        "panel1": _import_bool(attr("Panel1", "P1"), default=True),
        "panel2": _import_bool(attr("Panel2", "P2")),
        "remove_value": _import_int(attr("RemoveValue", "RV"), default=-15),
        "category": attr("Category", "C"),
        "restrict_category": _import_bool(attr("RestrictCategory", "RC")),
    }


def parse_import_xml(text: str) -> tuple[list[dict], list[dict]]:
    """Parse a paste-friendly XML chunk from ACT.

    Returns ``(triggers, spell_timers)`` — both lists of plain dicts ready
    to feed to the ``upsert_act_*`` helpers. Raises ``HTTPException(400)``
    on unparseable input. ``Trigger`` elements with no ``Regex``/``R``
    attribute are dropped silently — same for ``Spell`` with no ``Name``."""
    body = (text or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Empty XML body")

    # Strip the XML decl if present, then always wrap in a synthetic root —
    # this is what lets the parser accept a bare `<Trigger ... />` paste,
    # multiple sibling elements, or a pre-wrapped block all the same way.
    if body.lstrip().startswith("<?"):
        end = body.find("?>")
        if end == -1:
            raise HTTPException(status_code=400, detail="Malformed XML declaration")
        body = body[end + 2 :].strip()

    wrapped = f"<__import_root>{body}</__import_root>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid XML: {exc}") from exc

    triggers = [_trigger_from_element(t) for t in root.iter("Trigger") if (t.get("Regex") or t.get("R"))]
    spells = [_spell_from_element(s) for s in root.iter("Spell") if (s.get("Name") or s.get("N"))]

    if not triggers and not spells:
        raise HTTPException(
            status_code=400,
            detail="No <Trigger> or <Spell> elements found in XML",
        )

    return triggers, spells


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


@router.post(
    "/zones/{zone_name}/encounters/{position}/triggers/import-xml",
    response_model=ImportXmlResponse,
    status_code=201,
)
async def import_triggers_xml(
    zone_name: str,
    position: int,
    body: ImportXmlRequest,
    user: dict = Depends(require_editor),
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
    loop = asyncio.get_event_loop()

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

    triggers_added, triggers_skipped = await loop.run_in_executor(None, _write_triggers)
    spell_timers_added = await loop.run_in_executor(None, _write_spell_timers)

    # Hydrate the response with the freshly-written rows (in insert order).
    trigger_rows: list[TriggerEntry] = []
    for tid in triggers_added:
        row = await loop.run_in_executor(None, raids_db.get_act_trigger, tid)
        if row is not None:
            trigger_rows.append(_trigger_row_to_entry(row))

    spell_rows: list[SpellTimerEntry] = []
    for sid in spell_timers_added:
        row = await loop.run_in_executor(None, raids_db.get_act_spell_timer, sid)
        if row is not None:
            spell_rows.append(_spell_row_to_entry(row))

    return ImportXmlResponse(
        triggers_added=len(triggers_added),
        triggers_skipped_existing=triggers_skipped,
        spell_timers_added=len(spell_timers_added),
        triggers=trigger_rows,
        spell_timers=spell_rows,
    )
