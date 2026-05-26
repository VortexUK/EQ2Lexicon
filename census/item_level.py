"""Item level ("ilvl") — a single WoW-style power number for wearable gear.

Single source of the ilvl formula. Pure and dependency-free so it can be reused
by the item parser (live tooltip), the DB upsert path (materialised column), and
the backfill script, all guaranteeing the same result.

    ilvl = (L^2 / REF^2) * (LVL_W + TIER_W * Tier) + POT_W * ln(Potency)

Tier is an additive contributor to the level base (a modest per-quality step),
not a whole-formula multiplier. Potency is on a natural-log curve: equal
*percentage* changes produce equal ilvl steps at any scale, so single-digit TLE
potencies and tens-of-thousands live potencies both behave sensibly. Potency <= 1
(including the ~37% of gear with none) contributes 0.

See docs/superpowers/specs/2026-05-26-item-ilvl-design.md for the rationale.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

# Item types that count as "wearable gear". All three carry an equip slot;
# adornments live under a different type, so this set excludes them for free.
GEAR_TYPES = frozenset({"Armor", "Weapon", "Shield"})

# Equipment slots that count toward a character's average ilvl. The standard
# gear slots only — excludes ammo, food, drink, mount_adornment, mount_armor and
# event_slot (which can hold an off-level token that would skew the average).
CHARACTER_GEAR_SLOTS = frozenset(
    {
        "primary",
        "secondary",
        "head",
        "chest",
        "shoulders",
        "forearms",
        "hands",
        "legs",
        "feet",
        "left_ring",
        "right_ring",
        "ears",
        "ears2",
        "neck",
        "left_wrist",
        "right_wrist",
        "ranged",
        "waist",
        "cloak",
        "activate1",
        "activate2",
    }
)

# Fixed reference level. Deliberately a constant (not the server max level) so an
# item's ilvl never re-bases when the level cap rises.
ILVL_REF = 100.0
# Level baseline — the dominant, level-driven part of the score.
ILVL_LEVEL_WEIGHT = 300.0
# Per-tier step (scaled by the level factor): ~18-23 ilvl per quality band at L90.
ILVL_TIER_WEIGHT = 23.0
# Potency weight on a natural-log curve. ~POT_W*ln(2) ≈ 18 ilvl per potency doubling.
ILVL_POTENCY_WEIGHT = 26.0

# Quality keyword -> band (1-6). A tier string maps to the band of the strongest
# keyword it contains, so compound strings ("Mastercrafted Legendary") resolve
# to their highest quality.
_TIER_KEYWORD_BANDS: tuple[tuple[str, int], ...] = (
    ("common", 1),
    ("uncommon", 2),
    ("handcrafted", 2),
    ("treasured", 3),
    ("mastercrafted", 4),
    ("legendary", 4),
    ("fabled", 5),
    ("celestial", 6),
    ("mythical", 6),
    ("ethereal", 6),
)


def tier_band(tier_display: str | None) -> int:
    """Map a tier/quality string to its 1-6 band.

    Takes the strongest keyword present, so "Mastercrafted Celestial" -> 6 and
    "Mastercrafted Legendary" -> 4. (The "common" substring inside "uncommon" is
    harmless: max() picks 2.) Unknown/empty -> 1.
    """
    if not tier_display:
        return 1
    s = tier_display.lower()
    bands = [band for keyword, band in _TIER_KEYWORD_BANDS if keyword in s]
    return max(bands) if bands else 1


def compute_ilvl(
    level_to_use: int | None,
    tier_display: str | None,
    potency: float,
    item_type: str | None,
    two_handed: bool = False,
) -> float | None:
    """Return the item level for a piece of wearable gear, or None if out of scope.

    None when the item is not gear (type not in GEAR_TYPES) or has no equip level
    (heritage/appearance pieces) — both render as "no ilvl" rather than a
    misleading 0.

    ``two_handed`` halves the potency: a two-handed weapon occupies both weapon
    slots and so carries ~2x a one-hander's budget. Halving normalises it to a
    one-hand-equivalent ilvl, so a per-character average can simply count it as a
    single slot (dropping the empty off-hand).
    """
    if item_type not in GEAR_TYPES:
        return None
    if not level_to_use or level_to_use <= 0:
        return None
    tier = tier_band(tier_display)
    level_factor = level_to_use**2 / ILVL_REF**2
    base = level_factor * (ILVL_LEVEL_WEIGHT + ILVL_TIER_WEIGHT * tier)
    effective_potency = (potency or 0.0) / 2.0 if two_handed else (potency or 0.0)
    # Potency on a log curve; <=1 (incl. none) contributes nothing.
    potency_bonus = ILVL_POTENCY_WEIGHT * math.log(effective_potency) if effective_potency > 1 else 0.0
    return round(base + potency_bonus, 1)


def character_ilvl(equipped: Iterable[tuple[str, float | None]]) -> float | None:
    """Average ilvl of a character's equipped gear.

    ``equipped`` is an iterable of ``(slot_name, item_ilvl)`` for each equipped
    slot. Only the standard gear slots (CHARACTER_GEAR_SLOTS) that hold an
    ilvl-bearing item are averaged — consumables, mounts, the event slot, and
    appearance pieces (no ilvl) are ignored. A two-handed weapon contributes its
    (already-halved) ilvl in ``primary`` while the empty ``secondary`` simply
    isn't counted, so it's never penalised for the empty off-hand.

    Returns None when no qualifying gear is equipped.
    """
    values = [ilvl for slot, ilvl in equipped if slot in CHARACTER_GEAR_SLOTS and ilvl is not None]
    return round(sum(values) / len(values), 1) if values else None
