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

# Item types that count as "wearable gear". All three carry an equip slot;
# adornments live under a different type, so this set excludes them for free.
GEAR_TYPES = frozenset({"Armor", "Weapon", "Shield"})

# The number of standard gear slots a character has — the FIXED denominator for
# the average-ilvl calc. The 21 slots are: primary, secondary, ranged, head,
# chest, shoulders, forearms, hands, legs, feet, cloak, neck, 2x ear, 2x ring,
# 2x wrist, waist, 2x charm. Consumable (food/drink/ammo), mount and event slots
# are NOT gear and are excluded (they carry no item ilvl anyway). We key off the
# COUNT rather than slot names because the parsed slot labels are display names
# that vary; the per-item ilvl already tells us which equipped items are gear.
CHARACTER_GEAR_SLOT_COUNT = 21

# Fixed reference level. Deliberately a constant (not the server max level) so an
# item's ilvl never re-bases when the level cap rises.
ILVL_REF = 100.0
# Level baseline — the dominant, level-driven part of the score.
ILVL_LEVEL_WEIGHT = 300.0
# Per-tier step (scaled by the level factor): ~18-23 ilvl per quality band at L90.
ILVL_TIER_WEIGHT = 23.0
# Potency weight on a natural-log curve. ~POT_W*ln(2) ≈ 18 ilvl per potency doubling.
ILVL_POTENCY_WEIGHT = 26.0
# Per-socketed-adorn bonus weight. Each adorn adds a SMALL amount to its host
# item's ilvl from its own level + tier (no potency — most adorns lack it):
# adorn_bonus = (level^2/REF^2) * tier * ADORN_WEIGHT. At weight 1 an L90 fabled
# adorn is ~4 ilvl (~1% of a gear slot) — a gentle nudge for being fully socketed.
ILVL_ADORN_WEIGHT = 1.0

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


def character_ilvl(item_ilvls: list[float | None], *, two_handed: bool = False) -> float | None:
    """Average gear ilvl over the FIXED standard-gear-slot count.

    ``item_ilvls`` is the ilvl of each EQUIPPED item (None for appearance /
    non-gear pieces, which contribute 0). The denominator is the full gear-slot
    count (CHARACTER_GEAR_SLOT_COUNT = 21), NOT the number of items present:
    empty slots and appearance/0-ilvl items legitimately drag the average down —
    a character isn't "fully geared" just because the pieces they *do* wear are
    good.

    The only exception is ``two_handed``: a two-handed weapon fills the primary
    slot while the off-hand is necessarily empty, so the denominator drops by one
    (20) rather than penalising the unavoidable empty off-hand. (The 2H weapon's
    own ilvl is already potency-halved upstream.)

    Returns None only when no equipped item carries an ilvl at all.
    """
    values = [v for v in item_ilvls if v is not None]
    if not values:
        return None
    denom = CHARACTER_GEAR_SLOT_COUNT - (1 if two_handed else 0)
    return round(sum(values) / denom, 1) if denom > 0 else None


def adorn_bonus(level: int | None, tier_display: str | None) -> float:
    """Small ilvl bonus a socketed adornment adds to its host item.

    Derived from the adorn's own level and tier (no potency — most adorns lack
    it): (level^2/REF^2) * tier_band * ILVL_ADORN_WEIGHT. Returns 0 for an adorn
    with no level. Folded into the host item's ilvl when computing a character's
    average (not into the bare item's stored/tooltip ilvl)."""
    if not level or level <= 0:
        return 0.0
    return (level**2 / ILVL_REF**2) * tier_band(tier_display) * ILVL_ADORN_WEIGHT
