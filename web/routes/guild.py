from __future__ import annotations

import asyncio
import os
import re
from collections import Counter

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from census.client import CensusClient
from census.models import CharacterOverview, SpellEntry
from census.spells_db import DB_PATH as _SPELLS_DB, find_by_ids as _spell_find_by_ids
from web.cache import character_cache, guild_cache
from web.db import (
    add_item_watch, get_active_claims, get_claim_by_id,
    list_claims, list_item_watches, remove_item_watch,
    review_claim, update_item_watch_check,
)
from web.routes.claim import _refresh_claim_cache

router = APIRouter(tags=["guild"])

_SERVICE_ID  = os.getenv("CENSUS_SERVICE_ID", "example")
_WORLD       = os.getenv("EQ2_WORLD", "Varsoon")
_OFFICER_RANKS = frozenset({0, 1})   # rank_ids that count as "officer"

# Slots whose adornments are excluded from the adorn check (same as character page)
_SKIP_SLOTS = frozenset({"ammo", "event slot", "mount adornment", "mount armor"})

# Canonical adorn-colour display order
_COLOUR_ORDER = ["White", "Yellow", "Red", "Blue", "Turquoise", "Green", "Orange", "Purple"]

# Spell tier order (lowest → highest)
_TIER_ORDER = ["Apprentice", "Journeyman", "Adept", "Expert", "Master", "Grandmaster"]

# Matches trailing Roman numeral suffix so we can deduplicate spell names
_ROMAN_SUFFIX = re.compile(
    r'\s+(?:XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X'
    r'|IX|VIII|VII|VI|V|IV|III|II|I)$',
    re.IGNORECASE,
)


def _base_name(name: str) -> str:
    return _ROMAN_SUFFIX.sub("", name.strip())


def _unique_highest(entries: list[SpellEntry]) -> list[SpellEntry]:
    """For each base spell name, keep only the highest-level entry."""
    best: dict[tuple, SpellEntry] = {}
    for e in entries:
        key = (_base_name(e.name), e.spell_type)
        if key not in best or e.level > best[key].level:
            best[key] = e
    return list(best.values())


# ---------------------------------------------------------------------------
# Models — roster
# ---------------------------------------------------------------------------

class GuildInfoResponse(BaseModel):
    name: str
    world: str
    dateformed: int | None = None
    description: str | None = None
    alignment: str | None = None
    type: str | None = None
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
# Models — officer claim review
# ---------------------------------------------------------------------------

class GuildClaimItem(BaseModel):
    id: int
    discord_id: str
    discord_name: str
    avatar: str | None = None
    character_name: str
    requested_at: int
    is_own: bool = False   # True when this claim belongs to the requesting officer


class RejectNoteRequest(BaseModel):
    note: str | None = None


# ---------------------------------------------------------------------------
# Models — item watch
# ---------------------------------------------------------------------------

class ItemWatchEntry(BaseModel):
    id: int
    character_name: str
    item_id: int
    item_name: str
    added_by_name: str
    added_at: int
    first_seen_at: int | None = None
    last_seen_at: int | None = None
    last_checked_at: int | None = None

class AddItemWatchRequest(BaseModel):
    character_name: str
    item_name: str       # resolved server-side to item_id + canonical display name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def _roster_rank_map(guild_name: str) -> dict[str, int | None]:
    """
    Return {member_name_lower: rank_id} for a guild.
    Uses the cached roster when available; falls back to a Census call.
    """
    cache_key = f"roster:{guild_name.lower()}:{_WORLD.lower()}"
    roster, _ = guild_cache.get_stale(cache_key)
    if roster is not None:
        return {m.name.lower(): m.rank_id for m in roster.members}
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        guild = await client.get_guild(guild_name, _WORLD)
    finally:
        await client.close()
    if not guild:
        return {}
    return {m.name.lower(): m.rank_id for m in guild.members}


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

    out_members: list[MemberSpellTiers] = []
    tiers_with_data: set[str] = set()

    for ov in overviews:
        entries: list[SpellEntry] = []
        for sid in ov.spell_ids:
            row = spell_db.get(sid)
            if row is None:
                continue
            if not row.get("passes_spellcheck"):
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
            guild_data, overviews = full
        finally:
            await client.close()

        member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

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
                )
                for m in members_sorted
            ],
        ))
    except Exception as exc:
        print(f"[Cache] Background guild refresh failed for {guild_name}: {exc}")


def _overview_to_char_response(ov: CharacterOverview):  # → CharacterResponse
    """
    Convert an internal CharacterOverview (produced by get_guild_full) into the
    same CharacterResponse Pydantic model that the /character route caches.
    Importing inside the function avoids a module-level circular-import risk.
    """
    from web.routes.character import (  # local import to avoid circular imports
        AdornSlotResponse,
        CharacterResponse,
        EquipmentSlotResponse,
        _parse_stats,
    )
    return CharacterResponse(
        id        = ov.id,
        name      = ov.name,
        level     = ov.level,
        cls       = ov.cls,
        race      = ov.race,
        gender    = ov.gender,
        deity     = ov.deity,
        aa_count  = ov.aa_count,
        world     = ov.world,
        ts_class  = ov.ts_class,
        ts_level  = ov.ts_level,
        stats     = _parse_stats(ov.stats),
        equipment = [
            EquipmentSlotResponse(
                slot       = s.slot_name,
                name       = s.item_name,
                item_id    = s.item_id,
                icon_id    = s.icon_id,
                tier       = s.tier,
                adorn_slots = [
                    AdornSlotResponse(
                        color      = a.color,
                        adorn_name = a.adorn_name,
                        adorn_id   = a.adorn_id,
                    )
                    for a in s.adorn_slots
                ],
            )
            for s in ov.equipment
        ],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/guild/{guild_name}/info", response_model=GuildInfoResponse)
async def get_guild_info(guild_name: str) -> GuildInfoResponse:
    """Return lightweight guild metadata (no member list)."""
    client = CensusClient(service_id=_SERVICE_ID)
    try:
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
                        print(f"[Cache] Background guild info refresh failed for {gn}: {exc}")
                asyncio.create_task(_bg_refresh_info(guild_name, cache_key))
            return cached
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
        guild_data, overviews = full
    finally:
        await client.close()

    world_lower = _WORLD.lower()
    member_rank: dict[str, tuple[str | None, int | None]] = {
        m.name: (m.rank, m.rank_id) for m in guild_data.members
    }

    # Populate character, adorn, and spell caches from the single API response
    for ov in overviews:
        try:
            character_cache.set(f"{ov.name.lower()}:{world_lower}", _overview_to_char_response(ov))
        except Exception as exc:
            print(f"[Cache] Failed to pre-warm character {ov.name}: {exc}")
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
        guild_data, overviews = full
    finally:
        await client.close()

    world_lower = _WORLD.lower()
    member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

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
        guild_data, overviews = full
    finally:
        await client.close()

    world_lower = _WORLD.lower()
    member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

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
# Officer claim-review endpoints
# ---------------------------------------------------------------------------

@router.get("/guild/{guild_name}/officer-status")
async def get_officer_status(guild_name: str, request: Request) -> dict:
    """
    Return whether the current user holds an officer rank in this guild.
    Always returns 200 (unauthenticated / non-officer users get is_officer: false).
    """
    user = request.session.get("user")
    if not user:
        return {"is_officer": False}
    chars = await _officer_chars(user["id"], guild_name)
    return {"is_officer": bool(chars)}


@router.get("/guild/{guild_name}/claims", response_model=list[GuildClaimItem])
async def get_guild_claims(guild_name: str, request: Request) -> list[GuildClaimItem]:
    """
    List all pending claims for characters that are members of this guild.
    Requires the requesting user to be an officer (rank 0 or 1) of the guild.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    rank_map = await _roster_rank_map(guild_name)
    pending  = await list_claims(status="pending")

    return [
        GuildClaimItem(
            id             = c["id"],
            discord_id     = c["discord_id"],
            discord_name   = c["discord_name"],
            avatar         = c.get("avatar"),
            character_name = c["character_name"],
            requested_at   = c["requested_at"],
            is_own         = c["discord_id"] == user["id"],
        )
        for c in pending
        if c["character_name"].lower() in rank_map
    ]


@router.post("/guild/{guild_name}/claims/{claim_id}/approve", response_model=GuildClaimItem)
async def officer_approve_claim(guild_name: str, claim_id: int, request: Request) -> GuildClaimItem:
    """Approve a pending claim.  Officers cannot approve their own claims."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    claim = await get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["discord_id"] == user["id"]:
        raise HTTPException(status_code=403, detail="You cannot approve your own claim")

    result = await review_claim(claim_id, "approved", user["id"])
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    asyncio.create_task(_refresh_claim_cache(result["discord_id"]))
    return GuildClaimItem(
        id             = result["id"],
        discord_id     = result["discord_id"],
        discord_name   = result["discord_name"],
        avatar         = result.get("avatar"),
        character_name = result["character_name"],
        requested_at   = result["requested_at"],
        is_own         = False,
    )


@router.post("/guild/{guild_name}/claims/{claim_id}/reject")
async def officer_reject_claim(
    guild_name: str,
    claim_id: int,
    body: RejectNoteRequest,
    request: Request,
) -> dict:
    """Reject a pending claim, optionally with a note.  Officers cannot reject their own claims."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    claim = await get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["discord_id"] == user["id"]:
        raise HTTPException(status_code=403, detail="You cannot reject your own claim")

    result = await review_claim(claim_id, "rejected", user["id"], note=body.note)
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    asyncio.create_task(_refresh_claim_cache(result["discord_id"]))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Item watch endpoints
# ---------------------------------------------------------------------------

async def _check_watch(watch: dict) -> None:
    """
    Check whether the watched character currently has the item equipped,
    using the character_cache.  Updates the DB regardless of result.
    """
    name_key = f"{watch['character_name'].lower()}:{_WORLD.lower()}"
    cached, _ = character_cache.get_stale(name_key)
    if cached is None:
        return   # no data available yet — skip, will check later
    item_id_str = str(watch["item_id"])
    seen = any(s.item_id == item_id_str for s in cached.equipment)
    await update_item_watch_check(watch["id"], seen)


async def _check_all_watches(guild_name: str) -> None:
    """Background task: check every watch entry for a guild against the cache."""
    watches = await list_item_watches(guild_name)
    for w in watches:
        try:
            await _check_watch(w)
        except Exception:
            pass


@router.get("/guild/{guild_name}/item-watch", response_model=list[ItemWatchEntry])
async def get_item_watches(guild_name: str, request: Request) -> list[ItemWatchEntry]:
    """
    List all item watch entries for this guild.
    Triggers a background equipment check for all entries so statuses
    are updated against the latest cached character data.
    Officer access required.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    watches = await list_item_watches(guild_name)
    # Fire background check to freshen statuses; return current DB state immediately
    asyncio.create_task(_check_all_watches(guild_name))
    return [ItemWatchEntry(**w) for w in watches]


@router.post("/guild/{guild_name}/item-watch", response_model=ItemWatchEntry, status_code=201)
async def add_item_watch_entry(
    guild_name: str,
    body: AddItemWatchRequest,
    request: Request,
) -> ItemWatchEntry:
    """
    Add a new item watch.  The item_name is resolved against the local items DB
    (falling back to the Census API) to get a canonical display name and ID.
    Returns 409 if the same item is already being watched for that character.
    Officer access required.
    """
    import asyncio as _asyncio
    from census import db as item_db

    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    # Validate character is in this guild
    rank_map = await _roster_rank_map(guild_name)
    char_key = body.character_name.strip().lower()
    if char_key not in rank_map:
        raise HTTPException(
            status_code=404,
            detail=f"'{body.character_name}' is not a member of {guild_name}.",
        )

    # Resolve item — local DB first, then Census
    item_name = body.item_name.strip()
    raw = await item_db.find_by_name(item_name)
    if raw is None:
        # Try Census live
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            raw = await client.get_raw_item(item_name)
            if raw:
                item_list = raw.get("item_list") or []
                raw = item_list[0] if item_list else None
        finally:
            await client.close()
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Item '{item_name}' not found. Check the spelling.",
        )

    item_id   = int(raw["id"])
    item_name = raw.get("displayname") or item_name

    # Canonical character name from roster (correct capitalisation)
    canon_name = next(
        (n for n in rank_map if n == char_key),
        body.character_name.strip(),
    )
    # Try to get properly capitalised name from the cached roster response
    roster_cache_key = f"roster:{guild_name.lower()}:{_WORLD.lower()}"
    roster, _ = guild_cache.get_stale(roster_cache_key)
    if roster:
        match = next((m.name for m in roster.members if m.name.lower() == char_key), None)
        if match:
            canon_name = match

    # Use the officer's primary in-game character name as the attribution,
    # falling back to their Discord display name if no primary is set.
    officer_claims = await get_active_claims(user["id"])
    primary_claim  = next((c for c in officer_claims["approved"] if c.get("is_primary")), None)
    added_by_name  = (
        primary_claim["character_name"]
        if primary_claim
        else (user.get("global_name") or user.get("username", "Unknown"))
    )

    try:
        row = await add_item_watch(
            guild_name     = guild_name,
            character_name = canon_name,
            item_id        = item_id,
            item_name      = item_name,
            added_by       = user["id"],
            added_by_name  = added_by_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Immediately check if the character is already wearing it
    asyncio.create_task(_check_watch(row))

    return ItemWatchEntry(**row)


@router.delete("/guild/{guild_name}/item-watch/{watch_id}", status_code=200)
async def delete_item_watch(guild_name: str, watch_id: int, request: Request) -> dict:
    """Remove an item watch entry.  Officer access required."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")
    if not await remove_item_watch(watch_id, guild_name):
        raise HTTPException(status_code=404, detail="Watch entry not found")
    return {"ok": True}
