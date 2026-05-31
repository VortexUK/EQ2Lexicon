"""Tests for census.item_level — the ilvl formula and tier banding."""

from __future__ import annotations

import math

import pytest

from backend.census.item_level import (
    CHARACTER_GEAR_SLOT_COUNT,
    GEAR_TYPES,
    ILVL_POTENCY_WEIGHT,
    adorn_bonus,
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
    def test_fixed_21_slot_denominator(self):
        # One 420 item, every other slot empty -> 420 / 21 (not / 1).
        assert character_ilvl([420.0]) == round(420.0 / 21, 1)

    def test_full_set_of_420(self):
        # All 21 slots at 420 -> 420.
        assert character_ilvl([420.0] * CHARACTER_GEAR_SLOT_COUNT) == 420.0

    def test_appearance_items_count_as_zero(self):
        # Appearance pieces (None ilvl) don't add to the numerator but the
        # denominator stays /21, dragging the average down.
        assert character_ilvl([420.0, 420.0, None]) == round(840.0 / 21, 1)

    def test_two_handed_drops_denominator_to_20(self):
        assert character_ilvl([400.0], two_handed=True) == round(400.0 / 20, 1)
        assert character_ilvl([400.0], two_handed=False) == round(400.0 / 21, 1)

    def test_none_when_no_gear_at_all(self):
        assert character_ilvl([]) is None
        assert character_ilvl([None, None]) is None


class TestAdornBonus:
    def test_level_and_tier(self):
        # (90^2/100^2) * tier 5 * weight 1 = 0.81 * 5 = 4.05.
        assert adorn_bonus(90, "FABLED") == pytest.approx(4.05)
        # Compound tier resolves via tier_band: Mastercrafted Fabled -> 5.
        assert adorn_bonus(90, "MASTERCRAFTED FABLED") == pytest.approx(4.05)

    def test_lower_level_and_tier_worth_less(self):
        assert adorn_bonus(80, "LEGENDARY") < adorn_bonus(90, "FABLED")

    def test_no_level_is_zero(self):
        assert adorn_bonus(None, "FABLED") == 0.0
        assert adorn_bonus(0, "FABLED") == 0.0
