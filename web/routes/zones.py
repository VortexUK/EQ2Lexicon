"""
GET /api/zones                 — list zones, default-filtered to raid_x4.
GET /api/zones/{name}          — single zone hydrated with its encounter roster.
GET /api/zones/progress        — per-zone kill progress for the signed-in user's
                                 primary-character guild.

Public reference data — the zones DB carries no user-supplied rows. Sourced from
the curated wiki dump at ``scripts/dev/eq2_zones.cleaned.json`` +
``scripts/dev/eq2_raid_bosses.review.txt`` and rebuilt with
``scripts/build_zones_db.py``.

Sync sqlite calls (``zones_db.list_by_expansion`` / ``find_by_name``) are
offloaded with ``run_in_executor`` so they don't block the event loop — same
pattern as ``recipes.py`` and ``classes.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from census import zones_db
from parses.db import DB_PATH as PARSES_DB_PATH
from web.auth_deps import require_user_session
from web.cache import character_cache
from web.db import get_active_claims
from web.server_context import current_world as _current_world

router = APIRouter(tags=["zones"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class EncounterMobResponse(BaseModel):
    mob_name: str
    position: int


class EncounterResponse(BaseModel):
    encounter_name: str
    position: int
    stage: str | None = None
    wiki_url: str | None = None
    mobs: list[EncounterMobResponse] = []


class ZoneResponse(BaseModel):
    name: str
    expansion_short: str
    expansion_name: str
    expansion_year: int | None = None
    types: list[str] = []
    aliases: list[str] = []
    wiki_url: str | None = None
    is_contested: bool = False
    is_instance: bool = False
    is_openworld: bool = False
    bosses: list[EncounterResponse] = []


class ZoneListResponse(BaseModel):
    expansion: str | None
    type: str | None
    zones: list[ZoneResponse]


class KilledEncounter(BaseModel):
    """A single curator-encounter the guild has cleared at least once.

    ``last_kill_id`` + ``last_kill_at`` are taken from the most recent winning
    parse whose title maps (via ``zone_encounter_mobs``) to this encounter —
    so a group encounter's last_kill is whichever-mob ACT happened to log
    most recently for that group fight.
    """

    encounter_name: str
    kill_count: int
    last_kill_id: int
    last_kill_at: int  # unix seconds, UTC


class RaidProgressResponse(BaseModel):
    """Per-zone kill progress for one guild.

    ``killed_encounters`` is a dict of ``zone_name → [KilledEncounter, …]``.
    Frontend joins on zone name; zones not present here are simply unkilled
    (the curator's total still comes from the zone-list endpoint, so progress
    can render 0/N for untouched zones).
    """

    guild_name: str | None
    character_name: str | None
    killed_encounters: dict[str, list[KilledEncounter]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(z: dict) -> ZoneResponse:
    return ZoneResponse(
        name=z["name"],
        expansion_short=z["expansion_short"],
        expansion_name=z["expansion_name"],
        expansion_year=z.get("expansion_year"),
        types=z.get("types", []),
        aliases=z.get("aliases", []),
        wiki_url=z.get("wiki_url"),
        is_contested=bool(z.get("is_contested")),
        is_instance=bool(z.get("is_instance")),
        is_openworld=bool(z.get("is_openworld")),
        bosses=[
            EncounterResponse(
                encounter_name=b["encounter_name"],
                position=b["position"],
                stage=b.get("stage"),
                wiki_url=b.get("wiki_url"),
                mobs=[EncounterMobResponse(**m) for m in b.get("mobs", [])],
            )
            for b in z.get("bosses", [])
        ],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/zones", response_model=ZoneListResponse)
async def list_zones(
    expansion: str | None = Query(None, description="Expansion short code (e.g. 'EoF', 'RoK')."),
    type: str | None = Query(  # noqa: A002 — query-param name matches the type-token concept
        "raid_x4",
        description="Zone type filter ('raid_x4', 'group', 'solo', …). Pass an empty string for all.",
    ),
) -> ZoneListResponse:
    """List zones filtered by expansion and/or type token.

    Defaults to ``type=raid_x4`` because the current UI consumer is the raid
    roster. Pass ``type=`` (empty) to disable filtering. Without ``expansion``
    the result spans every expansion.
    """
    # Empty string from the client means "no filter" — None to the DB helper.
    type_arg = type or None
    loop = asyncio.get_event_loop()
    if expansion:
        rows = await loop.run_in_executor(None, zones_db.list_by_expansion, expansion, type_arg)
    elif type_arg:
        rows = await loop.run_in_executor(None, zones_db.list_by_type, type_arg)
    else:
        # No filter at all is rarely what a UI wants — bail early to keep the
        # accidental "/api/zones" payload small. The frontend should always
        # filter by something.
        raise HTTPException(status_code=400, detail="Specify expansion or type")

    return ZoneListResponse(
        expansion=expansion,
        type=type_arg,
        zones=[_to_response(z) for z in rows],
    )


@router.get("/zones/progress", response_model=RaidProgressResponse)
async def get_progress(user: dict = Depends(require_user_session)) -> RaidProgressResponse:
    """Per-zone kill progress for the signed-in user's primary character's guild.

    Resolution chain (cheapest first):
      1. Primary character (``claims where is_primary=1``) → cached guild from
         character_cache. Avoids a Census round-trip on the hot path.
      2. If still unknown, fall back to the most recent parse-stamped guild for
         this uploader — the ingest pipeline (``_resolve_uploader_guild_async``)
         already resolved it once and froze it on the encounter row.
      3. Still null → empty progress (the user's guild can't be determined yet;
         hitting their character page once will warm the cache).

    Encounter matching is done against ``zone_encounter_mobs.mob_name_lower`` so
    a group-encounter kill (ACT logs one of the 2-4 mob names) still resolves to
    its parent encounter and rolls up into the zone count.
    """
    discord_id = user["id"]
    character_name, guild_name = await _resolve_primary_guild(discord_id)
    if not guild_name:
        return RaidProgressResponse(
            guild_name=None,
            character_name=character_name,
            killed_encounters={},
        )

    loop = asyncio.get_event_loop()
    killed = await loop.run_in_executor(None, _compute_progress_sync, guild_name)
    return RaidProgressResponse(
        guild_name=guild_name,
        character_name=character_name,
        killed_encounters=killed,
    )


@router.get("/zones/{name}", response_model=ZoneResponse)
async def get_zone(name: str) -> ZoneResponse:
    """Fetch a single zone by canonical name or alias. 404 on miss."""
    loop = asyncio.get_event_loop()
    z = await loop.run_in_executor(None, zones_db.find_by_name, name)
    if z is None:
        raise HTTPException(status_code=404, detail=f"Zone not found: {name}")
    return _to_response(z)


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def _resolve_primary_guild(discord_id: str) -> tuple[str | None, str | None]:
    """Return (primary_character_name, guild_name) for a user.

    Either value may be None — missing claim, missing primary flag, cold cache,
    or no recent parses all return ``(name_or_None, None)`` and the caller
    renders the "no progress data" state.
    """
    world = _current_world()
    data = await get_active_claims(discord_id, world=world)
    primary = next((c for c in data["approved"] if c.get("is_primary")), None)
    character_name = primary["character_name"] if primary else None

    # Cheap path: character_cache holds the resolved guild already if any page
    # has loaded the character recently (incl. the guild roster prewarm).
    if character_name:
        cached, _ = character_cache.get_stale(f"{character_name.lower()}:{world.lower()}")
        if cached is not None:
            guild = getattr(cached, "guild_name", None) or (
                cached.get("guild_name") if isinstance(cached, dict) else None
            )
            if guild:
                return character_name, guild

    # Fallback: the parses pipeline already resolved + froze the guild on every
    # encounter the user has uploaded. Most-recent wins (handles guild changes).
    guild = await asyncio.get_event_loop().run_in_executor(None, _most_recent_parsed_guild_sync, discord_id)
    return character_name, guild


def _most_recent_parsed_guild_sync(discord_id: str) -> str | None:
    """Most recent non-null guild_name this user has uploaded a parse for."""
    if not PARSES_DB_PATH.exists():
        return None
    with sqlite3.connect(PARSES_DB_PATH) as conn:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute(
            """
            SELECT guild_name FROM encounters
            WHERE uploaded_by = ? AND guild_name IS NOT NULL AND hidden_at IS NULL
            ORDER BY started_at DESC LIMIT 1
            """,
            (discord_id,),
        ).fetchone()
    return row[0] if row else None


def _compute_progress_sync(guild_name: str) -> dict[str, list[KilledEncounter]]:
    """Per-encounter progress for one guild.

    Returns ``{zone_name: [{encounter_name, kill_count, last_kill_id, last_kill_at}, …]}``.

    A "kill" is a row in ``parses.encounters`` with ``success_level=1`` and
    ``hidden_at IS NULL``. Each parse title is mapped to a curator-encounter via
    ``zone_encounter_mobs.mob_name_lower`` — solo bosses match directly, group
    encounters collapse (any one mob ACT logs counts the whole encounter as
    cleared). Aggregation happens in Python rather than SQL because the parses
    and zones data live in separate SQLite files and we'd rather avoid
    cross-DB ATTACH.
    """
    if not PARSES_DB_PATH.exists() or not zones_db.DB_PATH.exists():
        return {}

    # Pull every winning row for the guild as (id, title_lower, started_at).
    # We need the timestamp + id to surface "last kill" — a DISTINCT title pass
    # wouldn't be enough.
    with sqlite3.connect(PARSES_DB_PATH) as pconn:
        pconn.execute("PRAGMA query_only = ON")
        kills = [
            (row[0], row[1].lower(), row[2])
            for row in pconn.execute(
                """
                SELECT id, title, started_at FROM encounters
                WHERE guild_name = ? AND success_level = 1 AND hidden_at IS NULL
                """,
                (guild_name,),
            ).fetchall()
            if row[1]
        ]

    if not kills:
        return {}

    # Build a mob_lower → (zone, encounter) map for every mob we actually need.
    # SQLite's variable limit is 999 by default; chunk to stay well clear.
    title_set = {t for _, t, _ in kills}
    mob_to_enc: dict[str, tuple[str, str]] = {}
    with sqlite3.connect(zones_db.DB_PATH) as zconn:
        zconn.execute("PRAGMA query_only = ON")
        zconn.row_factory = sqlite3.Row
        titles_list = list(title_set)
        for i in range(0, len(titles_list), 900):
            chunk = titles_list[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = zconn.execute(
                f"""
                SELECT m.mob_name_lower AS mob_lower,
                       z.name           AS zone_name,
                       e.encounter_name AS encounter_name
                FROM zone_encounter_mobs m
                JOIN zone_encounters     e ON e.id = m.encounter_id
                JOIN zones               z ON z.id = e.zone_id
                WHERE m.mob_name_lower IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            for r in rows:
                mob_to_enc[r["mob_lower"]] = (r["zone_name"], r["encounter_name"])

    # Per-encounter aggregate: kill count + most-recent kill (id + timestamp).
    agg: dict[tuple[str, str], dict] = {}
    for enc_id, title_lower, started_at in kills:
        key = mob_to_enc.get(title_lower)
        if key is None:
            continue  # parse title doesn't map to any curator encounter — skip
        cur = agg.get(key)
        if cur is None:
            agg[key] = {"kill_count": 1, "last_kill_id": enc_id, "last_kill_at": started_at}
        else:
            cur["kill_count"] += 1
            if started_at > cur["last_kill_at"]:
                cur["last_kill_id"] = enc_id
                cur["last_kill_at"] = started_at

    # Bucket by zone, sort each zone's list for deterministic output.
    # Construct KilledEncounter directly so the typed return matches what
    # RaidProgressResponse expects (pyright won't infer a dict→model coercion).
    out: dict[str, list[KilledEncounter]] = {}
    for (zone, enc), info in agg.items():
        out.setdefault(zone, []).append(
            KilledEncounter(
                encounter_name=enc,
                kill_count=info["kill_count"],
                last_kill_id=info["last_kill_id"],
                last_kill_at=info["last_kill_at"],
            )
        )
    for zone_list in out.values():
        zone_list.sort(key=lambda k: k.encounter_name)

    return out
