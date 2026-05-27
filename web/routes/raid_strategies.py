"""
GET  /api/zones/{zone}/encounters/{position}/strategy
PUT  /api/zones/{zone}/encounters/{position}/strategy             (officer/admin)
GET  /api/zones/{zone}/encounters/{position}/strategy/revisions   (history)

Read-write surface for per-encounter raid strategy markdown. Strategy bodies
live in ``census/raids_db.py`` (separate SQLite file from the zones DB); the
revision history is recorded automatically by ``upsert_raid_encounter`` so this
route doesn't have to think about it on the write path — only surface it on
the read path via the ``/revisions`` endpoint.

Key translation: the URL identifies a curator encounter by ``(zone_name,
position)`` (matches the sidebar URLs in the React app). We resolve those via
``zones.db`` → ``zone_encounters.encounter_name`` and use that string as the
raids_db row's ``mob_name`` — one strategy per curator encounter, keyed by the
display name. Group encounters get a single strategy under their joined name.

Lazy zone creation: a PUT for a zone not yet known to raids_db creates the
``raid_zones`` row on the fly, pulling ``expansion_short`` from zones.db.
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from census import raids_db, zones_db
from web.auth_deps import is_admin, require_user_session
from web.cache import character_cache
from web.config import WORLD as _WORLD
from web.db import get_active_claims
from web.routes.guild import _officer_chars

router = APIRouter(tags=["raid_strategies"])


# ---------------------------------------------------------------------------
# Auth — officer-or-admin
# ---------------------------------------------------------------------------


async def _resolve_primary_guild_cached(discord_id: str) -> str | None:
    """Return the cached guild for this user's primary character, or None.

    Cache-only (no Census fallback) — kept hot so the auth check stays cheap.
    A cold cache returns None and the caller 403s; visiting the character
    page once warms it. Mirrors the cheap branch of zones.py's resolver."""
    claims = await get_active_claims(discord_id)
    primary = next((c for c in claims["approved"] if c.get("is_primary")), None)
    if not primary:
        return None
    char_name = primary["character_name"]
    cached, _ = character_cache.get_stale(f"{char_name.lower()}:{_WORLD.lower()}")
    if cached is None:
        return None
    return getattr(cached, "guild_name", None) or (cached.get("guild_name") if isinstance(cached, dict) else None)


async def require_officer_or_admin(request: Request) -> dict:
    """Strategy-write gate.

    Allows the request through if the session user is either:
      * in ``ADMIN_DISCORD_IDS``, or
      * a recognised officer (rank in ``_OFFICER_RANKS``) in their primary
        character's guild.

    Future extension point: a "contributor" tag stored on the user row (or a
    third claim status) would slot in as a third allowed branch here without
    touching the route itself.
    """
    user = require_user_session(request)
    if is_admin(user):
        return user

    discord_id = user["id"]
    guild_name = await _resolve_primary_guild_cached(discord_id)
    if not guild_name:
        # No primary character, or cache is cold — fail closed. Visiting
        # /character/<name> once warms the cache for subsequent calls.
        raise HTTPException(status_code=403, detail="Strategy editing requires officer rank or admin.")

    officer_chars = await _officer_chars(discord_id, guild_name)
    if not officer_chars:
        raise HTTPException(status_code=403, detail="Strategy editing requires officer rank or admin.")
    return user


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
    before_md: str | None
    after_md: str
    edit_note: str | None


class RevisionListResponse(BaseModel):
    zone_name: str
    encounter_name: str
    position: int
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
    loop = asyncio.get_event_loop()
    resolved = await loop.run_in_executor(None, _resolve_curator_encounter, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    canonical_zone, encounter_name = resolved

    row = await loop.run_in_executor(None, _read_strategy_sync, canonical_zone, encounter_name)
    if row is None:
        raise HTTPException(status_code=404, detail="No strategy yet")

    return StrategyResponse(
        zone_name=canonical_zone,
        encounter_name=encounter_name,
        position=position,
        markdown=row["strategy_md"] or "",
        last_edited_at=row["last_edited_at"],
        last_edited_by=row["last_edited_by"],
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
    user: dict = Depends(require_officer_or_admin),
) -> StrategyResponse:
    """Replace the encounter's strategy. Records a revision automatically.

    Gated to admins and recognised officers (see ``require_officer_or_admin``).
    A future per-zone authz rule can swap in here without touching the rest
    of the route.
    """
    if not body.markdown.strip():
        raise HTTPException(status_code=400, detail="markdown body is empty")

    loop = asyncio.get_event_loop()
    resolved = await loop.run_in_executor(None, _resolve_curator_encounter, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    canonical_zone, encounter_name = resolved

    # Pull expansion_short from zones.db for the lazy raid_zones row creation.
    z = await loop.run_in_executor(None, zones_db.find_by_name, canonical_zone)
    expansion_short = z["expansion_short"] if z else "Unknown"

    row = await loop.run_in_executor(
        None,
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

    return StrategyResponse(
        zone_name=canonical_zone,
        encounter_name=encounter_name,
        position=position,
        markdown=row.get("strategy_md") or body.markdown,
        last_edited_at=row.get("last_edited_at"),
        last_edited_by=row.get("last_edited_by"),
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
    loop = asyncio.get_event_loop()
    resolved = await loop.run_in_executor(None, _resolve_curator_encounter, zone_name, position)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    canonical_zone, encounter_name = resolved

    rows = await loop.run_in_executor(None, _read_revisions_sync, canonical_zone, encounter_name)
    return RevisionListResponse(
        zone_name=canonical_zone,
        encounter_name=encounter_name,
        position=position,
        revisions=[
            RevisionEntry(
                id=r["id"],
                edited_at=r["edited_at"],
                edited_by=r["edited_by"],
                before_md=r["before_md"],
                after_md=r["after_md"],
                edit_note=r["edit_note"],
            )
            for r in rows
        ],
    )
