"""Approximate character-stat deltas between two equipment configurations.

Used by the gear-sets endpoint: for each saved set, "what would the sheet
stats roughly look like wearing this instead of the current gear?" — computed
as Σ item stats(set) − Σ item stats(worn), sourced from items.db's
``item_stats`` table (items + adorns) plus active item-set bonuses.

Deliberate approximations (the sheet marks these values as approximate):
  - Only additive stats are mapped. Percent-derived sheet values (armor /
    avoidance / mitigations, which Census reports as percentages) and
    attribute→pool cascades (stamina→health) are excluded.
  - Proc/effect-only set bonuses ("Applies Enhance: X") carry no numbers to
    move, so only stat-field set-bonus tiers count.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

from backend.census.constants import STAT_MAP
from backend.census.item_parser import _SETBONUS_ATTR_NAMES, _SETBONUS_RESERVED_KEYS
from backend.eq2db.items import catalogue as _items

if TYPE_CHECKING:
    from collections.abc import Iterable

_ALL_ATTRS = ("str_eff", "sta_eff", "agi_eff", "wis_eff", "int_eff")

# item_stats display name (lowercased) → CharacterStats field(s) it moves.
# Names absent here (skills, resolve, flat resists/mitigation, tradeskill
# mods, ...) have no additive sheet field and are skipped.
ITEM_STAT_FIELDS: dict[str, tuple[str, ...]] = {
    "primary attributes": _ALL_ATTRS,
    "all attributes": _ALL_ATTRS,
    "strength": ("str_eff",),
    "stamina": ("sta_eff",),
    "agility": ("agi_eff",),
    "wisdom": ("wis_eff",),
    "intelligence": ("int_eff",),
    "health": ("health_max",),
    "max health": ("health_max",),
    "mana": ("power_max",),
    "max power": ("power_max",),
    "combat health regen": ("health_regen",),
    "combat hp regen": ("health_regen",),
    "combat power regen": ("power_regen",),
    "potency": ("potency",),
    "crit chance": ("crit_chance",),
    "crit bonus": ("crit_bonus",),
    "fervor": ("fervor",),
    "dps": ("dps",),
    "multi attack": ("double_attack",),
    "multi attack chance": ("double_attack",),
    "ability doublecast": ("ability_doublecast",),
    "attack speed": ("attack_speed",),
    "haste": ("attack_speed",),
    "strikethrough": ("strikethrough",),
    "accuracy": ("accuracy",),
    "ability mod": ("ability_mod",),
    "weapon damage": ("weapon_damage_bonus",),
    "flurry": ("flurry",),
    "block chance": ("block_chance",),
    "parry": ("parry",),
    "casting speed": ("casting_speed",),
    "reuse speed": ("reuse_speed",),
    "spell reuse speed": ("reuse_speed",),
    "ability reuse speed": ("reuse_speed",),
    "recovery speed": ("recovery_speed",),
}


def _bonus_stat_fields(key: str) -> tuple[str, ...]:
    """A setbonus_list stat shorthand ('sta', 'blockchance', ...) → sheet
    fields, resolved the same way the tooltip renderer names them."""
    kl = key.lower()
    if kl in _SETBONUS_ATTR_NAMES:
        display = _SETBONUS_ATTR_NAMES[kl]
    elif mapped := STAT_MAP.get(kl):
        display = mapped[0]
    else:
        display = kl
    return ITEM_STAT_FIELDS.get(display.lower(), ())


def _add(totals: dict[str, float], fields: tuple[str, ...], value: float, weight: int = 1) -> None:
    for field in fields:
        totals[field] = totals.get(field, 0.0) + value * weight


def _set_bonus_totals(totals: dict[str, float], counts: Counter[int]) -> None:
    """Add every ACTIVE set-bonus tier for the given equipped-item counts.
    A tier is active when the number of equipped pieces of its set reaches
    ``requireditems``; only numeric stat fields contribute."""
    piece_count: Counter[str] = Counter()
    bonus_source: dict[str, str | None] = {}
    for item_id, set_name, raw_json in _items.set_bonus_rows_for_ids(list(counts)):
        piece_count[set_name] += counts[item_id]
        # Any member's raw_json carries the same setbonus_list — keep one.
        if raw_json:
            bonus_source.setdefault(set_name, raw_json)
    for set_name, count in piece_count.items():
        raw = bonus_source.get(set_name)
        if not raw:
            continue
        try:
            bonus_list = json.loads(raw).get("setbonus_list") or []
        except (ValueError, AttributeError):
            continue
        for bonus in bonus_list:
            if not isinstance(bonus, dict) or int(bonus.get("requireditems", 0)) > count:
                continue
            for key, value in bonus.items():
                kl = key.lower()
                if kl in _SETBONUS_RESERVED_KEYS or kl.startswith("descriptiontag_"):
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                _add(totals, _bonus_stat_fields(key), float(value))


def stat_totals(equipment: Iterable) -> dict[str, float]:
    """Total additive sheet-stat contribution of an equipment list (worn items
    + their adorns + active set bonuses), keyed by CharacterStats field name.
    Duck-typed over EquipmentSlotResponse-shaped slots."""
    counts: Counter[int] = Counter()
    for s in equipment:
        if s.item_id and str(s.item_id).isdigit():
            counts[int(s.item_id)] += 1
        for a in s.adorn_slots:
            if a.adorn_id and str(a.adorn_id).isdigit():
                counts[int(a.adorn_id)] += 1
    totals: dict[str, float] = {}
    for item_id, stat, value in _items.stats_for_ids(list(counts)):
        _add(totals, ITEM_STAT_FIELDS.get(stat.lower(), ()), value, weight=counts[item_id])
    _set_bonus_totals(totals, counts)
    return totals


def compute_stat_deltas(set_equipment: Iterable, current_equipment: Iterable) -> dict[str, float]:
    """Per-field approximation of ``set − worn``, zero-deltas dropped."""
    worn = stat_totals(current_equipment)
    proposed = stat_totals(set_equipment)
    deltas: dict[str, float] = {}
    for field in worn.keys() | proposed.keys():
        delta = round(proposed.get(field, 0.0) - worn.get(field, 0.0), 2)
        if delta:
            deltas[field] = delta
    return deltas
