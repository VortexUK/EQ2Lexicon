"""Guild cache orchestration helpers.

Extracted from ``web/routes/guild.py`` (BE-054). These functions are
shared infrastructure used by multiple callers that previously imported
them lazily to avoid circular imports:

  * ``web/census_refresh.py``   — calls _persist_and_publish_guild
  * ``web/routes/parses/ingest.py`` — calls _fetch_and_cache_guild

Moving them here breaks the circular-import chain (guild.py → character.py
→ guild.py) at the module level, so all callers can switch from lazy
in-function imports to module-level imports.

The ``_officer_chars`` / ``_roster_rank_map`` / ``_OFFICER_RANKS`` helpers
that are also shared with guild_officer.py, item_watch.py, etc. remain in
``web/routes/guild.py`` because they depend on ``current_world()`` at
request time and are closer to route logic than cache orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

from backend.census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from backend.census.models import CharacterOverview, GuildData, SpellEntry
from backend.census.store import store as census_store
from backend.eq2db.spells import (
    DB_PATH as _SPELLS_DB,
)
from backend.eq2db.spells import (
    SpellRow as _SpellRow,
)
from backend.eq2db.spells import (
    catalogue as _spells,
)
from backend.server.cache import character_cache, guild_cache
from backend.server.core.cache_keys import census_refresh_guild_key, guild_info_key, guild_roster_key
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

# Avoid top-level import of route-level models: import them locally to
# keep this module free of route-layer dependencies.

# Canonical adorn-colour display order (mirrors the constant in guild.py)
_COLOUR_ORDER = ["White", "Yellow", "Red", "Blue", "Turquoise", "Green", "Orange", "Purple"]

# ---------------------------------------------------------------------------
# In-flight deduplication guards
# ---------------------------------------------------------------------------

# In-flight deduplication: one dict covers ALL guild endpoints (roster, info,
# spell-check, adorn-check) so any concurrent cache-miss fires only one Census call.
_guild_fetch_tasks: dict[str, asyncio.Task] = {}

# Background-refresh dedup: tracks which guilds already have a background
# refresh task scheduled, so concurrent stale responses don't spawn duplicates.
_guild_refresh_in_flight: set[str] = set()


# ---------------------------------------------------------------------------
# Pre-warming helpers (shared between _fetch_and_cache_guild and any future
# callers that want to warm the derived caches from already-parsed data)
# ---------------------------------------------------------------------------


def _prewarm_adorn_cache(
    cache_key: str,
    guild_name: str,
    overviews: list[CharacterOverview],
    member_rank: dict[str, tuple],
) -> None:
    """Build and store GuildAdornCheckResponse from already-parsed equipment data."""
    # Import locally so this module doesn't depend on the route-layer models at
    # module level (those live in guild.py, which imports from here).
    from backend.server.api.guild import (  # noqa: PLC0415
        AdornColorStats,
        GuildAdornCheckResponse,
        MemberAdornStats,
    )

    all_colours: set[str] = set()
    out_members: list[MemberAdornStats] = []

    for ov in overviews:
        colour_stats: dict[str, list[int]] = {}  # colour → [filled, total]
        missing_slots: dict[str, list[str]] = {}  # colour → slot names with empty adorn
        for eq_slot in ov.equipment:
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
):
    """Build a GuildSpellCheckResponse from CharacterOverview.spell_ids using
    a single bulk lookup against the local spells DB.  Returns None if the
    DB is unavailable or no members had any spell IDs."""
    from backend.server.api.guild import (  # noqa: PLC0415
        GuildSpellCheckResponse,
        MemberSpellTiers,
    )

    if not _SPELLS_DB.exists():
        return None

    all_ids: list[int] = []
    for ov in overviews:
        all_ids.extend(ov.spell_ids)

    if not all_ids:
        return None

    spell_db: dict[int, _SpellRow] = _spells.find_by_ids(list(set(all_ids)))
    blocklist = _spells.load_blocklist()

    # Show every *upgradeable* spell a member owns (a line with a tier ladder),
    # regardless of how it was acquired. The old `given_by=='spellscroll'` gate
    # dropped base-tier auto-grants (given_by='class', e.g. Apprentice) and
    # trainer-granted spells (given_by='classtraining'), so those tiers — most
    # visibly Apprentice — never appeared as columns. Mirrors the character
    # spells path; AA abilities (given_by='alternateadvancement') stay excluded.
    upgradeable = _spells.upgradeable_crcs(
        {
            row.get("crc")
            for row in spell_db.values()
            if (row.get("level") or 0) > 0
            and row.get("type") in ("spells", "arts")
            and row.get("given_by") != "alternateadvancement"
            and _spells.strip_roman(row.get("name") or "").lower() not in blocklist
        }
    )

    out_members: list[MemberSpellTiers] = []
    tiers_with_data: set[str] = set()

    for ov in overviews:
        entries: list[SpellEntry] = []
        for sid in ov.spell_ids:
            row = spell_db.get(sid)
            if row is None:
                continue
            if (row.get("level") or 0) <= 0:
                continue
            if row.get("type") not in ("spells", "arts"):
                continue
            if row.get("given_by") == "alternateadvancement":
                continue
            if row.get("crc") not in upgradeable:
                continue
            if _spells.strip_roman(row.get("name") or "").lower() in blocklist:
                continue
            entries.append(
                SpellEntry(
                    name=row.get("name", ""),
                    tier=row.get("tier_name") or "Unknown",
                    spell_type=row.get("type") or "",
                    level=row.get("level") or 0,
                )
            )

        entries = _spells.unique_highest_entries(entries)
        count = Counter(e.tier for e in entries)
        tiers_with_data.update(count.keys())

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
    """Build and store GuildSpellCheckResponse using the local spells DB.
    No-op if DB is unavailable or IDs are empty."""
    result = _build_spell_check_from_overviews(guild_name, current_world(), overviews, member_rank)
    if result is not None:
        guild_cache.set(cache_key, result)


# ---------------------------------------------------------------------------
# Core fetch + cache orchestration
# ---------------------------------------------------------------------------


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
    from backend.server.api.guild import GuildInfoResponse, GuildMemberResponse, GuildResponse  # noqa: PLC0415

    task_key = guild_roster_key(guild_name, current_world())
    existing = _guild_fetch_tasks.get(task_key)
    if existing is not None and not existing.done():
        return await existing

    async def _do_fetch():
        world = current_world()
        async with shared_census_client() as client:
            full = await client.get_guild_full(guild_name, world)
        if not full or not full[0].members:
            return None

        guild_data, overviews, guild_info, roster_stubs = full
        world_lower = world.lower()
        guild_lower = guild_name.lower()
        member_rank: dict[str, tuple] = {m.name: (m.rank, m.rank_id) for m in guild_data.members}

        # Cache the full member stubs (every member, resolved AND offline) so
        # _persist_and_publish_guild can build the best-known merged roster
        # without a second Census round-trip.
        guild_cache.set(f"roster_stubs:{guild_lower}:{world_lower}", roster_stubs)

        # Info
        guild_cache.set(
            f"info:{guild_lower}:{world_lower}",
            GuildInfoResponse(**guild_info),
        )

        # Per-character overviews
        fails: list[tuple[str, Exception]] = []
        for ov in overviews:
            try:
                character_cache.set(
                    f"{ov.name.lower()}:{world_lower}",
                    _overview_to_char_response(ov),
                )
            except Exception as exc:
                fails.append((ov.name, exc))
        if fails:
            _log.warning(
                "[guild-cache] %d pre-warm failures (first: %s — %s)",
                len(fails),
                fails[0][0],
                fails[0][1],
            )

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

        # Per-member average gear ilvl
        from backend.eq2db.items import catalogue as _items  # noqa: PLC0415
        from backend.server.api.character import (  # noqa: PLC0415
            _equipment_lookup_ids,
            _ilvl_from_gear,
        )

        all_ids = list({i for ov in overviews for i in _equipment_lookup_ids(ov.equipment)})
        gear = _items.gear_for_ids(all_ids)
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
        return (guild_data, overviews, guild_info)

    task: asyncio.Task = asyncio.create_task(_do_fetch())
    _guild_fetch_tasks[task_key] = task
    try:
        return await task
    except Exception as exc:
        _log.warning("[cache] Guild fetch failed for %s: %s", guild_name, exc)
        return None
    finally:
        if _guild_fetch_tasks.get(task_key) is task:
            _guild_fetch_tasks.pop(task_key, None)


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
    from backend.server import census_events
    from backend.server.api.guild import GuildMemberResponse, GuildResponse  # noqa: PLC0415
    from backend.server.census_refresh import _merge_roster  # local import — cycle avoidance

    world = world or current_world()
    await _fetch_and_cache_guild(guild_name)  # existing: warms roster/info/spells/adorns + char cache
    now = int(time.time())
    roster, _ = guild_cache.get_stale(guild_roster_key(guild_name, world))
    if roster is None:
        return
    info, _ = guild_cache.get_stale(guild_info_key(guild_name, world))
    roster_stubs, _ = guild_cache.get_stale(f"roster_stubs:{guild_name.lower()}:{world.lower()}")
    if roster_stubs is None:
        roster_stubs = []

    resolved_members = roster.model_dump()["members"]
    fresh_by_name: dict[str, dict] = {m["name"]: m for m in resolved_members}

    _member_fields = set(GuildMemberResponse.model_fields)

    conn = census_store.init_db()
    try:
        stored_by_lower: dict[str, dict] = {}
        for stub in roster_stubs:
            sname = stub.get("name")
            if not sname or sname in fresh_by_name:
                continue
            rec = census_store.get_character(conn, sname, world)
            if rec is not None:
                stored_by_lower[sname.lower()] = rec["data"]

        merged = _merge_roster(roster_stubs, fresh_by_name, stored_by_lower)

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

        for m in fresh_by_name.values():
            if not m.get("name"):
                continue
            # Stamp the guild onto the stored member. The roster overview
            # (GuildMemberResponse) carries no guild_name, so without this a
            # character served from the durable store on a cache miss shows no
            # guild — most visibly for combatants in a big-guild parse import,
            # where many members are resolved once then fall out of the hot cache.
            census_store.upsert_character(
                conn, m["name"], world, {**m, "guild_name": roster.name}, resolved=True, now=now
            )
    finally:
        conn.close()
    # SSE event carries the MERGED roster
    census_events.publish(
        {"type": "guild", "key": census_refresh_guild_key(guild_name, world), "data": merged_data, "fetched_at": now}
    )


def _overview_to_char_response(ov: CharacterOverview):  # → CharacterResponse
    """Convert a CharacterOverview into the shared CharacterResponse model.

    Uses a local import to avoid a module-level circular-import between guild
    and character routes. Delegates entirely to _build_char_response so the
    two stay in sync (including spell_ids).
    """
    from backend.server.api.character import _build_char_response  # local to avoid circular import

    return _build_char_response(ov)
