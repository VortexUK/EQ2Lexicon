from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from census import census_store
from census.client import CensusClient
from census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from census.models import CharacterOverview, GuildData, SpellEntry
from census.spells_db import (
    DB_PATH as _SPELLS_DB,
)
from census.spells_db import (
    find_by_ids as _spell_find_by_ids,
)
from census.spells_db import (
    load_blocklist as _load_spell_blocklist,
)
from census.spells_db import (
    strip_roman as _strip_roman,
)
from census.spells_db import (
    unique_highest_entries as _unique_highest,
)
from web.cache import character_cache, guild_cache
from web.config import SERVICE_ID as _SERVICE_ID
from web.db import DB_PATH as _USERS_DB_PATH
from web.db import get_active_claims
from web.limiter import limiter
from web.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["guild"])


def _scrub(value: object) -> str:
    """Strip CR/LF before logging a user-supplied value, so a crafted name
    can't forge log lines (CWE-117 log injection)."""
    return str(value).replace("\r", " ").replace("\n", " ")


_OFFICER_RANKS = frozenset({0, 1})  # rank_ids that count as "officer"

# Guild name validation: EQ2 guild names are letters, digits, spaces, hyphens,
# apostrophes — max 64 characters.  Reject anything else early.
_GUILD_NAME_MAX = 64


def _validate_guild_name(guild_name: str) -> None:
    """Raise 400 if guild_name looks malformed or dangerously long."""
    if not guild_name or len(guild_name) > _GUILD_NAME_MAX:
        raise HTTPException(status_code=400, detail="Invalid guild name length (max 64 characters).")
    if not re.fullmatch(r"[A-Za-z0-9 '\-]+", guild_name):
        raise HTTPException(status_code=400, detail="Guild name contains invalid characters.")


# Slots whose adornments are excluded from the adorn check (same as character page)
_SKIP_SLOTS = frozenset({"ammo", "event slot", "mount adornment", "mount armor"})

# Canonical adorn-colour display order
_COLOUR_ORDER = ["White", "Yellow", "Red", "Blue", "Turquoise", "Green", "Orange", "Purple"]


# ---------------------------------------------------------------------------
# Models — roster
# ---------------------------------------------------------------------------


class GuildInfoResponse(BaseModel):
    name: str
    world: str
    dateformed: int | None = None
    description: str | None = None
    alignment: int | str | None = None  # Census returns an int (0/1/2) or None
    type: int | str | None = None  # Census may return an int here too
    level: int | None = None
    members: int | None = None
    accounts: int | None = None
    achievement_count: int = 0
    fetched_at: int | None = None
    stale: bool = False


class GuildMemberResponse(BaseModel):
    name: str
    level: int | None = None
    cls: str | None = None
    ts_class: str | None = None
    ts_level: int | None = None
    aa_level: int | None = None
    ilvl: float | None = None  # average gear item level
    deity: str | None = None
    rank: str | None = None
    rank_id: int | None = None
    guild_status: int | None = None  # status points contributed to the guild
    played_time: int | None = None  # total /played seconds


class GuildResponse(BaseModel):
    name: str
    world: str
    members: list[GuildMemberResponse]
    fetched_at: int | None = None
    stale: bool = False


# ---------------------------------------------------------------------------
# Models — spell check
# ---------------------------------------------------------------------------


class MemberSpellTiers(BaseModel):
    name: str
    rank: str | None = None
    rank_id: int | None = None
    tiers: dict[str, int]  # tier_name → count  (all _TIER_ORDER keys present)
    total: int
    spell_names: dict[str, list[str]] = {}  # tier_name → spell names, sorted by level desc


class GuildSpellCheckResponse(BaseModel):
    guild_name: str
    world: str
    tiers: list[str]  # ordered list of tier columns that have any data
    members: list[MemberSpellTiers]


# ---------------------------------------------------------------------------
# Models — adorn check
# ---------------------------------------------------------------------------


class AdornColorStats(BaseModel):
    filled: int
    total: int


class MemberAdornStats(BaseModel):
    name: str
    rank: str | None = None
    rank_id: int | None = None
    adorns: dict[str, AdornColorStats]  # colour → stats
    missing: dict[str, list[str]] = {}  # colour → slot names with empty adorn of that colour


class GuildAdornCheckResponse(BaseModel):
    guild_name: str
    world: str
    colors: list[str]  # ordered colour columns that appear in the data
    members: list[MemberAdornStats]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Shared guild fetch helper
# ---------------------------------------------------------------------------

# In-flight deduplication: one dict covers ALL guild endpoints (roster, info,
# spell-check, adorn-check) so any concurrent cache-miss fires only one Census call.
_guild_fetch_tasks: dict[str, asyncio.Task] = {}

# Background-refresh dedup: tracks which guilds already have a background
# refresh task scheduled, so concurrent stale responses don't spawn duplicates.
_guild_refresh_in_flight: set[str] = set()


async def _fetch_and_cache_guild(
    guild_name: str,
) -> tuple[GuildData, list, dict] | None:
    """
    Fetch full guild data via get_guild_full and atomically refresh every
    related cache key in one shot:

        roster:{guild}:{world}      — sorted member list
        info:{guild}:{world}        — guild metadata (no member list)
        {char_name}:{world}         — character_cache entry per member
        adorns:{guild}:{world}      — adornment check data
        spells:{guild}:{world}      — spell-tier check data

    Concurrent callers for the same guild share one in-flight Census request
    (thundering-herd guard, keyed on guild_name:world).

    Returns (GuildData, overviews, guild_info_dict) on success, None on
    network failure or guild-not-found.
    """
    task_key = f"{guild_name.lower()}:{current_world().lower()}"
    existing = _guild_fetch_tasks.get(task_key)
    if existing is not None and not existing.done():
        return await existing

    async def _do_fetch():
        world = current_world()
        # CENSUS-CLIENT-LIFECYCLE: migrate to web.lib.census_lifecycle.shared_census_client (Phase 2c.2)
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            full = await client.get_guild_full(guild_name, world)
        finally:
            await client.close()
        if not full or not full[0].members:
            return None

        guild_data, overviews, guild_info, roster_stubs = full
        world_lower = world.lower()
        guild_lower = guild_name.lower()
        member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

        # Cache the full member stubs (every member, resolved AND offline) so
        # _persist_and_publish_guild can build the best-known merged roster
        # without a second Census round-trip. The LIVE roster: cache below stays
        # resolved-only (unchanged).
        guild_cache.set(f"roster_stubs:{guild_lower}:{world_lower}", roster_stubs)

        # Info
        guild_cache.set(
            f"info:{guild_lower}:{world_lower}",
            GuildInfoResponse(**guild_info),
        )

        # Per-character overviews
        for ov in overviews:
            try:
                character_cache.set(
                    f"{ov.name.lower()}:{world_lower}",
                    _overview_to_char_response(ov),
                )
            except Exception:
                pass

        # Adorn + spell derived caches
        _prewarm_adorn_cache(
            f"adorns:{guild_lower}:{world_lower}",
            guild_data.name,
            overviews,
            member_rank,
        )
        _prewarm_spell_cache(
            f"spells:{guild_lower}:{world_lower}",
            guild_data.name,
            overviews,
            member_rank,
        )

        # Per-member average gear ilvl. Batch-fetch every equipped item's ilvl in
        # one items.db query (deduped), then map by name — avoids a DB hit per
        # member. Equipment lives on the CharacterOverview, not GuildMember.
        from census.db import gear_for_ids  # noqa: PLC0415
        from web.routes.character import (  # noqa: PLC0415 — local to avoid circular import
            _equipment_lookup_ids,
            _ilvl_from_gear,
        )

        all_ids = list({i for ov in overviews for i in _equipment_lookup_ids(ov.equipment)})
        gear = gear_for_ids(all_ids)
        ilvl_by_name = {ov.name.lower(): _ilvl_from_gear(ov.equipment, gear) for ov in overviews}

        # Roster (sorted by rank then level desc)
        members_sorted = sorted(
            guild_data.members,
            key=lambda m: (m.rank_id if m.rank_id is not None else 9999, -(m.level or 0)),
        )
        guild_cache.set(
            f"roster:{guild_lower}:{world_lower}",
            GuildResponse(
                name=guild_data.name,
                world=guild_data.world,
                members=[
                    GuildMemberResponse(
                        name=m.name,
                        level=m.level,
                        cls=m.cls,
                        ts_class=m.ts_class,
                        ts_level=m.ts_level,
                        aa_level=m.aa_level,
                        ilvl=ilvl_by_name.get(m.name.lower()),
                        deity=m.deity,
                        rank=m.rank,
                        rank_id=m.rank_id,
                        guild_status=m.guild_status,
                        played_time=m.played_time,
                    )
                    for m in members_sorted
                ],
            ),
        )
        # Return the 3-tuple (guild_data, overviews, guild_info) — every caller
        # (_roster_rank_map, spell-check, adorn-check, parses) unpacks 3 elements.
        # The 4th element (roster_stubs) is consumed via the roster_stubs: cache.
        return (guild_data, overviews, guild_info)

    task: asyncio.Task = asyncio.create_task(_do_fetch())
    _guild_fetch_tasks[task_key] = task
    try:
        return await task
    except Exception as exc:
        _log.error("[Cache] Guild fetch failed for %s: %s", guild_name, exc)
        return None
    finally:
        if _guild_fetch_tasks.get(task_key) is task:
            _guild_fetch_tasks.pop(task_key, None)


async def _roster_rank_map(guild_name: str) -> dict[str, int | None]:
    """
    Return {member_name_lower: rank_id} for a guild.
    Serves from the cached roster when available; on miss triggers a full
    guild fetch via _fetch_and_cache_guild (which also pre-warms every other
    cache key so the roster endpoint and character pages are all warm too).
    """
    roster, _ = guild_cache.get_stale(f"roster:{guild_name.lower()}:{current_world().lower()}")
    if roster is not None:
        return {m.name.lower(): m.rank_id for m in roster.members}
    full = await _fetch_and_cache_guild(guild_name)
    if not full:
        return {}
    guild_data, _, _ = full
    return {m.name.lower(): m.rank_id for m in guild_data.members}


async def _officer_chars(discord_id: str, guild_name: str) -> set[str]:
    """
    Return the set of this user's approved character names (lower-cased) that
    hold an officer rank (rank_id in _OFFICER_RANKS) in the named guild.
    Empty set means the user is not an officer of this guild.
    """
    claims_data = await get_active_claims(discord_id, world=current_world())
    approved = {c["character_name"].lower() for c in claims_data["approved"]}
    if not approved:
        return set()
    rank_map = await _roster_rank_map(guild_name)
    return {name for name in approved if rank_map.get(name) in _OFFICER_RANKS}


# ---------------------------------------------------------------------------
# Cache pre-warming helpers
# ---------------------------------------------------------------------------


def _prewarm_adorn_cache(
    cache_key: str,
    guild_name: str,
    overviews: list[CharacterOverview],
    member_rank: dict[str, tuple],
) -> None:
    """Build and store GuildAdornCheckResponse from already-parsed equipment data."""
    all_colours: set[str] = set()
    out_members: list[MemberAdornStats] = []

    for ov in overviews:
        colour_stats: dict[str, list[int]] = {}  # colour → [filled, total]
        missing_slots: dict[str, list[str]] = {}  # colour → slot names with empty adorn
        for eq_slot in ov.equipment:
            # Capitalise the slot name for display (e.g. "ring" → "Ring")
            slot_label = eq_slot.slot_name.title() if eq_slot.slot_name else "Unknown"
            for adorn_slot in eq_slot.adorn_slots:
                colour = adorn_slot.color
                if not colour:
                    continue
                filled = adorn_slot.adorn_id is not None
                if colour not in colour_stats:
                    colour_stats[colour] = [0, 0]
                if filled:
                    colour_stats[colour][0] += 1
                else:
                    missing_slots.setdefault(colour, []).append(slot_label)
                colour_stats[colour][1] += 1
                all_colours.add(colour)

        if not colour_stats:
            continue

        rank_label, rank_id = member_rank.get(ov.name, (None, None))
        out_members.append(
            MemberAdornStats(
                name=ov.name,
                rank=rank_label,
                rank_id=rank_id,
                adorns={c: AdornColorStats(filled=v[0], total=v[1]) for c, v in colour_stats.items()},
                missing=missing_slots,
            )
        )

    out_members.sort(
        key=lambda m: (
            member_rank.get(m.name, (None, 9999))[1] if member_rank.get(m.name, (None, None))[1] is not None else 9999,
            m.name,
        )
    )
    ordered_colours = [c for c in _COLOUR_ORDER if c in all_colours]
    ordered_colours += sorted(c for c in all_colours if c not in _COLOUR_ORDER)

    guild_cache.set(
        cache_key,
        GuildAdornCheckResponse(
            guild_name=guild_name,
            world=current_world(),
            colors=ordered_colours,
            members=out_members,
        ),
    )


def _build_spell_check_from_overviews(
    guild_name: str,
    guild_world: str,
    overviews: list[CharacterOverview],
    member_rank: dict[str, tuple],
) -> GuildSpellCheckResponse | None:
    """
    Build a GuildSpellCheckResponse from CharacterOverview.spell_ids using
    a single bulk lookup against the local spells DB.  Returns None if the
    DB is unavailable or no members had any spell IDs.
    """
    if not _SPELLS_DB.exists():
        return None

    # Collect all unique IDs across every member in one shot
    all_ids: list[int] = []
    for ov in overviews:
        all_ids.extend(ov.spell_ids)

    if not all_ids:
        return None

    # One DB query for the whole guild
    spell_db: dict[int, dict] = _spell_find_by_ids(list(set(all_ids)))
    blocklist = _load_spell_blocklist()

    out_members: list[MemberSpellTiers] = []
    tiers_with_data: set[str] = set()

    for ov in overviews:
        entries: list[SpellEntry] = []
        for sid in ov.spell_ids:
            row = spell_db.get(sid)
            if row is None:
                continue
            # Match the same filter as the character spell endpoint:
            # only spellscroll-granted spells are upgradable, and the blocklist applies.
            if (row.get("level") or 0) <= 0:
                continue
            if row.get("type") not in ("spells", "arts"):
                continue
            if row.get("given_by") != "spellscroll":
                continue
            if _strip_roman(row.get("name") or "").lower() in blocklist:
                continue
            entries.append(
                SpellEntry(
                    name=row["name"],
                    tier=row["tier_name"] or "Unknown",
                    spell_type=row["type"] or "",
                    level=row["level"] or 0,
                )
            )

        entries = _unique_highest(entries)
        count = Counter(e.tier for e in entries)
        tiers_with_data.update(count.keys())

        # Group names by tier, sorted by level descending
        names_by_tier: dict[str, list[tuple[int, str]]] = {}
        for e in entries:
            names_by_tier.setdefault(e.tier, []).append((e.level, e.name))
        spell_names = {
            tier: [n for _, n in sorted(pairs, key=lambda x: -x[0])] for tier, pairs in names_by_tier.items()
        }

        rank_label, rank_id = member_rank.get(ov.name, (None, None))
        out_members.append(
            MemberSpellTiers(
                name=ov.name,
                rank=rank_label,
                rank_id=rank_id,
                tiers={t: count.get(t, 0) for t in _TIER_ORDER},
                total=sum(count.values()),
                spell_names=spell_names,
            )
        )

    out_members.sort(
        key=lambda m: (
            member_rank.get(m.name, (None, 9999))[1] if member_rank.get(m.name, (None, None))[1] is not None else 9999,
            m.name,
        )
    )

    return GuildSpellCheckResponse(
        guild_name=guild_name,
        world=guild_world,
        tiers=[t for t in _TIER_ORDER if t in tiers_with_data],
        members=out_members,
    )


def _prewarm_spell_cache(
    cache_key: str,
    guild_name: str,
    overviews: list[CharacterOverview],
    member_rank: dict[str, tuple],
) -> None:
    """
    Build and store GuildSpellCheckResponse from CharacterOverview.spell_ids
    using the local spells DB.  No-op if DB is unavailable or IDs are empty.
    """
    result = _build_spell_check_from_overviews(guild_name, current_world(), overviews, member_rank)
    if result is not None:
        guild_cache.set(cache_key, result)


async def _bg_refresh_guild(guild_name: str) -> None:
    """Background task: re-fetch all guild data and refresh every related cache."""
    key = guild_name.lower()
    if key in _guild_refresh_in_flight:
        return
    _guild_refresh_in_flight.add(key)
    try:
        await _fetch_and_cache_guild(guild_name)
    finally:
        _guild_refresh_in_flight.discard(key)


async def _persist_and_publish_guild(guild_name: str, world: str | None = None) -> None:
    """Full guild refresh: fetch + warm the in-memory caches (existing behaviour),
    then build the BEST-KNOWN merged roster (resolved members this fetch + offline
    members carried forward with last-good data from the character store), persist
    that to census_store, upsert ONLY the freshly-resolved members into the
    character store, and publish an SSE roster event with the merged roster."""
    from web import census_events
    from web.census_refresh import _merge_roster  # local import — cycle avoidance

    world = world or current_world()
    await _fetch_and_cache_guild(guild_name)  # existing: warms roster/info/spells/adorns + char cache
    now = int(time.time())
    glower, wlower = guild_name.lower(), world.lower()
    roster, _ = guild_cache.get_stale(f"roster:{glower}:{wlower}")
    if roster is None:
        return
    info, _ = guild_cache.get_stale(f"info:{glower}:{wlower}")
    roster_stubs, _ = guild_cache.get_stale(f"roster_stubs:{glower}:{wlower}")
    if roster_stubs is None:
        roster_stubs = []

    # Members that resolved THIS fetch (the live, resolved-only roster).
    resolved_members = roster.model_dump()["members"]
    fresh_by_name: dict[str, dict] = {m["name"]: m for m in resolved_members}

    _member_fields = set(GuildMemberResponse.model_fields)

    conn = census_store.init_db(census_store.DB_PATH)
    try:
        # For each stub NOT resolved this fetch, pull last-good data from the store.
        stored_by_lower: dict[str, dict] = {}
        for stub in roster_stubs:
            sname = stub.get("name")
            if not sname or sname in fresh_by_name:
                continue
            rec = census_store.get_character(conn, sname, world)
            if rec is not None:
                stored_by_lower[sname.lower()] = rec["data"]

        merged = _merge_roster(roster_stubs, fresh_by_name, stored_by_lower)

        # Sort by the same key the live roster uses (rank then level desc).
        merged_sorted = sorted(
            merged,
            key=lambda m: (m["rank_id"] if m.get("rank_id") is not None else 9999, -(m.get("level") or 0)),
        )
        merged_response = GuildResponse(
            name=roster.name,
            world=roster.world,
            members=[GuildMemberResponse(**{k: v for k, v in m.items() if k in _member_fields}) for m in merged_sorted],
        )
        merged_data = merged_response.model_dump()

        blob = {"roster": merged_data, "info": info.model_dump() if info is not None else None}
        census_store.upsert_guild(conn, guild_name, world, blob, now=now)

        # Upsert ONLY the freshly-resolved members — carrying-forward a stored-only
        # member must NOT bump its last_resolved_at (that would mark stale data fresh).
        for m in fresh_by_name.values():
            if not m.get("name"):
                continue
            census_store.upsert_character(conn, m["name"], world, m, resolved=True, now=now)
    finally:
        conn.close()
    # SSE event carries the MERGED roster (that's what the guild page live-swaps):
    census_events.publish({"type": "guild", "key": f"guild:{glower}:{wlower}", "data": merged_data, "fetched_at": now})


def _overview_to_char_response(ov: CharacterOverview):  # → CharacterResponse
    """Convert a CharacterOverview into the shared CharacterResponse model.

    Uses a local import to avoid a module-level circular-import between guild
    and character routes.  Delegates entirely to _build_char_response so the
    two stay in sync (including spell_ids).
    """
    from web.routes.character import _build_char_response  # local to avoid circular import

    return _build_char_response(ov)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_name}/info", response_model=GuildInfoResponse)
@limiter.limit("10/minute")
async def get_guild_info(request: Request, guild_name: str) -> GuildInfoResponse:
    """
    Return lightweight guild metadata (no member list).
    The info cache is pre-warmed by any guild endpoint that calls
    _fetch_and_cache_guild, so this usually hits cache on first load.
    On a fresh cache hit returns immediately.  On a stale hit or full miss:
    checks the durable census_store (using the roster blob to derive member
    count), and only calls Census when the store is also empty and Census is up.
    """
    from web import census_health
    from web.census_refresh import request_guild_refresh

    _validate_guild_name(guild_name)
    info_key = f"info:{guild_name.lower()}:{current_world().lower()}"
    cached, is_stale = guild_cache.get_stale(info_key)
    if cached is not None and not is_stale:
        return cached
    # Fall through to the durable store (the stored blob is the roster shape;
    # derive a minimal GuildInfoResponse from it — name/world + member count).
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_guild(conn, guild_name, current_world())
    finally:
        conn.close()
    if rec is not None:
        age = int(time.time()) - rec["last_resolved_at"]
        stale = age > 900
        if stale:
            request_guild_refresh(guild_name)
        info_data = rec["data"].get("info")
        if info_data:
            info_resp = GuildInfoResponse(**{**info_data, "fetched_at": rec["last_resolved_at"], "stale": stale})
        else:
            # older/partial row without info — degrade gracefully to what the roster gives us
            roster_data = rec["data"]["roster"]
            info_resp = GuildInfoResponse(
                name=roster_data.get("name", guild_name),
                world=roster_data.get("world", current_world()),
                members=len(roster_data.get("members", [])),
                fetched_at=rec["last_resolved_at"],
                stale=stale,
            )
        guild_cache.set(info_key, info_resp)
        return info_resp
    # Never seen in the store.
    if census_health.is_down():
        raise HTTPException(
            status_code=503,
            detail=f"Guild '{guild_name}' not cached yet and Census is unavailable.",
        )
    try:
        await _persist_and_publish_guild(guild_name)
    except Exception as exc:
        _log.error("[guild] Live fetch failed for %s: %s", _scrub(guild_name), exc)
        raise HTTPException(
            status_code=503,
            detail=f"Census error while fetching guild '{guild_name}'.",
        ) from exc
    result, _ = guild_cache.get_stale(info_key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    return result


@router.get("/guild/{guild_name}", response_model=GuildResponse)
@limiter.limit("10/minute")
async def get_guild(request: Request, guild_name: str) -> GuildResponse:
    """
    Return the guild roster sorted by rank then level descending.
    On a fresh cache hit returns immediately.  On a stale hit or full miss:
    checks the durable census_store first (serving stored data if Census is
    down), and only calls Census when both the store is empty and Census is up.
    """
    from web import census_health
    from web.census_refresh import request_guild_refresh

    _validate_guild_name(guild_name)
    cache_key = f"roster:{guild_name.lower()}:{current_world().lower()}"
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None and not is_stale:
        return cached
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_guild(conn, guild_name, current_world())
    finally:
        conn.close()
    if rec is not None:
        age = int(time.time()) - rec["last_resolved_at"]
        stale = age > 900
        if stale:
            request_guild_refresh(guild_name)
        stored = rec["data"]
        roster_data = stored["roster"]
        resp = GuildResponse(**{**roster_data, "fetched_at": rec["last_resolved_at"], "stale": stale})
        guild_cache.set(cache_key, resp)
        return resp
    # Never seen in the store — need a live Census fetch.
    if census_health.is_down():
        raise HTTPException(
            status_code=503,
            detail=f"Guild '{guild_name}' not cached yet and Census is unavailable.",
        )
    try:
        await _persist_and_publish_guild(guild_name)
    except Exception as exc:
        _log.error("[guild] Live fetch failed for %s: %s", _scrub(guild_name), exc)
        raise HTTPException(
            status_code=503,
            detail=f"Census error while fetching guild '{guild_name}'.",
        ) from exc
    final_cached, _ = guild_cache.get_stale(cache_key)
    if final_cached is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    return final_cached


@router.get("/guild/{guild_name}/spell-check", response_model=GuildSpellCheckResponse)
@limiter.limit("10/minute")
async def guild_spell_check(request: Request, guild_name: str) -> GuildSpellCheckResponse:
    """
    Spell tier summary for every guild member.
    Responds instantly from cache; on miss triggers a full guild fetch
    (one Census call) that also warms roster/chars/adorns.
    Spell IDs are resolved locally — no per-character Census calls needed.
    """
    _validate_guild_name(guild_name)
    cache_key = f"spells:{guild_name.lower()}:{current_world().lower()}"
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            # Spawned within the request context: asyncio.create_task copies the
            # contextvar, so current_world() inside the task resolves to THIS
            # request's server even after the middleware resets it post-response.
            asyncio.create_task(_bg_refresh_guild(guild_name))
        return cached
    full = await _fetch_and_cache_guild(guild_name)
    if full is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    result, _ = guild_cache.get_stale(cache_key)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Spells database unavailable — run scripts/download_spells.py first.",
        )
    return result


@router.get("/guild/{guild_name}/adorn-check", response_model=GuildAdornCheckResponse)
@limiter.limit("10/minute")
async def guild_adorn_check(request: Request, guild_name: str) -> GuildAdornCheckResponse:
    """
    Adornment slot summary for every guild member.
    Responds instantly from cache; on miss triggers a full guild fetch
    (one Census call) that also warms roster/chars/spells.
    """
    _validate_guild_name(guild_name)
    cache_key = f"adorns:{guild_name.lower()}:{current_world().lower()}"
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            # Spawned within the request context: asyncio.create_task copies the
            # contextvar, so current_world() inside the task resolves to THIS
            # request's server even after the middleware resets it post-response.
            asyncio.create_task(_bg_refresh_guild(guild_name))
        return cached
    full = await _fetch_and_cache_guild(guild_name)
    if full is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    result, _ = guild_cache.get_stale(cache_key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No adorn data found for '{guild_name}'.")
    return result


# ---------------------------------------------------------------------------
# Guild name search (local DB)
# ---------------------------------------------------------------------------


class GuildNameResult(BaseModel):
    name: str


class GuildSearchResponse(BaseModel):
    results: list[GuildNameResult]
    total: int


@router.get("/guilds/search", response_model=GuildSearchResponse)
async def search_guilds(name: str = "") -> GuildSearchResponse:
    """
    Search guilds by name prefix on the configured world.
    Queries Census first; falls back to locally-tracked guilds (item-watch)
    if Census is unavailable.
    Requires at least 2 characters.
    """
    q = name.strip()
    if len(q) < 2:
        return GuildSearchResponse(results=[], total=0)
    if len(q) > 64:
        return GuildSearchResponse(results=[], total=0)

    # CENSUS-CLIENT-LIFECYCLE: migrate to web.lib.census_lifecycle.shared_census_client (Phase 2c.2)
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        raw = await client.search_guilds_by_name(q, current_world())
    except Exception:
        raw = []
    finally:
        await client.close()

    if raw:
        results = [GuildNameResult(name=r["name"]) for r in raw]
        return GuildSearchResponse(results=results, total=len(results))

    # Census failed — fall back to locally-tracked guilds in item_watch
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT DISTINCT guild_name
            FROM item_watch
            WHERE LOWER(guild_name) LIKE ?
            ORDER BY guild_name
            LIMIT 25
            """,
            (f"{q.lower()}%",),
        ) as cur:
            rows = await cur.fetchall()

    results = [GuildNameResult(name=r["guild_name"]) for r in rows]
    return GuildSearchResponse(results=results, total=len(results))
