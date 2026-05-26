"""Tests for census.item_level — the ilvl formula and tier banding."""

from __future__ import annotations

import math

import pytest

from census.item_level import (
    GEAR_TYPES,
    ILVL_POTENCY_WEIGHT,
    character_ilvl,
    compute_ilvl,
    tier_band,
)


@pytest.mark.parametrize(
    "tier_string,expected",
    [
        ("COMMON", 1),
        ("UNCOMMON", 2),
        ("HANDCRAFTED", 2),
        ("TREASURED", 3),
        ("MASTERCRAFTED", 4),
        ("LEGENDARY", 4),
        ("FABLED", 5),
        ("CELESTIAL", 6),
        ("MYTHICAL", 6),
        ("ETHEREAL", 6),
        # Compound strings take the strongest keyword.
        ("MASTERCRAFTED LEGENDARY", 4),
        ("MASTERCRAFTED FABLED", 5),
        ("MASTERCRAFTED MYTHICAL", 6),
        ("MASTERCRAFTED CELESTIAL", 6),
        # Case-insensitive; mixed case from the live parser.
        ("Fabled", 5),
        # Unknown / empty -> band 1.
        ("", 1),
        (None, 1),
        ("Glowing", 1),
    ],
)
def test_tier_band(tier_string, expected):
    assert tier_band(tier_string) == expected


def test_uncommon_substring_does_not_demote_to_common():
    # "uncommon" contains "common"; max() must still pick 2.
    assert tier_band("UNCOMMON") == 2


def test_non_gear_has_no_ilvl():
    assert compute_ilvl(100, "FABLED", 0.0, "Spell Scroll") is None
    assert compute_ilvl(100, "FABLED", 0.0, "House Item") is None
    assert compute_ilvl(100, "FABLED", 0.0, None) is None


def test_gear_types_membership():
    assert GEAR_TYPES == {"Armor", "Weapon", "Shield"}


def test_missing_level_has_no_ilvl():
    assert compute_ilvl(None, "FABLED", 0.0, "Armor") is None
    assert compute_ilvl(0, "FABLED", 0.0, "Armor") is None


def test_no_potency_returns_base():
    # Fabled (tier 5), level 100: (100^2/100^2=1) * (300 + 23*5) = 415, potency 0 adds nothing.
    assert compute_ilvl(100, "FABLED", 0.0, "Armor") == 415.0


def test_potency_at_or_below_one_adds_nothing():
    # The log term is floored: potency <= 1 (incl. none) contributes 0.
    assert compute_ilvl(100, "FABLED", 0.0, "Weapon") == 415.0
    assert compute_ilvl(100, "FABLED", 1.0, "Weapon") == 415.0
    assert compute_ilvl(100, "FABLED", 0.5, "Weapon") == 415.0


def test_potency_is_a_log_bonus():
    # potency = e -> ln(e)=1 -> +POT_W on top of the base.
    base = compute_ilvl(100, "FABLED", 0.0, "Weapon")
    boosted = compute_ilvl(100, "FABLED", math.e, "Weapon")
    assert boosted == pytest.approx(base + ILVL_POTENCY_WEIGHT)


def test_equal_potency_ratio_gives_equal_step():
    # A +9% potency bump adds the same ilvl at any scale (the log property).
    def step(p):
        return compute_ilvl(90, "FABLED", p * 1.09, "Armor") - compute_ilvl(90, "FABLED", p, "Armor")

    assert step(6.6) == pytest.approx(step(10000.0), abs=0.05)


def test_two_handed_halves_potency():
    # A 2H weapon's potency is halved: its ilvl equals a 1H with half the potency.
    one_hand = compute_ilvl(100, "FABLED", 500.0, "Weapon")
    two_hand = compute_ilvl(100, "FABLED", 1000.0, "Weapon", two_handed=True)
    assert two_hand == pytest.approx(one_hand)


def test_two_handed_subtracts_log_two():
    # Halving potency under a log curve subtracts a constant POT_W * ln(2).
    full = compute_ilvl(90, "FABLED", 4000.0, "Weapon")
    halved = compute_ilvl(90, "FABLED", 4000.0, "Weapon", two_handed=True)
    assert full - halved == pytest.approx(ILVL_POTENCY_WEIGHT * math.log(2), abs=0.15)


@pytest.mark.parametrize(
    "level,tier,potency,expected",
    [
        # base = (L^2/100^2) * (300 + 23*tier); potency adds 26*ln(p) when p>1.
        (100, "FABLED", 0.0, 415.0),  # 1.0 * (300+115)
        (90, "FABLED", 6.6, 385.2),  # 0.81*415 + 26*ln(6.6)
        (90, "FABLED", 7.2, 387.5),
        (90, "LEGENDARY", 6.2, 365.0),  # 0.81*(300+92) + 26*ln(6.2)
        (80, "MYTHICAL", 5.1, 322.7),  # 0.64*(300+138) + 26*ln(5.1)
    ],
)
def test_worked_examples(level, tier, potency, expected):
    assert compute_ilvl(level, tier, potency, "Armor") == pytest.approx(expected, abs=0.1)


class TestCharacterIlvl:
    def test_averages_gear_slots(self):
        equipped = [("head", 400.0), ("chest", 300.0), ("legs", 200.0)]
        assert character_ilvl(equipped) == 300.0

    def test_ignores_non_gear_slots(self):
        # ammo/food/drink/event_slot are not in CHARACTER_GEAR_SLOTS.
        equipped = [("chest", 400.0), ("food", 999.0), ("ammo", 999.0), ("event_slot", 0.0)]
        assert character_ilvl(equipped) == 400.0

    def test_ignores_slots_without_ilvl(self):
        # An appearance cloak (no ilvl) doesn't drag the average.
        equipped = [("chest", 400.0), ("cloak", None)]
        assert character_ilvl(equipped) == 400.0

    def test_two_handed_not_penalised_for_empty_offhand(self):
        # 2H in primary (halved ilvl), secondary empty -> only primary counts.
        one_hander = character_ilvl([("primary", 300.0), ("secondary", 300.0), ("chest", 300.0)])
        two_hander = character_ilvl([("primary", 300.0), ("chest", 300.0)])
        assert one_hander == two_hander == 300.0

    def test_none_when_no_gear(self):
        assert character_ilvl([]) is None
        assert character_ilvl([("food", 500.0), ("cloak", None)]) is None
