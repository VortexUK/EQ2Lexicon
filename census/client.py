from __future__ import annotations

import asyncio
import re
import time as _time
from collections import Counter
from typing import Any, Optional

import aiohttp

from census import db as item_db
from census.item_parser import parse_item as _parse_item_fn
from census.models import AAProfile, AdornSlot, CharacterAAs, CharacterOverview, CharacterSpells, EquipmentSlot, GuildData, GuildMember, ItemData, NodeAA, SpellEntry

BASE_URL = "https://census.daybreakgames.com"


# ---------------------------------------------------------------------------
# aiohttp TraceConfig for Prometheus metrics
# ---------------------------------------------------------------------------
# Uses a lazy import of web.metrics so the Discord bot (which also imports
# this module) works fine even if prometheus-client is absent or the web
# package isn't on sys.path.

def _build_trace_config() -> aiohttp.TraceConfig:
    """Return an aiohttp TraceConfig that records Census API metrics."""

    tc = aiohttp.TraceConfig()

    async def _on_start(
        session: aiohttp.ClientSession,
        ctx: aiohttp.TraceRequestStartParams,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        ctx.start_time = _time.perf_counter()  # type: ignore[attr-defined]

    async def _on_end(
        session: aiohttp.ClientSession,
        ctx: aiohttp.TraceRequestEndParams,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        elapsed = _time.perf_counter() - getattr(ctx, "start_time", _time.perf_counter())
        try:
            from web.metrics import CENSUS_DURATION, CENSUS_REQUESTS, census_endpoint_label
            endpoint = census_endpoint_label(str(params.url))
            http_ok  = 200 <= params.response.status < 300
            CENSUS_REQUESTS.labels(
                endpoint=endpoint,
                status="success" if http_ok else "http_error",
            ).inc()
            CENSUS_DURATION.labels(endpoint=endpoint).observe(elapsed)
        except Exception:
            pass

    async def _on_exception(
        session: aiohttp.ClientSession,
        ctx: aiohttp.TraceRequestExceptionParams,
        params: aiohttp.TraceRequestExceptionParams,
    ) -> None:
        try:
            from web.metrics import CENSUS_REQUESTS, census_endpoint_label
            endpoint = census_endpoint_label(str(params.url))
            CENSUS_REQUESTS.labels(endpoint=endpoint, status="error").inc()
        except Exception:
            pass

    tc.on_request_start.append(_on_start)
    tc.on_request_end.append(_on_end)
    tc.on_request_exception.append(_on_exception)
    return tc


class CensusClient:
    def __init__(self, service_id: str = "example") -> None:
        self.service_id = service_id
        self._session: Optional[aiohttp.ClientSession] = None

    def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                trace_configs=[_build_trace_config()],
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_item(self, query: str) -> Optional[ItemData]:
        from census.db import DB_PATH
        db_exists = DB_PATH.exists()
        # Try local DB first (fast, no rate limits)
        raw = await self._find_in_db(query)
        if raw:
            print(f"[DB] Cache hit for {query!r}")
            return _parse_item_fn(raw)
        if db_exists:
            print(f"[DB] Cache miss for {query!r} — falling back to Census API")
        else:
            print(f"[DB] No database at {DB_PATH} — using Census API")
        # Fall back to live Census API
        data = await self._fetch(self._build_params(query))
        if not data:
            return None
        item_list = data.get("item_list", [])
        if not item_list:
            return None
        raw_item = item_list[0]
        # Cache in local DB so the next lookup is instant
        self._cache_item(raw_item)
        return _parse_item_fn(raw_item)

    async def _find_in_db(self, query: str) -> Optional[dict]:
        """Look up an item in the local SQLite DB. Returns raw Census dict or None."""
        query = query.strip()
        # Game link
        m = re.match(r'\\*aITEM\s+(-?\d+)', query)
        if m:
            item_id = int(m.group(1))
            if item_id < 0:
                item_id += 2 ** 32
            return await item_db.find_by_id(item_id)
        # Bare numeric ID
        if re.fullmatch(r'-?\d+', query):
            return await item_db.find_by_id(int(query))
        # Display name
        return await item_db.find_by_name(query)

    def _cache_item(self, raw: dict) -> None:
        """Write a freshly-fetched Census item into the local DB."""
        try:
            conn = item_db.init_db()
            item_db.upsert_items([raw], conn)
            conn.close()
            print(f"[DB] Cached item {raw.get('id')} ({raw.get('displayname')})")
        except Exception as exc:
            print(f"[DB] Failed to cache item {raw.get('id')}: {exc}")

    async def get_raw_item(self, query: str) -> Optional[dict]:
        """Return the raw parsed JSON — used by inspect_item.py."""
        return await self._fetch(self._build_params(query))

    async def get_guild(self, name: str, world: str) -> Optional[GuildData]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/guild/"
        params = {
            "name": name,
            "world": world,
            "c:resolve": "members(displayname,type.aa_level,type.deity,type.level,type.class,guild.rank,guild.status,type.ts_class,type.ts_level,playedtime)",
            "c:show": "member_list,name,world,rank_list",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        guild_list = data.get("guild_list", [])
        if not guild_list:
            return None
        guild = guild_list[0]

        # Build rank id → name lookup from rank_list
        rank_map: dict[int, str] = {
            int(r["id"]): r["name"]
            for r in (guild.get("rank_list") or [])
            if isinstance(r, dict) and "id" in r and "name" in r
        }

        members: list[GuildMember] = []
        for m in guild.get("member_list") or []:
            t = m.get("type")
            if not isinstance(t, dict):
                continue
            guild_sec    = m.get("guild") or {}
            raw_rank     = _int(guild_sec.get("rank"))
            guild_status = _int(guild_sec.get("status"))
            deity_val    = t.get("deity")
            members.append(GuildMember(
                name         = m.get("name") or m.get("displayname", "Unknown"),
                level        = _int(t.get("level")),
                cls          = t.get("class"),
                ts_class     = t.get("ts_class"),
                ts_level     = _int(t.get("ts_level")),
                aa_level     = _int(t.get("aa_level")),
                deity        = deity_val if deity_val and str(deity_val).lower() != "none" else None,
                rank         = rank_map.get(raw_rank) if raw_rank is not None else None,
                rank_id      = raw_rank,
                guild_status = guild_status,
                played_time  = _int(m.get("playedtime")),
            ))
        return GuildData(
            name    = guild.get("name", name),
            world   = guild.get("world", world),
            members = members,
        )

    # Slots to exclude from the equipment display
    _SKIP_SLOTS = frozenset({"ammo", "event slot", "mount adornment", "mount armor"})

    async def _parse_equipment(self, raw_slots: list) -> list[EquipmentSlot]:
        """Parse a Census equipmentslot_list into EquipmentSlot dataclass objects."""
        equipment: list[EquipmentSlot] = []
        for slot in raw_slots:
            if not isinstance(slot, dict):
                continue
            slot_display = slot.get("displayname", "")
            if slot_display.lower() in self._SKIP_SLOTS:
                continue
            item_data = slot.get("item")
            if not isinstance(item_data, dict):
                continue
            item_id = _int(item_data.get("id"))
            if item_id is None:
                continue
            db_row = await item_db.find_by_id(item_id)
            if db_row:
                item_name = db_row.get("displayname") or f"Item #{item_id}"
                item_tier = str(db_row.get("tier") or "")
                icon_id   = str(db_row["iconid"]) if db_row.get("iconid") else None
            else:
                item_name = f"Item #{item_id}"
                item_tier = ""
                icon_id   = None
            adorn_slots: list[AdornSlot] = []
            for adorn in (item_data.get("adornment_list") or []):
                if not isinstance(adorn, dict):
                    continue
                color    = adorn.get("color", "").capitalize()
                adorn_id = _int(adorn.get("id"))
                if adorn_id is not None:
                    adorn_db   = await item_db.find_by_id(adorn_id)
                    adorn_name = adorn_db.get("displayname") if adorn_db else None
                else:
                    adorn_name = None
                adorn_slots.append(AdornSlot(
                    color      = color,
                    adorn_name = adorn_name,
                    adorn_id   = str(adorn_id) if adorn_id is not None else None,
                ))
            equipment.append(EquipmentSlot(
                slot_name   = slot_display,
                item_name   = item_name,
                item_id     = str(item_id),
                icon_id     = icon_id,
                tier        = item_tier or None,
                adorn_slots = adorn_slots,
            ))
        return equipment

    async def get_character(self, name: str, world: str) -> Optional[CharacterOverview]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": name,
            "locationdata.world": world,
            "c:show": "name,type,stats,equipmentslot_list,spell_list,guild",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        char_list = data.get("character_list", [])
        if not char_list:
            return None
        char = char_list[0]

        t = char.get("type") or {}
        deity_val = t.get("deity")

        # aa_level in type is the total AA level shown in-game
        aa_count = _int(t.get("aa_level")) or 0

        equipment = await self._parse_equipment(char.get("equipmentslot_list") or [])

        gender   = t.get("gender", "")
        ts_class = t.get("ts_class", "")
        raw_stats = char.get("stats") or {}
        # In the direct character call, 'ability' and 'personal_status_points' are
        # top-level on the character object rather than nested inside 'stats'.
        # Merge them in so _parse_stats always finds them in the same place.
        if "ability" not in raw_stats and char.get("ability"):
            raw_stats = {**raw_stats, "ability": char["ability"]}
        if "personal_status_points" not in raw_stats and char.get("personal_status_points"):
            raw_stats = {**raw_stats, "personal_status_points": char["personal_status_points"]}

        spell_ids: list[int] = []
        for s in char.get("spell_list") or []:
            if isinstance(s, dict):
                sid = _int(s.get("id"))
            elif isinstance(s, (int, str)):
                sid = _int(s)
            else:
                sid = None
            if sid is not None:
                spell_ids.append(sid)

        guild_raw  = char.get("guild")
        guild_name = guild_raw.get("name") if isinstance(guild_raw, dict) else None

        return CharacterOverview(
            id         = str(char.get("id", "")),
            name       = (char.get("name") or {}).get("first", name),
            level      = _int(t.get("level")),
            cls        = t.get("class"),
            race       = t.get("race"),
            gender     = gender.capitalize() if gender else None,
            deity      = deity_val if deity_val and str(deity_val).lower() != "none" else None,
            aa_count   = aa_count,
            world      = world,
            ts_class   = ts_class.capitalize() if ts_class else None,
            ts_level   = _int(t.get("ts_level")),
            guild_name = guild_name or None,
            stats      = raw_stats,
            equipment  = equipment,
            spell_ids  = spell_ids,
        )

    async def get_character_aas(self, name: str, world: str) -> Optional[CharacterAAs]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": name,
            "locationdata.world": world,
            "c:show": "name,alternateadvancements,orderedalternateadvancement_list",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        char_list = data.get("character_list", [])
        if not char_list:
            return None
        char = char_list[0]
        char_name = (char.get("name") or {}).get("first", name)

        aa_entries: list[NodeAA] = []
        aas = char.get("alternateadvancements") or {}
        for aa in aas.get("alternateadvancement_list") or []:
            tier = _int(aa.get("tier")) or 0
            if tier == 0:
                continue
            node_id = _int(aa.get("id"))
            tree_id = _int(aa.get("treeID"))
            if node_id is None or tree_id is None:
                continue
            aa_entries.append(NodeAA(node_id=node_id, tree_id=tree_id, tier=tier))

        # Parse AA profiles (orderedalternateadvancement_list).
        # Each entry is one point spent: {treeID, id, order}.
        # Count occurrences of each (treeID, id) pair to reconstruct tier counts.
        profiles: list[AAProfile] = []
        for prof_raw in char.get("orderedalternateadvancement_list") or []:
            prof_name = str(prof_raw.get("profilename") or "Profile")
            counts: Counter = Counter()
            for entry in prof_raw.get("alternateadvancement_list") or []:
                node_id = _int(entry.get("id"))
                tree_id = _int(entry.get("treeID"))
                if node_id is not None and tree_id is not None:
                    counts[(tree_id, node_id)] += 1
            prof_nodes = [
                NodeAA(node_id=nid, tree_id=tid, tier=count)
                for (tid, nid), count in counts.items()
            ]
            profiles.append(AAProfile(name=prof_name, aa_list=prof_nodes))

        return CharacterAAs(character_name=char_name, aa_list=aa_entries, profiles=profiles)

    async def get_guild_equipment_data(
        self, guild_name: str, world: str
    ) -> tuple[dict[int, str], list[dict]]:
        """
        Fetch the guild's member list with equipment + adornment data.
        Returns (rank_id→name map, raw member list).
        Each member dict has 'name', 'guild' (with 'rank'), and 'equipmentslot_list'.
        """
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/guild/"
        params = {
            "name": guild_name,
            "world": world,
            "c:resolve": "members(displayname,guild.rank,equipmentslot_list)",
            "c:show": "member_list,name,world,rank_list",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return {}, []
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return {}, []

        guild_list = data.get("guild_list", [])
        if not guild_list:
            return {}, []
        guild = guild_list[0]

        rank_map: dict[int, str] = {
            int(r["id"]): r["name"]
            for r in (guild.get("rank_list") or [])
            if isinstance(r, dict) and "id" in r and "name" in r
        }
        return rank_map, guild.get("member_list") or []

    async def get_guild_info(self, name: str, world: str) -> Optional[dict]:
        """Return lightweight guild metadata (no member resolve)."""
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/guild/"
        params = {
            "name": name,
            "world": world,
            "c:show": "name,world,dateformed,description,alignment,type,level,members,accounts,achievement_list",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        guild_list = data.get("guild_list", [])
        if not guild_list:
            return None
        g = guild_list[0]
        return {
            "name":              g.get("name", name),
            "world":             g.get("world", world),
            "dateformed":        g.get("dateformed"),
            "description":       g.get("description") or None,
            "alignment":         g.get("alignment") or None,
            "type":              g.get("type") or None,
            "level":             _int(g.get("level")),
            "members":           _int(g.get("members")),
            "accounts":          _int(g.get("accounts")),
            "achievement_count": len(g.get("achievement_list") or []),
        }

    async def get_guild_full(
        self, name: str, world: str
    ) -> Optional[tuple[GuildData, list[CharacterOverview]]]:
        """
        Fetch guild with full member profiles (type + stats + equipment + spell IDs).
        Returns (GuildData, list[CharacterOverview]) for cache pre-warming.
        Spell IDs are raw integers stored in CharacterOverview.spell_ids; callers
        should resolve them against the local spells DB rather than making
        per-character Census calls.
        """
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/guild/"
        params = {
            "name": name,
            "world": world,
            "c:resolve": "members(displayname,type,stats,guild.rank,guild.status,playedtime,equipmentslot_list,spell_list)",
            "c:show": "member_list,name,world,rank_list",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        guild_list = data.get("guild_list", [])
        if not guild_list:
            return None
        guild = guild_list[0]

        rank_map: dict[int, str] = {
            int(r["id"]): r["name"]
            for r in (guild.get("rank_list") or [])
            if isinstance(r, dict) and "id" in r and "name" in r
        }

        members: list[GuildMember] = []
        overviews: list[CharacterOverview] = []

        guild_name_str = guild.get("name", name)

        for m in guild.get("member_list") or []:
            t = m.get("type")
            if not isinstance(t, dict):
                continue
            guild_sec    = m.get("guild") or {}
            raw_rank     = _int(guild_sec.get("rank"))
            guild_status = _int(guild_sec.get("status"))
            deity_val    = t.get("deity")
            # Guild member resolve puts the character name in 'name' or 'displayname'
            member_name = m.get("name") or m.get("displayname", "Unknown")
            gender   = t.get("gender", "")
            ts_class = t.get("ts_class", "")

            members.append(GuildMember(
                name         = member_name,
                level        = _int(t.get("level")),
                cls          = t.get("class"),
                ts_class     = ts_class,
                ts_level     = _int(t.get("ts_level")),
                aa_level     = _int(t.get("aa_level")),
                deity        = deity_val if deity_val and str(deity_val).lower() != "none" else None,
                rank         = rank_map.get(raw_rank) if raw_rank is not None else None,
                rank_id      = raw_rank,
                guild_status = guild_status,
                played_time  = _int(m.get("playedtime")),
            ))

            # In guild resolves 'ability' and 'personal_status_points' live inside
            # the 'stats' dict already — no merge needed (unlike direct character calls).
            raw_stats = m.get("stats") or {}
            equipment = await self._parse_equipment(m.get("equipmentslot_list") or [])

            # Spell IDs: Census returns a flat list of dicts with at minimum {"id": <int>}
            # when spell_list is included in the member resolve.
            raw_spell_list = m.get("spell_list") or []
            spell_ids: list[int] = []
            for s in raw_spell_list:
                if isinstance(s, dict):
                    sid = _int(s.get("id"))
                elif isinstance(s, (int, str)):
                    sid = _int(s)
                else:
                    sid = None
                if sid is not None:
                    spell_ids.append(sid)

            overviews.append(CharacterOverview(
                id         = str(m.get("id", "")),
                name       = member_name,
                level      = _int(t.get("level")),
                cls        = t.get("class"),
                race       = t.get("race"),
                gender     = gender.capitalize() if gender else None,
                deity      = deity_val if deity_val and str(deity_val).lower() != "none" else None,
                aa_count   = _int(t.get("aa_level")) or 0,
                world      = world,
                ts_class   = ts_class.capitalize() if ts_class else None,
                ts_level   = _int(t.get("ts_level")),
                guild_name = guild_name_str or None,
                stats      = raw_stats,
                equipment  = equipment,
                spell_ids  = spell_ids,
            ))

        return (
            GuildData(
                name    = guild.get("name", name),
                world   = guild.get("world", world),
                members = members,
            ),
            overviews,
        )

    async def get_character_guild_name(self, character_name: str, world: str) -> Optional[str]:
        """
        Return the guild name for a character, or None if not in a guild.
        Raises on Census API/network errors so callers can distinguish
        'character has no guild' (returns None) from 'fetch failed' (raises).
        """
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": character_name,
            "locationdata.world": world,
            "c:show": "name,guild",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    raise RuntimeError(f"Census HTTP {resp.status} for guild lookup of {character_name!r}")
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error fetching guild for {character_name!r}: {type(exc).__name__}: {exc!r}")
            raise  # re-raise so callers can detect the failure

        char_list = data.get("character_list", [])
        if not char_list:
            return None  # character not found — not a fetch error
        guild = char_list[0].get("guild")
        if not guild or not isinstance(guild, dict):
            return None  # character genuinely has no guild
        return guild.get("name") or None

    async def get_character_brief(self, name: str, world: str) -> dict | None:
        """
        Lightweight single-character lookup — returns name, class, level, guild only.
        Uses the same ``name.first`` exact-match parameter as ``get_character``
        so it works reliably even when prefix-search misses a character.
        """
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": name,
            "locationdata.world": world,
            "c:show": "name,type,guild",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        char_list = data.get("character_list", [])
        if not char_list:
            return None
        char = char_list[0]
        t     = char.get("type") or {}
        guild = char.get("guild") or {}
        char_name = (char.get("name") or {}).get("first", name)
        return {
            "name":       char_name,
            "cls":        t.get("class"),
            "class_id":   _int(t.get("classid")),
            "level":      _int(t.get("level")),
            "aa_level":   _int(t.get("aa_level")),
            "race":       t.get("race"),
            "guild_name": guild.get("name") if isinstance(guild, dict) else None,
        }

    async def search_characters_by_name(
        self,
        name: str,
        world: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search characters whose first name starts with *name* on the given world.
        Uses Census ``name.first_lower`` prefix filter (``^`` = starts-with).
        Returns a list of dicts with keys: name, cls, class_id, level, aa_level,
        race, guild_name.
        """
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first_lower": f"^{name.lower()}",
            "locationdata.world": world,
            "c:show": "name,type,guild",
            "c:limit": str(limit),
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return []

        results: list[dict] = []
        for char in data.get("character_list") or []:
            t = char.get("type") or {}
            guild = char.get("guild") or {}
            char_name = (char.get("name") or {}).get("first", "")
            if not char_name:
                continue
            results.append({
                "name":       char_name,
                "cls":        t.get("class"),
                "class_id":   _int(t.get("classid")),
                "level":      _int(t.get("level")),
                "aa_level":   _int(t.get("aa_level")),
                "race":       t.get("race"),
                "guild_name": guild.get("name") if isinstance(guild, dict) else None,
            })
        results.sort(key=lambda r: (r.get("name") or "").lower())
        return results

    async def search_guilds_by_name(
        self,
        name: str,
        world: str,
        limit: int = 15,
    ) -> list[dict]:
        """
        Search guilds whose name starts with *name* on the given world.
        Returns a list of dicts with keys: name.
        """
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/guild/"
        params = {
            "name_lower": f"^{name.lower()}",
            "world": world,
            "c:show": "name,world",
            "c:limit": str(limit),
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return []

        results: list[dict] = []
        for guild in data.get("guild_list") or []:
            guild_name = guild.get("name")
            if guild_name:
                results.append({"name": guild_name})
        results.sort(key=lambda r: (r.get("name") or "").lower())
        return results

    async def search_characters(
        self,
        world: str,
        class_ids: list[int],
        min_level: int | None = None,
        max_level: int | None = None,
        sort_by: str = "level",
        sort_dir: str = "desc",
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        """
        Search characters on the given world with optional class/level filters.
        For multiple class_ids (archetype queries), runs parallel Census calls
        and combines results server-side.
        Returns: {results, total, page, per_page}
        """
        queries: list[int | None] = class_ids if class_ids else [None]
        tasks = [self._search_chars_single(world, cid, min_level) for cid in queries]
        results_lists = await asyncio.gather(*tasks)

        all_results: list[dict] = []
        seen: set[str] = set()
        for rlist in results_lists:
            for r in rlist:
                if r["name"] not in seen:
                    seen.add(r["name"])
                    all_results.append(r)

        # Apply max_level filter server-side (Census can only do >= efficiently)
        if max_level is not None:
            all_results = [r for r in all_results if (r.get("level") or 0) <= max_level]

        # Sort
        reverse = sort_dir.lower() == "desc"
        if sort_by == "name":
            all_results.sort(key=lambda r: (r.get("name") or "").lower(), reverse=reverse)
        elif sort_by == "aa":
            all_results.sort(key=lambda r: r.get("aa_level") or 0, reverse=reverse)
        else:  # level (default)
            all_results.sort(key=lambda r: r.get("level") or 0, reverse=reverse)

        total = len(all_results)
        start = (page - 1) * per_page
        return {
            "results": all_results[start : start + per_page],
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    async def _search_chars_single(
        self,
        world: str,
        class_id: int | None,
        min_level: int | None,
    ) -> list[dict]:
        """Single Census character search call.  Used by search_characters."""
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params: dict[str, str] = {
            "locationdata.world": world,
            "c:show": "displayname,type,guild",
            "c:limit": "200",
        }
        if class_id is not None:
            params["type.classid"] = str(class_id)
        if min_level is not None:
            # ] prefix = "greater than or equal" in Census filter syntax
            # aiohttp percent-encodes ] as %5D which Census accepts fine
            params["type.level"] = f"]{min_level}"

        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return []

        results: list[dict] = []
        for char in data.get("character_list") or []:
            t = char.get("type") or {}
            guild = char.get("guild") or {}
            guild_name = guild.get("name") if isinstance(guild, dict) else None
            name = char.get("displayname") or (char.get("name") or {}).get("first", "")
            if not name:
                continue
            results.append({
                "name":       name,
                "cls":        t.get("class"),
                "class_id":   _int(t.get("classid")),
                "level":      _int(t.get("level")),
                "aa_level":   _int(t.get("aa_level")),
                "race":       t.get("race"),
                "guild_name": guild_name or None,
            })
        return results

    async def get_character_spells(self, name: str, world: str) -> Optional[CharacterSpells]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": name,
            "locationdata.world": world,
            "c:resolve": "spells(name,tier_name,type,level,given_by)",
            "c:show": "name,spell_list",
            "c:limit": "1",
        }
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None

        char_list = data.get("character_list", [])
        if not char_list:
            return None
        char = char_list[0]
        char_name = (char.get("name") or {}).get("first", name)

        entries: list[SpellEntry] = []
        for spell in char.get("spell_list") or []:
            level = _int(spell.get("level")) or 0
            spell_type = spell.get("type", "")
            if level == 0 or spell_type not in ("spells", "arts"):
                continue
            if spell.get("given_by") in ("alternateadvancement", "class"):
                continue
            entries.append(SpellEntry(
                name       = spell.get("name", ""),
                tier       = spell.get("tier_name", "Unknown"),
                spell_type = spell_type,
                level      = level,
            ))
        return CharacterSpells(character_name=char_name, entries=entries)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _build_params(self, query: str) -> dict:
        """Return Census API query params for a name, numeric ID, or game link."""
        query = query.strip()
        # Game link: \aITEM <id> ...:<name>/a
        # The game uses signed 32-bit IDs; Census uses unsigned — convert if negative.
        m = re.match(r'\\*aITEM\s+(-?\d+)', query)
        if m:
            item_id = int(m.group(1))
            if item_id < 0:
                item_id += 2 ** 32
            return {"id": str(item_id), "c:limit": "1"}
        # Bare numeric ID (positive or negative)
        if re.fullmatch(r'-?\d+', query):
            return {"id": query, "c:limit": "1"}
        # Display name
        return {"displayname": query, "c:limit": "1"}

    async def _fetch(self, params: dict) -> Optional[dict]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/item/"
        print(f"[Census] GET {url} params={params}")
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                print(f"[Census] HTTP {resp.status} url={resp.url}")
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                print(f"[Census] returned={data.get('returned')} items")
                return data
        except Exception as exc:
            print(f"[Census] API error: {type(exc).__name__}: {exc!r}")
            return None



# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _str(value: Any) -> str:
    """Return a string from value, treating dicts/None as empty."""
    if value is None or isinstance(value, dict):
        return ""
    return str(value)
