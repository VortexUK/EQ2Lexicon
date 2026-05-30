"""
GET  /api/zones/{zone}/encounters/{position}/strategy
PUT  /api/zones/{zone}/encounters/{position}/strategy             (editor-gated)
GET  /api/zones/{zone}/encounters/{position}/strategy/revisions   (history)
GET  /api/zones/{zone}/overview                                   (zone-level)
PUT  /api/zones/{zone}/overview                                   (editor-gated)
GET  /api/zones/{zone}/overview/revisions                         (history)

Read-write surface for raid strategy markdown — per-encounter strategies and
zone-level overview. Bodies live in ``census/raids_db.py`` (separate SQLite
file from the zones DB). Write gate is ``require_editor`` from
``web/auth_deps.py`` (admin / contributor — see that module's
docstring for the role model).

For encounters, the revision history is recorded automatically by
``upsert_raid_encounter`` so this route doesn't have to think about it on the
write path — only surface it on the read path via the ``/revisions`` endpoint.

Zone overviews share the editor gate and carry a per-field revision history
in ``raid_zone_revisions`` — every PUT to the overview writes a revision row
in the same transaction before returning the updated row.

Key translation: the URL identifies a curator encounter by ``(zone_name,
position)`` (matches the sidebar URLs in the React app). We resolve those via
``zones.db`` → ``zone_encounters.encounter_name`` and use that string as the
raids_db row's ``mob_name`` — one strategy per curator encounter, keyed by the
display name. Group encounters get a single strategy under their joined name.

Lazy zone creation: a PUT for a zone not yet known to raids_db creates the
``raid_zones`` row on the fly, pulling ``expansion_short`` from zones.db.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from census import raids_db, zones_db
from web import db as users_db
from web.auth_deps import require_editor
from web.lib.audit_log import audit_log
from web.lib.executor import run_sync
from web.lib.primary_guild import cached_primary_guild
from web.lib.session_user import SessionUser
from web.server_context import current_world as _current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["raid_strategies"])


# ---------------------------------------------------------------------------
# Primary-guild resolution (shared with web/auth_deps.require_editor)
# ---------------------------------------------------------------------------


async def _primary_guild_from_cache(discord_id: str) -> str | None:
    """Return the cached guild for this user's primary character, or None.

    Cache-only (no Census fallback) — kept hot so the auth check stays cheap.
    A cold cache returns None and the caller 403s; visiting the character
    page once warms it. Mirrors the cheap branch of zones.py's resolver.

    Lives here rather than in web/auth_deps.py because raids_db isn't an auth
    concept — auth_deps imports this lazily inside ``require_editor`` to skirt
    the routes→auth circular dependency."""
    _, guild_name = await cached_primary_guild(discord_id, _current_world())
    return guild_name


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class StrategyResponse(BaseModel):
    zone_name: str
    encounter_name: str
    position: int
    markdown: str
    last_edited_at: int | None = None
    last_edited_by: str | None = None
    last_edited_by_name: str | None = None
    source: str  # SOURCE_SCRAPE / SOURCE_MANUAL


class StrategyUpdateRequest(BaseModel):
    markdown: str = Field(..., description="Full markdown body (replaces the current strategy).")
    edit_note: str | None = Field(None, description="Optional commit-style note attached to the revision.")


class RevisionEntry(BaseModel):
    """One row in the revision history.

    ``before_md`` is NULL on the very first row (the seed insert). Subsequent
    rows always have both before/after so the UI can compute a diff client-side
    if it wants — for now the v1 UI just shows the after_md content."""

    id: int
    edited_at: int
    edited_by: str
    edited_by_name: str | None = None
    before_md: str | None
    after_md: str
    edit_note: str | None


class RevisionListResponse(BaseModel):
    zone_name: str
    encounter_name: str
    position: int
    revisions: list[RevisionEntry]


class ZoneOverviewResponse(BaseModel):
    """Zone-level overview markdown (tactics that apply across encounters).

    Only ``overview_md`` is exposed for now — the schema also has access_md
    and background_md from the wiki scraper but those need their own UI
    treatment to be useful. Surfaced as separate sections later."""

    zone_name: str
    markdown: str
    last_edited_at: int | None = None
    last_edited_by: str | None = None
    last_edited_by_name: str | None = None
    source: str  # SOURCE_SCRAPE / SOURCE_MANUAL


class ZoneOverviewUpdateRequest(BaseModel):
    markdown: str = Field(..., description="Full overview markdown body (replaces the current value).")
    edit_note: str | None = Field(None, description="Optional commit-style note attached to the revision.")


class ZoneRevisionListResponse(BaseModel):
    zone_name: str
    revisions: list[RevisionEntry]


# ---------------------------------------------------------------------------
# Sync helpers — run via run_in_executor
# ---------------------------------------------------------------------------


def _resolve_curator_encounter(zone_name: str, position: int) -> tuple[str, str] | None:
    """Map ``(zone_name, position)`` → ``(canonical_zone_name, encounter_name)``
    via zones.db. Returns None if the zone is unknown or has no encounter at
    that position.

    Canonicalising the zone name (rather than echoing whatever the URL had)
    means an alias-lookup PUT writes the strategy under the canonical key,
    avoiding silent duplicates."""
    z = zones_db.find_by_name(zone_name)
    if z is None:
        return None
    canonical_zone = z["name"]
    # BE-210: bosses is a list[dict]; a {position: encounter_name} dict would be
    # faster but this path is not hot (curator writes only), so defer the rebuild.
    for boss in z.get("bosses", []):
        if int(boss.get("position", -1)) == position:
            return canonical_zone, boss["encounter_name"]
    return None


def _read_revisions_sync(zone_name: str, encounter_name: str) -> list[dict]:
    """All revision rows for an encounter's strategy, newest first.

    Returns ``[]`` if the encounter doesn't exist in raids_db yet (never had a
    strategy written) — the route surfaces that as a 200 with an empty list,
    matching the "show history" disclosure's no-op state."""
    if not raids_db.DB_PATH.exists():
        return []
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        zrow = conn.execute("SELECT id FROM raid_zones WHERE zone_name_lower = ?", (zone_name.lower(),)).fetchone()
        if zrow is None:
            return []
        erow = conn.execute(
            "SELECT id FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
            (zrow["id"], encounter_name.lower()),
        ).fetchone()
        if erow is None:
            return []
    # encounter_revisions opens its own connection — fine, runs in the same
    # executor thread as us.
    return raids_db.encounter_revisions(erow["id"])


def _read_overview_sync(zone_name: str) -> dict | None:
    """Return the raid_zones row for a zone (overview-relevant columns only).

    None when no row exists yet OR when overview_md is empty — same semantics
    as the encounter helpers, lets the route 404 cleanly and the UI fall back
    to the empty state."""
    if not raids_db.DB_PATH.exists():
        return None
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT zone_name, overview_md, source, last_edited_at, last_edited_by "
            "FROM raid_zones WHERE zone_name_lower = ?",
            (zone_name.lower(),),
        ).fetchone()
    if row is None or not row["overview_md"]:
        return None
    return dict(row)


def _create_overview_sync(
    conn: sqlite3.Connection,
    *,
    zone_name: str,
    markdown: str,
    editor_discord_id: str,
    expansion_short: str,
    edit_note: str | None,
    now: int,
) -> None:
    """Insert a brand-new raid_zone overview row with revision history."""
    raids_db.upsert_raid_zone(
        conn,
        zone_name=zone_name,
        expansion_short=expansion_short,
        overview_md=markdown,
        source=raids_db.SOURCE_MANUAL,
    )
    # upsert_raid_zone doesn't set last_edited_at — stamp the audit fields here.
    conn.execute(
        "UPDATE raid_zones SET last_edited_at = ?, last_edited_by = ? WHERE zone_name_lower = ?",
        (now, editor_discord_id, zone_name.lower()),
    )
    # Record first-ever revision with before_md=NULL.
    zone_id_row = conn.execute(
        "SELECT id FROM raid_zones WHERE zone_name_lower = ?",
        (zone_name.lower(),),
    ).fetchone()
    conn.execute(
        "INSERT INTO raid_zone_revisions "
        "(raid_zone_id, edited_at, edited_by, before_md, after_md, edit_note) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (zone_id_row[0], now, editor_discord_id, markdown, edit_note),
    )


def _update_overview_sync(
    conn: sqlite3.Connection,
    *,
    zone_id: int,
    zone_name: str,
    prev_md: str | None,
    markdown: str,
    editor_discord_id: str,
    edit_note: str | None,
    now: int,
) -> None:
    """Update an existing raid_zone overview row and conditionally write a revision entry."""
    conn.execute(
        "UPDATE raid_zones SET "
        "  overview_md = ?, "
        "  source = ?, "
        "  last_edited_at = ?, "
        "  last_edited_by = ? "
        "WHERE id = ?",
        (markdown, raids_db.SOURCE_MANUAL, now, editor_discord_id, zone_id),
    )
    # Only write a revision row when the markdown actually changes.
    if markdown != prev_md:
        conn.execute(
            "INSERT INTO raid_zone_revisions "
            "(raid_zone_id, edited_at, edited_by, before_md, after_md, edit_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (zone_id, now, editor_discord_id, prev_md, markdown, edit_note),
        )


def _write_overview_sync(
    *,
    zone_name: str,
    markdown: str,
    editor_discord_id: str,
    expansion_short: str,
    edit_note: str | None = None,
) -> dict:
    """Targeted update of just ``overview_md`` on the raid_zones row.

    Goes through ``upsert_raid_zone`` only when the row doesn't exist yet
    (to lazy-create it with expansion_short). For an existing row we issue a
    column-scoped UPDATE so access_md / background_md aren't clobbered — the
    full-row upsert helper would null them out.

    Writes a ``raid_zone_revisions`` row in the same transaction whenever the
    markdown actually changes (or on the very first write, with before_md=NULL).
    No revision row is written if the markdown is identical to the current value.

    Dispatches to ``_create_overview_sync`` or ``_update_overview_sync``.
    Returns the fresh row dict matching ``_read_overview_sync``."""
    conn = raids_db.init_db()
    try:
        existing = conn.execute(
            "SELECT id, overview_md FROM raid_zones WHERE zone_name_lower = ?",
            (zone_name.lower(),),
        ).fetchone()
        now = int(time.time())
        if existing is None:
            _create_overview_sync(
                conn,
                zone_name=zone_name,
                markdown=markdown,
                editor_discord_id=editor_discord_id,
                expansion_short=expansion_short,
                edit_note=edit_note,
                now=now,
            )
        else:
            _update_overview_sync(
                conn,
                zone_id=int(existing[0]),
                zone_name=zone_name,
                prev_md=existing[1],
                markdown=markdown,
                editor_discord_id=editor_discord_id,
                edit_note=edit_note,
                now=now,
            )
        conn.commit()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT zone_name, overview_md, source, last_edited_at, last_edited_by "
            "FROM raid_zones WHERE zone_name_lower = ?",
            (zone_name.lower(),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def _read_strategy_sync(zone_name: str, encounter_name: str) -> dict | None:
    """Look up an existing strategy row. Returns None if none exists yet."""
    if not raids_db.DB_PATH.exists():
        return None
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # Find the raid_zones id (loose name match — the strategy DB stores
        # the canonical zone_name verbatim, so a case-insensitive lower-match
        # is robust against any alias canonicalisation drift).
        zrow = conn.execute("SELECT id FROM raid_zones WHERE zone_name_lower = ?", (zone_name.lower(),)).fetchone()
        if zrow is None:
            return None
        erow = conn.execute(
            """
            SELECT id, mob_name, position, strategy_md, source,
                   last_edited_at, last_edited_by
            FROM raid_encounters
            WHERE raid_zone_id = ? AND mob_name_lower = ?
            """,
            (zrow["id"], encounter_name.lower()),
        ).fetchone()
    if erow is None or erow["strategy_md"] is None:
        return None
    return dict(erow)


def _write_strategy_sync(
    *,
    zone_name: str,
    encounter_name: str,
    position: int,
    markdown: str,
    edit_note: str | None,
    editor_discord_id: str,
    expansion_short: str,
) -> dict:
    """Upsert a strategy row. Auto-creates the raid_zones parent on first write.

    Returns the fresh row as a dict (same shape as ``_read_strategy_sync``)."""
    # init_db is idempotent — safe to call every time. Also creates the parent
    # data/raids/ directory.
    conn = raids_db.init_db()
    try:
        zone_id = raids_db.upsert_raid_zone(
            conn,
            zone_name=zone_name,
            expansion_short=expansion_short,
            source=raids_db.SOURCE_MANUAL,
        )
        raids_db.upsert_raid_encounter(
            conn,
            raid_zone_id=zone_id,
            mob_name=encounter_name,
            position=position,
            strategy_md=markdown,
            source=raids_db.SOURCE_MANUAL,
            edited_by=editor_discord_id,
            edit_note=edit_note,
        )
        # Re-read so we return the freshly-merged row (last_edited_at, source,
        # etc. — easier than reconstructing it client-side).
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, mob_name, position, strategy_md, source,
                   last_edited_at, last_edited_by
            FROM raid_encounters
            WHERE raid_zone_id = ? AND mob_name_lower = ?
            """,
            (zone_id, encounter_name.lower()),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Editor name resolution
# ---------------------------------------------------------------------------


async def _resolve_editor_name(edited_by: str | None) -> str | None:
    """Look up the display name for a single ``edited_by`` value.

    Returns None for falsy input or non-discord-id tokens (e.g.
    ``'eq2i_scrape'``, ``'unknown'``) — the frontend formatter handles those
    as special-cased labels."""
    if not edited_by:
        return None
    names = await users_db.get_display_names_for_discord_ids([edited_by])
    return names.get(edited_by)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/zones/{zone_name}/encounters/{position}/strategy",
    response_model=StrategyResponse,
)
async def get_strategy(zone_name: str, position: int) -> StrategyResponse:
    """Fetch the markdown strategy for one curator encounter. 404 when the
    encounter exists but no strategy has been written yet — same as 404 for
    an unknown encounter (callers don't need to distinguish; either way the
    UI falls back to the placeholder)."""
    resolved = await run_sync(_resolve_curator_encounter, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    canonical_zone, encounter_name = resolved

    row = await run_sync(_read_strategy_sync, canonical_zone, encounter_name)
    if row is None:
        raise HTTPException(status_code=404, detail="No strategy yet")

    editor_name = await _resolve_editor_name(row["last_edited_by"])
    return StrategyResponse(
        zone_name=canonical_zone,
        encounter_name=encounter_name,
        position=position,
        markdown=row["strategy_md"] or "",
        last_edited_at=row["last_edited_at"],
        last_edited_by=row["last_edited_by"],
        last_edited_by_name=editor_name,
        source=row["source"],
    )


@router.put(
    "/zones/{zone_name}/encounters/{position}/strategy",
    response_model=StrategyResponse,
)
async def put_strategy(
    zone_name: str,
    position: int,
    body: StrategyUpdateRequest,
    user: SessionUser = Depends(require_editor),
) -> StrategyResponse:
    """Replace the encounter's strategy. Records a revision automatically.

    Gated by ``require_editor`` — admin or contributor. A future per-zone
    authz rule can swap in here without touching the rest of the route.
    """
    if not body.markdown.strip():
        raise HTTPException(status_code=400, detail="markdown body is empty")

    resolved = await run_sync(_resolve_curator_encounter, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    canonical_zone, encounter_name = resolved

    # Pull expansion_short from zones.db for the lazy raid_zones row creation.
    z = await run_sync(zones_db.find_by_name, canonical_zone)
    expansion_short = z["expansion_short"] if z else "Unknown"

    row = await run_sync(
        lambda: _write_strategy_sync(
            zone_name=canonical_zone,
            encounter_name=encounter_name,
            position=position,
            markdown=body.markdown,
            edit_note=body.edit_note,
            editor_discord_id=user["id"],
            expansion_short=expansion_short,
        ),
    )

    last_edited_by = row.get("last_edited_by")
    editor_name = await _resolve_editor_name(last_edited_by)
    audit_log(
        "raid_strategy_edited",
        actor=user["id"],
        zone=canonical_zone,
        encounter=encounter_name,
        position=position,
        body_len=len(body.markdown),
    )
    return StrategyResponse(
        zone_name=canonical_zone,
        encounter_name=encounter_name,
        position=position,
        markdown=row.get("strategy_md") or body.markdown,
        last_edited_at=row.get("last_edited_at"),
        last_edited_by=last_edited_by,
        last_edited_by_name=editor_name,
        source=row.get("source") or raids_db.SOURCE_MANUAL,
    )


@router.get(
    "/zones/{zone_name}/encounters/{position}/strategy/revisions",
    response_model=RevisionListResponse,
)
async def get_strategy_revisions(zone_name: str, position: int) -> RevisionListResponse:
    """Return the full revision history for one encounter's strategy, newest
    first. Public read (matches the strategy GET endpoint's visibility).

    Empty list when no strategy has ever been written for this encounter —
    the frontend disclosure just shows "no history yet" in that case rather
    than 404-ing, which would be confusing on a valid encounter."""
    resolved = await run_sync(_resolve_curator_encounter, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    canonical_zone, encounter_name = resolved

    rows = await run_sync(_read_revisions_sync, canonical_zone, encounter_name)

    # Batch-resolve display names for all unique editor ids in one DB call.
    unique_ids = list({r["edited_by"] for r in rows if r.get("edited_by")})
    names = await users_db.get_display_names_for_discord_ids(unique_ids)

    return RevisionListResponse(
        zone_name=canonical_zone,
        encounter_name=encounter_name,
        position=position,
        revisions=[
            RevisionEntry(
                id=r["id"],
                edited_at=r["edited_at"],
                edited_by=r["edited_by"],
                edited_by_name=names.get(r["edited_by"]),
                before_md=r["before_md"],
                after_md=r["after_md"],
                edit_note=r["edit_note"],
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# Zone-level overview
# ---------------------------------------------------------------------------


def _read_zone_revisions_sync(zone_name: str) -> list[dict]:
    """All revision rows for a zone's overview, newest first.

    Returns [] if the zone has no raid_zones row OR no revisions yet."""
    if not raids_db.DB_PATH.exists():
        return []
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        zrow = conn.execute(
            "SELECT id FROM raid_zones WHERE zone_name_lower = ?",
            (zone_name.lower(),),
        ).fetchone()
        if zrow is None:
            return []
    return raids_db.list_zone_revisions(zrow["id"])


async def _resolve_canonical_zone_name(zone_name: str) -> str | None:
    """Resolve an URL zone name (possibly an alias) to its canonical form via
    zones.db. Returns None when the zone is unknown — caller 404s."""
    z = await run_sync(zones_db.find_by_name, zone_name)
    return z["name"] if z else None


@router.get("/zones/{zone_name}/overview", response_model=ZoneOverviewResponse)
async def get_zone_overview(zone_name: str) -> ZoneOverviewResponse:
    """Fetch the zone's overview markdown. 404 when the zone is unknown OR no
    overview is written yet — both states resolve to the same UI placeholder."""
    canonical = await _resolve_canonical_zone_name(zone_name)
    if canonical is None:
        raise HTTPException(status_code=404, detail="Zone not found")

    row = await run_sync(_read_overview_sync, canonical)
    if row is None:
        raise HTTPException(status_code=404, detail="No overview yet")

    editor_name = await _resolve_editor_name(row["last_edited_by"])
    return ZoneOverviewResponse(
        zone_name=canonical,
        markdown=row["overview_md"] or "",
        last_edited_at=row["last_edited_at"],
        last_edited_by=row["last_edited_by"],
        last_edited_by_name=editor_name,
        source=row["source"],
    )


@router.put("/zones/{zone_name}/overview", response_model=ZoneOverviewResponse)
async def put_zone_overview(
    zone_name: str,
    body: ZoneOverviewUpdateRequest,
    user: SessionUser = Depends(require_editor),
) -> ZoneOverviewResponse:
    """Replace the zone's overview markdown. Same editor gate as the
    encounter strategy editor. Does NOT touch access_md or background_md."""
    if not body.markdown.strip():
        raise HTTPException(status_code=400, detail="markdown body is empty")

    canonical = await _resolve_canonical_zone_name(zone_name)
    if canonical is None:
        raise HTTPException(status_code=404, detail="Zone not found")

    # expansion_short needed for the lazy-create branch in the write helper.
    z = await run_sync(zones_db.find_by_name, canonical)
    expansion_short = z["expansion_short"] if z else "Unknown"

    row = await run_sync(
        lambda: _write_overview_sync(
            zone_name=canonical,
            markdown=body.markdown,
            editor_discord_id=user["id"],
            expansion_short=expansion_short,
            edit_note=body.edit_note,
        ),
    )

    last_edited_by = row.get("last_edited_by")
    editor_name = await _resolve_editor_name(last_edited_by)
    audit_log(
        "raid_zone_overview_edited",
        actor=user["id"],
        zone=canonical,
        body_len=len(body.markdown),
    )
    return ZoneOverviewResponse(
        zone_name=canonical,
        markdown=row.get("overview_md") or body.markdown,
        last_edited_at=row.get("last_edited_at"),
        last_edited_by=last_edited_by,
        last_edited_by_name=editor_name,
        source=row.get("source") or raids_db.SOURCE_MANUAL,
    )


@router.get(
    "/zones/{zone_name}/overview/revisions",
    response_model=ZoneRevisionListResponse,
)
async def get_zone_overview_revisions(zone_name: str) -> ZoneRevisionListResponse:
    """Return the full revision history for a zone's overview, newest first.
    Public read (same auth as the overview GET).

    Empty list when no overview has ever been written — the frontend
    disclosure shows "no history yet" rather than 404-ing."""
    canonical = await _resolve_canonical_zone_name(zone_name)
    if canonical is None:
        raise HTTPException(status_code=404, detail=f"Unknown zone: {zone_name!r}")

    rows = await run_sync(_read_zone_revisions_sync, canonical)

    # Batch-resolve display names for all unique editor ids in one DB call.
    editor_ids = list({r["edited_by"] for r in rows if r.get("edited_by")})
    names = await users_db.get_display_names_for_discord_ids(editor_ids)

    return ZoneRevisionListResponse(
        zone_name=canonical,
        revisions=[
            RevisionEntry(
                id=r["id"],
                edited_at=r["edited_at"],
                edited_by=r["edited_by"],
                edited_by_name=names.get(r["edited_by"]),
                before_md=r["before_md"],
                after_md=r["after_md"],
                edit_note=r["edit_note"],
            )
            for r in rows
        ],
    )
