from __future__ import annotations

import io
from typing import Any, Optional

import aiohttp

from census.constants import CLASS_GROUPS, FLAG_FIELDS, STAT_MAP, ALL_CLASSES
from census.models import ItemData, ItemEffect, ItemStat

BASE_URL = "http://census.daybreakgames.com"
ICON_URL_TEMPLATE = "https://census.daybreakgames.com/files/eq2/images/icons/items/{icon_id}.png"


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
        raw = await self.get_raw_item(name)
        if not raw:
            return None
        item_list = raw.get("item_list", [])
        if not item_list:
            return None
        return await self._parse_item(item_list[0])

    async def get_raw_item(self, name: str) -> Optional[dict]:
        url = f"{BASE_URL}/s:{self.service_id}/json/get/eq2/item/"
        params = {"displayname": name, "c:limit": "1"}
        try:
            async with self._session_().get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)
        except Exception as exc:
            print(f"[Census] API error: {exc}")
            return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def _parse_item(self, raw: dict) -> ItemData:
        icon_id = raw.get("icon") or raw.get("icon_id")
        # Icon API is currently broken — store the ID for future use but don't fetch
        icon_bytes = None

        return ItemData(
            id=str(raw.get("id", "")),
            name=raw.get("displayname", "Unknown Item"),
            quality=str(raw.get("quality", "")).lower(),
            description=str(raw.get("description", "")),
            icon_id=str(icon_id) if icon_id else None,
            icon_bytes=icon_bytes,
            slot_type=self._find_str(raw, "slot_type_data.name", "slot_type.name", "slot_type"),
            armor_type=self._find_str(raw, "type_data.name", "item_type_data.name", "item_type"),
            mitigation=self._find_int(raw, "mitigation", "armor_data.mitigation"),
            item_level=self._find_int(raw, "item_level", "level"),
            required_level=self._find_int(raw, "required_level"),
            classes=self._parse_classes(raw),
            stats=self._parse_stats(raw),
            effects=self._parse_effects(raw),
            adornment_slots=self._parse_adornment_slots(raw),
            flags=self._parse_flags(raw),
            game_link=raw.get("gamelink") or raw.get("game_link"),
        )

    def _parse_stats(self, raw: dict) -> list[ItemStat]:
        stat_list: list[Any] = (
            raw.get("item_stat_list")
            or raw.get("stat_list")
            or []
        )
        stats: list[ItemStat] = []
        for entry in stat_list:
            type_name = (
                self._nested(entry, "type", "name")
                or self._nested(entry, "stat_type", "name")
                or entry.get("name")
                or entry.get("type")
            )
            value = entry.get("value") or entry.get("base_value") or 0
            if not type_name:
                continue
            key = str(type_name).lower().replace(" ", "").replace("_", "")
            mapping = STAT_MAP.get(key) or STAT_MAP.get(str(type_name).lower())
            if mapping:
                display_name, group = mapping
            else:
                display_name = str(type_name).replace("_", " ").title()
                group = "secondary"
            stats.append(ItemStat(
                name=str(type_name),
                display_name=display_name,
                value=float(value),
                stat_group=group,
            ))
        return stats

    def _parse_effects(self, raw: dict) -> list[ItemEffect]:
        effect_list: list[Any] = (
            raw.get("equip_effect_list")
            or raw.get("spell_list")
            or raw.get("effect_list")
            or []
        )
        effects: list[ItemEffect] = []
        for entry in effect_list:
            name = (
                self._nested(entry, "spell_data", "displayname")
                or self._nested(entry, "spell", "name")
                or entry.get("displayname")
                or entry.get("name")
                or "Unknown Effect"
            )
            trigger = str(
                entry.get("trigger_type")
                or entry.get("trigger")
                or "When Equipped"
            )
            if trigger and not trigger.endswith(":"):
                trigger += ":"

            description = (
                self._nested(entry, "spell_data", "description")
                or entry.get("description")
                or ""
            )
            lines: list[str] = []
            if description:
                lines = [l.strip() for l in str(description).replace("\\n", "\n").split("\n") if l.strip()]

            for sub in entry.get("description_list") or entry.get("effect_list") or []:
                text = (sub.get("description") or sub.get("text") or "") if isinstance(sub, dict) else str(sub)
                if text:
                    lines.append(str(text))

            effects.append(ItemEffect(name=str(name), trigger=trigger, lines=lines))
        return effects

    def _parse_adornment_slots(self, raw: dict) -> list[str]:
        slot_list: list[Any] = raw.get("adornment_slot_list") or raw.get("adornment_slots") or []
        slots: list[str] = []
        for slot in slot_list:
            if isinstance(slot, dict):
                color = slot.get("color") or slot.get("type") or slot.get("name") or ""
            else:
                color = str(slot)
            if color:
                slots.append(str(color).capitalize())
        return slots

    def _parse_flags(self, raw: dict) -> list[str]:
        seen: set[str] = set()
        flags: list[str] = []

        def add(label: str) -> None:
            if label not in seen:
                seen.add(label)
                flags.append(label)

        for field_name, label in FLAG_FIELDS:
            val = raw.get(field_name)
            if val in (True, 1, "1", "true", "True"):
                add(label)

        raw_flags = raw.get("flags") or []
        if isinstance(raw_flags, list):
            for f in raw_flags:
                name = (f.get("name") or f.get("type") or "") if isinstance(f, dict) else str(f)
                lookup = name.lower().replace("-", "_").replace(" ", "_")
                label = next((lbl for fn, lbl in FLAG_FIELDS if fn == lookup), name.upper())
                add(label)
        elif isinstance(raw_flags, dict):
            for key, val in raw_flags.items():
                if val in (True, 1, "1", "true", "True"):
                    lookup = key.lower()
                    label = next((lbl for fn, lbl in FLAG_FIELDS if fn == lookup), key.upper())
                    add(label)

        return flags

    def _parse_classes(self, raw: dict) -> list[str]:
        class_list: Any = raw.get("classes") or raw.get("class_list") or []
        classes: list[str] = []
        if isinstance(class_list, list):
            for cls in class_list:
                name = (cls.get("name") or cls.get("displayname") or str(cls)) if isinstance(cls, dict) else str(cls)
                classes.append(name)
        elif isinstance(class_list, str):
            classes = [c.strip() for c in class_list.split(",") if c.strip()]
        return classes

    async def _fetch_icon(self, icon_id: Any) -> Optional[bytes]:
        url = ICON_URL_TEMPLATE.format(icon_id=icon_id)
        try:
            async with self._session_().get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except Exception as exc:
            print(f"[Census] Icon fetch failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _nested(self, obj: Any, *keys: str) -> Any:
        for key in keys:
            if not isinstance(obj, dict):
                return None
            obj = obj.get(key)
        return obj

    def _find_str(self, raw: dict, *dot_paths: str) -> str:
        for path in dot_paths:
            keys = path.split(".")
            val = self._nested(raw, *keys)
            if val is not None:
                return str(val)
        return ""

    def _find_int(self, raw: dict, *dot_paths: str) -> Optional[int]:
        for path in dot_paths:
            keys = path.split(".")
            val = self._nested(raw, *keys)
            if val is not None:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    continue
        return None
