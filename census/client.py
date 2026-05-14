from __future__ import annotations

from typing import Any, Optional

import aiohttp

from census.constants import STAT_MAP
from census.models import ItemData, ItemEffect, ItemStat

BASE_URL = "http://census.daybreakgames.com"

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

    async def get_item(self, name: str) -> Optional[ItemData]:
        data = await self._fetch(name)
        if not data:
            return None
        item_list = data.get("item_list", [])
        if not item_list:
            return None
        return self._parse_item(item_list[0])

    async def get_raw_item(self, name: str) -> Optional[dict]:
        """Return the raw parsed JSON — used by inspect_item.py."""
        return await self._fetch(name)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _fetch(self, name: str) -> Optional[dict]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/item/"
        params = {"displayname": name, "c:limit": "1"}
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    print(f"[Census] HTTP {resp.status}")
                    return None
                return await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {exc}")
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

        return ItemData(
            id          = str(item.get("id", "")),
            name        = item.get("displayname", "Unknown Item"),
            quality     = str(item.get("tier", "")).lower(),      # "FABLED" → "fabled"
            description = _str(item.get("description")),
            icon_id     = str(item["iconid"]) if item.get("iconid") else None,
            icon_bytes  = None,                                    # Icon API currently broken
            armor_type  = typeinfo.get("knowledgedesc", ""),      # "Leather Armor"
            mitigation  = _int(typeinfo.get("maxarmorclass")),
            slot_type   = slot_list[0].get("name", "") if slot_list else "",
            item_level  = _int(item.get("itemlevel") or item.get("leveltouse")),
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
            flags       = self._parse_flags(item.get("flags") or {}),
            game_link   = item.get("gamelink"),
        )

    def _parse_stats(self, modifiers: dict) -> list[ItemStat]:
        stats: list[ItemStat] = []
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
                current["lines"].append(desc)
        if current is not None:
            groups.append(current)

        effects: list[ItemEffect] = []
        for i, group in enumerate(groups):
            name = adornment_names[i] if i < len(adornment_names) else "Unknown Effect"
            effects.append(ItemEffect(name=name, trigger=group["trigger"], lines=group["lines"]))
        return effects

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
