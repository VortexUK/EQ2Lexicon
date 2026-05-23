from __future__ import annotations

import asyncio
import logging
from collections import Counter

_log = logging.getLogger(__name__)

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from census.client import CensusClient
from census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from census.models import CharacterOverview, SpellEntry
from census.spells_db import (
    DB_PATH as _SPELLS_DB,
    find_by_ids as _spell_find_by_ids,
    load_blocklist as _load_spell_blocklist,
    strip_roman as _strip_roman,
    unique_highest_entries as _unique_highest,
)
from web.cache import character_cache, guild_cache
from web.config import SERVICE_ID as _SERVICE_ID, WORLD as _WORLD
from web.db import DB_PATH as _USERS_DB_PATH, get_active_claims

router = APIRouter(tags=["guild"])

_OFFICER_RANKS = frozenset({0, 1})   # rank_ids that count as "officer"

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
    alignment: int | str | None = None   # Census returns an int (0/1/2) or None
    type: int | str | None = None        # Census may return an int here too
    level: int | None = None
    members: int | None = None
    accounts: int | None = None
    achievement_count: int = 0


class GuildMemberResponse(BaseModel):
    name: str
    level: int | None = None
    cls: str | None = None
    ts_class: str | None = None
    ts_level: int | None = None
    aa_level: int | None = None
    deity: str | None = None
    rank: str | None = None
    rank_id: int | None = None
    guild_status: int | None = None   # status points contributed to the guild
    played_time: int | None = None    # total /played seconds


class GuildResponse(BaseModel):
    name: str
    world: str
    members: list[GuildMemberResponse]


# ---------------------------------------------------------------------------
# Models — spell check
# ---------------------------------------------------------------------------

class MemberSpellTiers(BaseModel):
    name: str
    rank: str | None = None
    rank_id: int | None = None
    tiers: dict[str, int]              # tier_name → count  (all _TIER_ORDER keys present)
    total: int
    spell_names: dict[str, list[str]] = {}  # tier_name → spell names, sorted by level desc


class GuildSpellCheckResponse(BaseModel):
    guild_name: str
    world: str
    tiers: list[str]        # ordered list of tier columns that have any data
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
    colors: list[str]       # ordered colour columns that appear in the data
    members: list[MemberAdornStats]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# In-flight deduplication: prevents thundering-herd when multiple requests
# concurrently miss the roster cache for the same guild.
_roster_fetch_tasks: dict[str, "asyncio.Task[dict[str, int | None]]"] = {}


async def _roster_rank_map(guild_name: str) -> dict[str, int | None]:
    """
    Return {member_name_lower: rank_id} for a guild.
    Uses the cached roster when available; falls back to a Census call.
    The Census result is stored in guild_cache so subsequent calls skip Census.
    Concurrent cache-misses share a single in-flight fetch task (thundering-herd guard).
    """
    cache_key = f"roster:{guild_name.lower()}:{_WORLD.lower()}"
    roster, _ = guild_cache.get_stale(cache_key)
    if roster is not None:
        return {m.name.lower(): m.rank_id for m in roster.members}

    # Reuse any already-running fetch for this guild instead of firing a new one
    existing = _roster_fetch_tasks.get(cache_key)
    if existing is not None and not existing.done():
        return await existing

    async def _fetch() -> dict[str, int | None]:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            guild = await client.get_guild(guild_name, _WORLD)
        finally:
            await client.close()
        if not guild:
            return {}
        # Cache the result so the next call is served from memory
        guild_cache.set(cache_key, GuildResponse(
            name    = guild.name,
            world   = guild.world,
            members = [
                GuildMemberResponse(
                    name         = m.name,
                    level        = m.level,
                    cls          = m.cls,
                    ts_class     = m.ts_class,
                    ts_level     = m.ts_level,
                    aa_level     = m.aa_level,
                    deity        = m.deity,
                    rank         = m.rank,
                    rank_id      = m.rank_id,
                    guild_status = m.guild_status,
                    played_time  = m.played_time,
                )
                for m in guild.members
            ],
        ))
        return {m.name.lower(): m.rank_id for m in guild.members}

    task: asyncio.Task[dict[str, int | None]] = asyncio.create_task(_fetch())
    _roster_fetch_tasks[cache_key] = task
    try:
        return await task
    finally:
        # Clean up only our own task; a later task may have replaced it already
        if _roster_fetch_tasks.get(cache_key) is task:
            _roster_fetch_tasks.pop(cache_key, None)


async def _officer_chars(discord_id: str, guild_name: str) -> set[str]:
    """
    Return the set of this user's approved character names (lower-cased) that
    hold an officer rank (rank_id in _OFFICER_RANKS) in the named guild.
    Empty set means the user is not an officer of this guild.
    """
    claims_data = await get_active_claims(discord_id)
    approved = {c["character_name"].lower() for c in claims_data["approved"]}
    if not approved:
        return set()
    rank_map = await _roster_rank_map(guild_name)
    return {
        name for name in approved
        if rank_map.get(name) in _OFFICER_RANKS
    }


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
        colour_stats: dict[str, list[int]] = {}   # colour → [filled, total]
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
        out_members.append(MemberAdornStats(
            name    = ov.name,
            rank    = rank_label,
            rank_id = rank_id,
            adorns  = {c: AdornColorStats(filled=v[0], total=v[1]) for c, v in colour_stats.items()},
            missing = missing_slots,
        ))

    out_members.sort(key=lambda m: (
        member_rank.get(m.name, (None, 9999))[1]
        if member_rank.get(m.name, (None, None))[1] is not None else 9999,
        m.name,
    ))
    ordered_colours = [c for c in _COLOUR_ORDER if c in all_colours]
    ordered_colours += sorted(c for c in all_colours if c not in _COLOUR_ORDER)

    guild_cache.set(cache_key, GuildAdornCheckResponse(
        guild_name = guild_name,
        world      = _WORLD,
        colors     = ordered_colours,
        members    = out_members,
    ))


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
            entries.append(SpellEntry(
                name       = row["name"],
                tier       = row["tier_name"] or "Unknown",
                spell_type = row["type"] or "",
                level      = row["level"] or 0,
            ))

        entries = _unique_highest(entries)
        count = Counter(e.tier for e in entries)
        tiers_with_data.update(count.keys())

        # Group names by tier, sorted by level descending
        names_by_tier: dict[str, list[tuple[int, str]]] = {}
        for e in entries:
            names_by_tier.setdefault(e.tier, []).append((e.level, e.name))
        spell_names = {
            tier: [n for _, n in sorted(pairs, key=lambda x: -x[0])]
            for tier, pairs in names_by_tier.items()
        }

        rank_label, rank_id = member_rank.get(ov.name, (None, None))
        out_members.append(MemberSpellTiers(
            name        = ov.name,
            rank        = rank_label,
            rank_id     = rank_id,
            tiers       = {t: count.get(t, 0) for t in _TIER_ORDER},
            total       = sum(count.values()),
            spell_names = spell_names,
        ))

    out_members.sort(key=lambda m: (
        member_rank.get(m.name, (None, 9999))[1]
        if member_rank.get(m.name, (None, None))[1] is not None else 9999,
        m.name,
    ))

    return GuildSpellCheckResponse(
        guild_name = guild_name,
        world      = guild_world,
        tiers      = [t for t in _TIER_ORDER if t in tiers_with_data],
        members    = out_members,
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
    result = _build_spell_check_from_overviews(guild_name, _WORLD, overviews, member_rank)
    if result is not None:
        guild_cache.set(cache_key, result)


async def _bg_refresh_guild(guild_name: str) -> None:
    """
    Background task: re-fetch all guild data and update every related cache.
    Uses get_guild_full for equipment/stats/roster; spell IDs come from the
    same single Census call and are resolved against the local spells DB.
    Fired by any guild endpoint when its cached data is stale.
    """
    world_lower = _WORLD.lower()
    try:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            full = await client.get_guild_full(guild_name, _WORLD)
            if not full or not full[0].members:
                return
            guild_data, overviews, guild_info = full
        finally:
            await client.close()

        member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

        # Info cache — warmed from the same Census response, no extra round-trip
        guild_cache.set(f"info:{guild_name.lower()}:{world_lower}", GuildInfoResponse(**guild_info))

        # Per-character caches
        for ov in overviews:
            try:
                character_cache.set(f"{ov.name.lower()}:{world_lower}", _overview_to_char_response(ov))
            except Exception:
                pass

        # Adorn cache
        _prewarm_adorn_cache(f"adorns:{guild_name.lower()}:{world_lower}", guild_name, overviews, member_rank)

        # Spell cache — resolved from local DB using IDs already in the guild response
        _prewarm_spell_cache(f"spells:{guild_name.lower()}:{world_lower}", guild_data.name, overviews, member_rank)

        # Roster cache
        members_sorted = sorted(
            guild_data.members,
            key=lambda m: (m.rank_id if m.rank_id is not None else 9999, -(m.level or 0)),
        )
        guild_cache.set(f"roster:{guild_name.lower()}:{world_lower}", GuildResponse(
            name    = guild_data.name,
            world   = guild_data.world,
            members = [
                GuildMemberResponse(
                    name=m.name, level=m.level, cls=m.cls,
                    ts_class=m.ts_class, ts_level=m.ts_level,
                    aa_level=m.aa_level, deity=m.deity,
                    rank=m.rank, rank_id=m.rank_id,
                    guild_status=m.guild_status, played_time=m.played_time,
                )
                for m in members_sorted
            ],
        ))
    except Exception as exc:
        _log.error("[Cache] Background guild refresh failed for %s: %s", guild_name, exc)


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
async def get_guild_info(guild_name: str) -> GuildInfoResponse:
    """
    Return lightweight guild metadata (no member list).
    The info cache is pre-warmed whenever GET /guild/{name} is called, so on
    first-load where both endpoints are hit simultaneously only one Census call
    fires.  This endpoint falls back to its own Census call only when neither
    cache is populated yet.
    """
    cache_key = f"info:{guild_name.lower()}:{_WORLD.lower()}"
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            async def _bg_refresh_info(gn: str, ck: str) -> None:
                try:
                    c = CensusClient(service_id=_SERVICE_ID)
                    try:
                        info = await c.get_guild_info(gn, _WORLD)
                    finally:
                        await c.close()
                    if info:
                        guild_cache.set(ck, GuildInfoResponse(**info))
                except Exception as exc:
                    _log.error("[Cache] Background guild info refresh failed for %s: %s", gn, exc)
            asyncio.create_task(_bg_refresh_info(guild_name, cache_key))
        return cached

    client = CensusClient(service_id=_SERVICE_ID)
    try:
        info = await client.get_guild_info(guild_name, _WORLD)
        if not info:
            raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {_WORLD}.")
    finally:
        await client.close()
    result = GuildInfoResponse(**info)
    guild_cache.set(cache_key, result)
    return result


@router.get("/guild/{guild_name}", response_model=GuildResponse)
async def get_guild(guild_name: str) -> GuildResponse:
    """
    Return the guild roster for the named guild.
    Sorted by rank then level descending.  Only members with census data.
    Also pre-warms the per-character cache from the single enriched guild call
    so that subsequent /character lookups are served from cache instantly.
    """
    if len(guild_name) > 64:
        raise HTTPException(status_code=400, detail="Guild name is too long")
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        cache_key = f"roster:{guild_name.lower()}:{_WORLD.lower()}"
        cached, is_stale = guild_cache.get_stale(cache_key)
        if cached is not None:
            if is_stale:
                asyncio.create_task(_bg_refresh_guild(guild_name))
            return cached
        full = await client.get_guild_full(guild_name, _WORLD)
        if not full or not full[0].members:
            raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {_WORLD}.")
        guild_data, overviews, guild_info = full
    finally:
        await client.close()

    world_lower = _WORLD.lower()
    member_rank: dict[str, tuple[str | None, int | None]] = {
        m.name: (m.rank, m.rank_id) for m in guild_data.members
    }

    # Pre-warm info cache from the same Census response so /guild/{name}/info
    # doesn't need its own Census call on first load.
    guild_cache.set(f"info:{guild_name.lower()}:{world_lower}", GuildInfoResponse(**guild_info))

    # Populate character, adorn, and spell caches from the single API response
    for ov in overviews:
        try:
            character_cache.set(f"{ov.name.lower()}:{world_lower}", _overview_to_char_response(ov))
        except Exception as exc:
            _log.warning("[Cache] Failed to pre-warm character %s: %s", ov.name, exc)
    _prewarm_adorn_cache(f"adorns:{guild_name.lower()}:{world_lower}", guild_name, overviews, member_rank)
    _prewarm_spell_cache(f"spells:{guild_name.lower()}:{world_lower}", guild_name, overviews, member_rank)

    members = sorted(
        guild_data.members,
        key=lambda m: (m.rank_id if m.rank_id is not None else 9999, -(m.level or 0)),
    )
    result = GuildResponse(
        name=guild_data.name,
        world=guild_data.world,
        members=[
            GuildMemberResponse(
                name=m.name, level=m.level, cls=m.cls,
                ts_class=m.ts_class, ts_level=m.ts_level,
                aa_level=m.aa_level, deity=m.deity,
                rank=m.rank, rank_id=m.rank_id,
                guild_status=m.guild_status, played_time=m.played_time,
            )
            for m in members
        ],
    )
    guild_cache.set(cache_key, result)
    return result


@router.get("/guild/{guild_name}/spell-check", response_model=GuildSpellCheckResponse)
async def guild_spell_check(guild_name: str) -> GuildSpellCheckResponse:
    """
    Spell tier summary for every guild member.
    Responds instantly from cache; fires a full guild background refresh when stale.
    On cache miss, uses get_guild_full (warms adorns + characters) then resolves
    spell IDs against the local spells DB — no per-character Census calls needed.
    """
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        cache_key = f"spells:{guild_name.lower()}:{_WORLD.lower()}"
        cached, is_stale = guild_cache.get_stale(cache_key)
        if cached is not None:
            if is_stale:
                asyncio.create_task(_bg_refresh_guild(guild_name))
            return cached

        # Cache miss — one Census call gets roster + equipment + spell IDs
        full = await client.get_guild_full(guild_name, _WORLD)
        if not full or not full[0].members:
            raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {_WORLD}.")
        guild_data, overviews, guild_info = full
    finally:
        await client.close()

    world_lower = _WORLD.lower()
    member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

    # Pre-warm info cache (avoids a separate Census call for /guild/{name}/info)
    guild_cache.set(f"info:{guild_name.lower()}:{world_lower}", GuildInfoResponse(**guild_info))

    # Warm character + adorn caches from the guild data we already have
    for ov in overviews:
        try:
            character_cache.set(f"{ov.name.lower()}:{world_lower}", _overview_to_char_response(ov))
        except Exception:
            pass
    _prewarm_adorn_cache(f"adorns:{guild_name.lower()}:{world_lower}", guild_name, overviews, member_rank)

    # Build spell response from local DB (single bulk lookup — no Census calls)
    result = _build_spell_check_from_overviews(guild_data.name, guild_data.world, overviews, member_rank)
    if result is None:
        raise HTTPException(status_code=503, detail="Spells database unavailable — run scripts/download_spells.py first.")

    guild_cache.set(cache_key, result)
    return result


@router.get("/guild/{guild_name}/adorn-check", response_model=GuildAdornCheckResponse)
async def guild_adorn_check(guild_name: str) -> GuildAdornCheckResponse:
    """
    Adornment slot summary for every guild member.
    Responds instantly from cache; fires a full guild background refresh when stale.
    On cache miss, uses the guild-full resolve so spells/roster are warmed simultaneously.
    """
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        cache_key = f"adorns:{guild_name.lower()}:{_WORLD.lower()}"
        cached, is_stale = guild_cache.get_stale(cache_key)
        if cached is not None:
            if is_stale:
                asyncio.create_task(_bg_refresh_guild(guild_name))
            return cached
        # Cache miss — fetch via get_guild_full so we warm characters simultaneously
        full = await client.get_guild_full(guild_name, _WORLD)
        if not full or not full[0].members:
            raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {_WORLD}.")
        guild_data, overviews, guild_info = full
    finally:
        await client.close()

    world_lower = _WORLD.lower()
    member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

    # Pre-warm info cache (avoids a separate Census call for /guild/{name}/info)
    guild_cache.set(f"info:{guild_name.lower()}:{world_lower}", GuildInfoResponse(**guild_info))

    # Warm character caches from the data we already have
    for ov in overviews:
        try:
            character_cache.set(f"{ov.name.lower()}:{world_lower}", _overview_to_char_response(ov))
        except Exception:
            pass

    # Build + cache the adorn response
    _prewarm_adorn_cache(cache_key, guild_name, overviews, member_rank)
    result = guild_cache.get_stale(cache_key)[0]
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

    client = CensusClient(service_id=_SERVICE_ID)
    try:
        raw = await client.search_guilds_by_name(q, _WORLD)
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
