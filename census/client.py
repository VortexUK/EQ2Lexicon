from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import aiohttp

from census import db as item_db
from census.constants import ITEM_DISPLAY, STAT_MAP, TYPEINFO_DISPLAY
from census.models import CharacterAAs, CharacterOverview, CharacterSpells, EquipmentSlot, GuildData, GuildMember, ItemData, ItemEffect, ItemStat, NodeAA, SpellEntry

BASE_URL       = "https://census.daybreakgames.com"
_ITEM_ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "items" / "icons"


def _load_item_icon(icon_id: str) -> Optional[bytes]:
    path = _ITEM_ICONS_DIR / f"{icon_id}.png"
    return path.read_bytes() if path.exists() else None

# JSON flag key → display label (order = display order in tooltip)
_FLAG_LABELS: dict[str, str] = {
    "heirloom":   "HEIRLOOM",
    "lore-equip": "LORE-EQUIP",
    "lore":       "LORE",
    "attunable":  "ATTUNEABLE",
    "notrade":    "NO-TRADE",
    "nozone":     "NO-ZONE",
    "novalue":    "NO-VALUE",
    "prestige":   "PRESTIGE",
    "relic":      "RELIC",
}


class CensusClient:
    def __init__(self, service_id: str = "example") -> None:
        self.service_id = service_id
        self._session: Optional[aiohttp.ClientSession] = None

    def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
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
            return self._parse_item(raw)
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
        return self._parse_item(raw_item)

    async def _find_in_db(self, query: str) -> Optional[dict]:
        """Look up an item in the local SQLite DB. Returns raw Census dict or None."""
        import re
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
            "c:resolve": "members(displayname,type.aa_level,type.deity,type.level,type.class,guild.rank,type.ts_class,type.ts_level)",
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
            raw_rank = _int((m.get("guild") or {}).get("rank"))
            deity_val = t.get("deity")
            members.append(GuildMember(
                name     = m.get("name") or m.get("displayname", "Unknown"),
                level    = _int(t.get("level")),
                cls      = t.get("class"),
                ts_class = t.get("ts_class"),
                ts_level = _int(t.get("ts_level")),
                aa_level = _int(t.get("aa_level")),
                deity    = deity_val if deity_val and str(deity_val).lower() != "none" else None,
                rank     = rank_map.get(raw_rank) if raw_rank is not None else None,
                rank_id  = raw_rank,
            ))
        return GuildData(
            name    = guild.get("name", name),
            world   = guild.get("world", world),
            members = members,
        )

    # Slots to exclude from the equipment display
    _SKIP_SLOTS = frozenset({"food", "drink", "ammo", "event slot", "mount adornment", "mount armor"})

    async def get_character(self, name: str, world: str) -> Optional[CharacterOverview]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": name,
            "locationdata.world": world,
            "c:show": "name,type,equipmentslot_list",
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

        # Equipment — items come back with only an id; look up names in local DB
        equipment: list[EquipmentSlot] = []
        for slot in char.get("equipmentslot_list") or []:
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

            # Look up item name + tier from local DB
            db_row = await item_db.find_by_id(item_id)
            if db_row:
                item_name = db_row.get("displayname") or f"Item #{item_id}"
                item_tier = db_row.get("tier")
                icon_id   = str(db_row["iconid"]) if db_row.get("iconid") else None
            else:
                item_name = f"Item #{item_id}"
                item_tier = None
                icon_id   = None

            equipment.append(EquipmentSlot(
                slot_name = slot_display,
                item_name = item_name,
                item_id   = str(item_id),
                icon_id   = icon_id,
                tier      = item_tier,
            ))

        gender   = t.get("gender", "")
        ts_class = t.get("ts_class", "")
        return CharacterOverview(
            id        = str(char.get("id", "")),
            name      = (char.get("name") or {}).get("first", name),
            level     = _int(t.get("level")),
            cls       = t.get("class"),
            race      = t.get("race"),
            gender    = gender.capitalize() if gender else None,
            deity     = deity_val if deity_val and str(deity_val).lower() != "none" else None,
            aa_count  = aa_count,
            world     = world,
            ts_class  = ts_class.capitalize() if ts_class else None,
            ts_level  = _int(t.get("ts_level")),
            equipment = equipment,
        )

    async def get_character_aas(self, name: str, world: str) -> Optional[CharacterAAs]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/character/"
        params = {
            "name.first": name,
            "locationdata.world": world,
            "c:show": "name,alternateadvancements",
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

        return CharacterAAs(character_name=char_name, aa_list=aa_entries)

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
        import re
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
    # Parsing
    # ------------------------------------------------------------------

    def _parse_item(self, item: dict) -> ItemData:
        typeinfo   = item.get("typeinfo") or {}
        slot_list  = item.get("slot_list") or []

        # Classes: typeinfo.classes is a dict keyed by internal class name
        classes_dict = typeinfo.get("classes") or {}
        classes = [
            v["displayname"] if isinstance(v, dict) and "displayname" in v else k.capitalize()
            for k, v in classes_dict.items()
        ]

        # Level comes from the first class entry; fall back to leveltouse
        first_class = next(iter(classes_dict.values()), None)
        class_level = _int(first_class.get("level")) if isinstance(first_class, dict) else None
        item_level  = class_level or _int(item.get("leveltouse"))

        return ItemData(
            id          = str(item.get("id", "")),
            name        = item.get("displayname", "Unknown Item"),
            quality     = str(item.get("tier", "")).lower(),      # "FABLED" → "fabled"
            description = _str(item.get("description")),
            icon_id     = str(item["iconid"]) if item.get("iconid") else None,
            icon_bytes  = _load_item_icon(str(item["iconid"])) if item.get("iconid") else None,
            armor_type  = _armor_type(typeinfo),
            mitigation  = _int(typeinfo.get("maxarmorclass")),
            slot_type   = _slot_type(slot_list, typeinfo),
            item_level  = item_level,
            required_level = _int(item.get("leveltouse")),
            classes     = classes,
            stats       = self._parse_stats(item.get("modifiers") or {}),
            effects     = self._parse_effects(
                              item.get("effect_list") or [],
                              item.get("adornment_list") or [],
                          ),
            adornment_slots = [
                s["color"].capitalize()
                for s in (item.get("adornmentslot_list") or [])
                if isinstance(s, dict) and s.get("color")
            ],
            flags           = self._parse_flags(item.get("flags") or {}),
            game_link       = item.get("gamelink"),
            container_slots = _int(typeinfo.get("slots")),
            extra_info      = self._parse_extra_info(item, typeinfo),
        )

    def _parse_stats(self, modifiers: dict) -> list[ItemStat]:
        stats: list[ItemStat] = []
        seen_display_names: set[str] = set()
        for tag, mod in modifiers.items():
            if not isinstance(mod, dict):
                continue
            key     = tag.lower()
            mapping = STAT_MAP.get(key)
            if mapping:
                display_name, group = mapping
            else:
                api_dn = mod.get("displayname", "")
                # Use the API's displayname only if it looks like a real name (>3 chars)
                display_name = api_dn if (api_dn and len(api_dn) > 3) else key.replace("_", " ").title()
                group = "primary" if mod.get("type") == "attribute" else "secondary"
            # The API sometimes returns "All" as the display name for ability modifier
            if display_name.strip().lower() == "all":
                display_name = "Ability Mod"
                group = "secondary"
            if display_name in seen_display_names:
                continue
            seen_display_names.add(display_name)
            stats.append(ItemStat(
                name         = key,
                display_name = display_name,
                value        = float(mod.get("value", 0)),
                stat_group   = group,
            ))
        return stats

    def _parse_effects(self, effect_list: list, adornment_list: list) -> list[ItemEffect]:
        # Spell/effect names come from adornment_list
        adornment_names: list[str] = [
            a["name"] for a in adornment_list
            if isinstance(a, dict) and a.get("name")
        ]

        # Group flat effect_list into (trigger, [bullet lines]) blocks.
        # indentation=0 → trigger line ("When Equipped:")
        # indentation>0 → bullet line
        groups: list[dict] = []
        current: Optional[dict] = None
        for eff in effect_list:
            indent = int(eff.get("indentation", 0))
            desc   = _str(eff.get("description")) or ""
            if indent == 0:
                if current is not None:
                    groups.append(current)
                current = {"trigger": desc, "lines": []}
            else:
                if current is None:
                    current = {"trigger": "", "lines": []}
                current["lines"].append((indent, desc))
        if current is not None:
            groups.append(current)

        effects: list[ItemEffect] = []
        for i, group in enumerate(groups):
            name = adornment_names[i] if i < len(adornment_names) else "Unknown Effect"
            effects.append(ItemEffect(name=name, trigger=group["trigger"], lines=group["lines"]))
        return effects

    def _parse_extra_info(self, item: dict, typeinfo: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for field, label, fmt in ITEM_DISPLAY:
            val = item.get(field)
            if val is None:
                continue
            if fmt == "charges":
                n = int(val)
                rows.append((label, "Unlimited" if n == -1 else f"{n}/{n}"))
            else:
                rows.append((label, str(val)))
        for field, label, fmt in TYPEINFO_DISPLAY:
            val = typeinfo.get(field)
            if val is None:
                continue
            if fmt == "duration":
                rows.append((label, _fmt_duration(float(val))))
            else:
                rows.append((label, str(val)))
        return rows

    def _parse_flags(self, flags_dict: dict) -> list[str]:
        flags: list[str] = []
        for key, val in flags_dict.items():
            flag_val = val.get("value", 0) if isinstance(val, dict) else val
            if flag_val == 1 or flag_val is True:
                label = _FLAG_LABELS.get(key)
                if label:
                    flags.append(label)
        return flags


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


def _armor_type(typeinfo: dict) -> str:
    knowledgedesc = typeinfo.get("knowledgedesc", "")
    if knowledgedesc and knowledgedesc != "Magic Affinity":
        return knowledgedesc
    # Fall back to building a label from typeinfo color + name (e.g. "Temporary Adornment")
    name  = typeinfo.get("name", "").replace("_", " ").title()
    color = typeinfo.get("color", "").replace("_", " ").title()
    if color and name:
        return f"{color} {name}"
    return name  # may be empty string — that's fine, nothing will render


def _slot_type(slot_list: list, typeinfo: dict) -> str:
    # Top-level slot_list takes priority
    if slot_list:
        return slot_list[0].get("name", "")
    # Adornments and some items store their slot inside typeinfo.slot_list
    ti_slots = typeinfo.get("slot_list") or []
    if ti_slots and isinstance(ti_slots[0], dict):
        return ti_slots[0].get("displayname", "")
    return ""


def _fmt_duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:g} hr"
    if seconds >= 60:
        return f"{seconds / 60:g} min"
    return f"{seconds:g} sec"
