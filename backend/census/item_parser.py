from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.census._coerce import coerce_int as _int
from backend.census.constants import ITEM_DISPLAY, STAT_MAP, TYPEINFO_DISPLAY
from backend.census.item_level import compute_ilvl
from backend.census.models import ItemData, ItemEffect, ItemStat, RecipeBookEntry, SetBonusEntry

_ITEM_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "items" / "icons"

# JSON flag key → display label (order = display order in tooltip)
_FLAG_LABELS: dict[str, str] = {
    "heirloom": "HEIRLOOM",
    "lore-equip": "LORE-EQUIP",
    "lore": "LORE",
    "attunable": "ATTUNEABLE",
    "notrade": "NO-TRADE",
    "nozone": "NO-ZONE",
    "novalue": "NO-VALUE",
    "prestige": "PRESTIGE",
    "relic": "RELIC",
}


# ------------------------------------------------------------------
# Low-level helpers (duplicated from client.py to avoid circular import)
# ------------------------------------------------------------------


def _str(value: Any) -> str:
    """Return a string from value, treating dicts/None as empty."""
    if value is None or isinstance(value, dict):
        return ""
    return str(value)


# ------------------------------------------------------------------
# Item-specific helpers
# ------------------------------------------------------------------


def _load_item_icon(icon_id: str) -> bytes | None:
    path = _ITEM_ICONS_DIR / f"{icon_id}.png"
    return path.read_bytes() if path.exists() else None


def _armor_type(typeinfo: dict) -> str:
    knowledgedesc = typeinfo.get("knowledgedesc", "")
    if knowledgedesc and knowledgedesc != "Magic Affinity":
        return knowledgedesc
    # Fall back to building a label from typeinfo color + name (e.g. "Temporary Adornment")
    name = typeinfo.get("name", "").replace("_", " ").title()
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


# ------------------------------------------------------------------
# Public parsing functions
# ------------------------------------------------------------------


def parse_item(item: dict) -> ItemData:
    typeinfo = item.get("typeinfo") or {}
    slot_list = item.get("slot_list") or []

    # Classes: typeinfo.classes is a dict keyed by internal class name
    classes_dict = typeinfo.get("classes") or {}
    classes = [
        v["displayname"] if isinstance(v, dict) and "displayname" in v else k.capitalize()
        for k, v in classes_dict.items()
    ]

    # Level comes from the first class entry; fall back to leveltouse
    first_class = next(iter(classes_dict.values()), None)
    class_level = _int(first_class.get("level")) if isinstance(first_class, dict) else None
    item_level = class_level or _int(item.get("leveltouse"))

    stats = parse_stats(item.get("modifiers") or {})
    # ilvl reads Potency off the already-parsed stats (no second pass, no import cycle).
    potency = next((s.value for s in stats if s.display_name == "Potency"), 0.0)
    ilvl = compute_ilvl(
        level_to_use=_int(item.get("leveltouse")),
        tier_display=item.get("tier"),
        potency=potency,
        item_type=item.get("type"),
        two_handed=typeinfo.get("wieldstyle") == "Two-Handed",
    )

    return ItemData(
        id=str(item.get("id", "")),
        name=item.get("displayname", "Unknown Item"),
        quality=str(item.get("tier", "")).lower(),  # "FABLED" → "fabled"
        description=_str(item.get("description")),
        icon_id=str(item["iconid"]) if item.get("iconid") else None,
        icon_bytes=_load_item_icon(str(item["iconid"])) if item.get("iconid") else None,
        armor_type=_armor_type(typeinfo),
        mitigation=_int(typeinfo.get("maxarmorclass")),
        slot_type=_slot_type(slot_list, typeinfo),
        item_level=item_level,
        required_level=_int(item.get("leveltouse")),
        classes=classes,
        ilvl=ilvl,
        stats=stats,
        effects=parse_effects(
            item.get("effect_list") or [],
            item.get("adornment_list") or [],
        ),
        adornment_slots=[
            s["color"].capitalize()
            for s in (item.get("adornmentslot_list") or [])
            if isinstance(s, dict) and s.get("color")
        ],
        flags=parse_flags(item.get("flags") or {}),
        game_link=item.get("gamelink"),
        container_slots=_int(typeinfo.get("slots")),
        extra_info=parse_extra_info(item, typeinfo),
        set_name=parse_set_name(item),
        set_bonuses=parse_set_bonuses(item),
        recipe_list=[
            RecipeBookEntry(id=str(r.get("id", "")), name=_str(r.get("name")))
            for r in (typeinfo.get("recipe_list") or [])
            if isinstance(r, dict) and r.get("name")
        ],
    )


def parse_stats(modifiers: dict) -> list[ItemStat]:
    stats: list[ItemStat] = []
    seen_display_names: set[str] = set()
    for tag, mod in modifiers.items():
        if not isinstance(mod, dict):
            continue
        key = tag.lower()
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
        stats.append(
            ItemStat(
                name=key,
                display_name=display_name,
                value=float(mod.get("value", 0)),
                stat_group=group,
            )
        )
    return stats


def parse_effects(effect_list: list, adornment_list: list) -> list[ItemEffect]:
    # Spell/effect names come from adornment_list
    adornment_names: list[str] = [a["name"] for a in adornment_list if isinstance(a, dict) and a.get("name")]

    # Group flat effect_list into (trigger, [bullet lines]) blocks.
    # indentation=0 → trigger line ("When Equipped:")
    # indentation>0 → bullet line
    groups: list[dict] = []
    current: dict | None = None
    for eff in effect_list:
        indent = int(eff.get("indentation", 0))
        desc = _str(eff.get("description")) or ""
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
        name = adornment_names[i] if i < len(adornment_names) else ""
        effects.append(ItemEffect(name=name, trigger=group["trigger"], lines=group["lines"]))
    return effects


def parse_extra_info(item: dict, typeinfo: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen_labels: set[str] = set()  # deduplicate — first non-null value wins

    for field, label, fmt in ITEM_DISPLAY:
        if label in seen_labels:
            continue
        val = item.get(field)
        if val is None:
            continue
        if fmt == "charges":
            n = int(val)
            if n == 0:
                continue
            rows.append((label, "Unlimited" if n == -1 else f"{n}/{n}"))
            seen_labels.add(label)
        else:
            if str(val) == "0":
                continue
            rows.append((label, str(val)))
            seen_labels.add(label)

    for field, label, fmt in TYPEINFO_DISPLAY:
        if label in seen_labels:
            continue
        val = typeinfo.get(field)
        if val is None:
            continue
        if fmt == "duration":
            try:
                seconds = float(val)
                if seconds == 0:
                    continue
                rows.append((label, _fmt_duration(seconds)))
                seen_labels.add(label)
            except (TypeError, ValueError):
                # Already a pre-formatted string (e.g. '6 minutes')
                formatted = str(val)
                if formatted in ("0", "0 sec", "0 min", "0 hr"):
                    continue
                rows.append((label, formatted))
                seen_labels.add(label)
        else:
            if str(val) == "0":
                continue
            rows.append((label, str(val)))
            seen_labels.add(label)

    return rows


def parse_flags(flags_dict: dict) -> list[str]:
    flags: list[str] = []
    for key, val in flags_dict.items():
        flag_val = val.get("value", 0) if isinstance(val, dict) else val
        if flag_val == 1 or flag_val is True:
            label = _FLAG_LABELS.get(key)
            if label:
                flags.append(label)
    return flags


def parse_set_name(item: dict) -> str | None:
    """Return the set display name from setbonus_info, or None."""
    info = item.get("setbonus_info")
    if not isinstance(info, dict):
        return None
    return info.get("displayname") or None


# Reserved (non-stat) keys inside a setbonus_list entry — anything else is a
# raw stat shorthand (e.g. {"sta": 120, "blockchance": 10.0}).
_SETBONUS_RESERVED_KEYS = frozenset({"requireditems", "effect"})

# STAT_MAP collapses str/agi/wis/int into the "Primary Attributes" group label
# (the main stat block bundles all four). A set bonus names individual
# attributes, so resolve them to their real names here instead.
_SETBONUS_ATTR_NAMES = {
    "str": "Strength",
    "sta": "Stamina",
    "agi": "Agility",
    "wis": "Wisdom",
    "int": "Intelligence",
}


def _format_setbonus_stat_value(value: Any) -> str:
    """Format a setbonus stat value: ints stay as ints, floats keep their
    fractional part only when non-zero."""
    if isinstance(value, bool):  # bool is a subclass of int — exclude
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def _build_setbonus_effect_from_stats(bonus: dict) -> str:
    """Compose an effect string like '+120 Stamina, +10 Block Chance' from
    raw stat fields when the bonus dict has no 'effect' key.
    Returns '' if no stat-like fields are present."""
    parts: list[str] = []
    for key, value in bonus.items():
        kl = key.lower()
        if kl in _SETBONUS_RESERVED_KEYS or kl.startswith("descriptiontag_"):
            continue
        if value is None or value == "" or value == 0:
            continue
        # Prefer the individual attribute name, then STAT_MAP, then a titlised key.
        if kl in _SETBONUS_ATTR_NAMES:
            display_name = _SETBONUS_ATTR_NAMES[kl]
        elif mapped := STAT_MAP.get(kl):
            display_name = mapped[0]
        else:
            display_name = kl.replace("_", " ").title()
        val_str = _format_setbonus_stat_value(value)
        sign = "" if val_str.startswith("-") else "+"
        parts.append(f"{sign}{val_str} {display_name}")
    return ", ".join(parts)


def parse_set_bonuses(item: dict) -> list[SetBonusEntry]:
    """
    Parse setbonus_list into SetBonusEntry objects.

    Two formats are supported:
      1. Effect-string format: {"requireditems": N, "effect": "...", "descriptiontag_1": ...}
      2. Stat-field format:    {"requireditems": N, "sta": 120, "blockchance": 10.0, ...}
         (common on adornment item-set bonuses)

    Result is sorted ascending by required_items.
    """
    raw = item.get("setbonus_list") or []
    entries: list[SetBonusEntry] = []
    for bonus in raw:
        if not isinstance(bonus, dict):
            continue
        effect = (bonus.get("effect") or "").strip()
        lines: list[str] = []
        i = 1
        while True:
            tag = bonus.get(f"descriptiontag_{i}")
            if tag is None:
                break
            if str(tag).strip():
                lines.append(str(tag).strip())
            i += 1
        # A tier can carry raw stat fields (sta/int/...) *and* an effect string
        # at the same time (e.g. adornment sets: "+135 Stamina ... Applies
        # Enhance: Void Bane."). The stats are the headline in-game, so promote
        # them to the header and drop any applies-effect into the lines below.
        stat_str = _build_setbonus_effect_from_stats(bonus)
        if stat_str:
            if effect:
                lines.insert(0, effect)
            effect = stat_str
        if not effect and not lines:
            continue  # truly empty placeholder tier — skip
        entries.append(
            SetBonusEntry(
                required_items=int(bonus.get("requireditems", 0)),
                effect=effect,
                lines=lines,
            )
        )
    entries.sort(key=lambda e: e.required_items)
    return entries
