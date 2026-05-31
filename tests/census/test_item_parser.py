"""Tests for census.item_parser — helper functions and parse_item smoke test."""

from __future__ import annotations

import pytest

from backend.census.item_parser import (
    _armor_type,
    _fmt_duration,
    _slot_type,
    parse_flags,
    parse_item,
    parse_set_bonuses,
)

# ---------------------------------------------------------------------------
# _armor_type
# ---------------------------------------------------------------------------


class TestArmorType:
    def test_returns_knowledgedesc_when_present(self):
        typeinfo = {"knowledgedesc": "Plate Armor", "name": "heavy_armor", "color": "plate"}
        assert _armor_type(typeinfo) == "Plate Armor"

    def test_skips_magic_affinity_knowledgedesc(self):
        typeinfo = {
            "knowledgedesc": "Magic Affinity",
            "name": "adornment",
            "color": "white",
        }
        result = _armor_type(typeinfo)
        # Falls back to color+name combo
        assert result == "White Adornment"

    def test_returns_empty_knowledgedesc_fallback(self):
        typeinfo = {"knowledgedesc": "", "name": "adornment", "color": "red"}
        result = _armor_type(typeinfo)
        assert result == "Red Adornment"

    def test_fallback_name_only_when_no_color(self):
        typeinfo = {"knowledgedesc": "", "name": "ring", "color": ""}
        result = _armor_type(typeinfo)
        assert result == "Ring"

    def test_underscore_replaced_in_name(self):
        typeinfo = {"knowledgedesc": "", "name": "chain_armor", "color": ""}
        result = _armor_type(typeinfo)
        assert result == "Chain Armor"

    def test_empty_typeinfo(self):
        result = _armor_type({})
        assert result == ""

    def test_none_knowledgedesc(self):
        typeinfo = {"knowledgedesc": None, "name": "bracelet", "color": "yellow"}
        result = _armor_type(typeinfo)
        assert result == "Yellow Bracelet"


# ---------------------------------------------------------------------------
# _slot_type
# ---------------------------------------------------------------------------


class TestSlotType:
    def test_top_level_slot_list_takes_priority(self):
        slot_list = [{"name": "Head"}]
        typeinfo = {"slot_list": [{"displayname": "Finger"}]}
        assert _slot_type(slot_list, typeinfo) == "Head"

    def test_falls_back_to_typeinfo_slot_list(self):
        slot_list = []
        typeinfo = {"slot_list": [{"displayname": "Finger"}]}
        assert _slot_type(slot_list, typeinfo) == "Finger"

    def test_empty_both(self):
        assert _slot_type([], {}) == ""

    def test_typeinfo_slot_list_not_dict_returns_empty(self):
        slot_list = []
        typeinfo = {"slot_list": ["not a dict"]}
        assert _slot_type(slot_list, typeinfo) == ""

    def test_slot_list_missing_name_key(self):
        slot_list = [{"displayname": "Chest"}]  # no "name" key
        assert _slot_type(slot_list, {}) == ""


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(30) == "30 sec"

    def test_exactly_one_minute(self):
        assert _fmt_duration(60) == "1 min"

    def test_minutes_only(self):
        assert _fmt_duration(300) == "5 min"

    def test_exactly_one_hour(self):
        assert _fmt_duration(3600) == "1 hr"

    def test_multiple_hours(self):
        assert _fmt_duration(7200) == "2 hr"

    def test_fractional_seconds(self):
        result = _fmt_duration(45.5)
        assert "45.5" in result or "45" in result  # format may vary slightly

    def test_fractional_minutes(self):
        result = _fmt_duration(90)
        assert "1.5" in result or "90" in result  # 90 sec → "1.5 min"

    def test_zero_seconds(self):
        result = _fmt_duration(0)
        assert "0" in result


# ---------------------------------------------------------------------------
# parse_flags
# ---------------------------------------------------------------------------


class TestParseFlags:
    def test_single_flag_set(self):
        flags = {"notrade": {"value": 1}}
        result = parse_flags(flags)
        assert "NO-TRADE" in result

    def test_multiple_flags(self):
        flags = {
            "heirloom": {"value": 1},
            "lore": {"value": 1},
            "notrade": {"value": 0},
        }
        result = parse_flags(flags)
        assert "HEIRLOOM" in result
        assert "LORE" in result
        assert "NO-TRADE" not in result

    def test_unrecognised_flag_skipped(self):
        flags = {"unknown_flag": {"value": 1}}
        result = parse_flags(flags)
        assert result == []

    def test_empty_flags(self):
        assert parse_flags({}) == []

    def test_prestige_flag(self):
        flags = {"prestige": {"value": 1}}
        assert "PRESTIGE" in parse_flags(flags)

    def test_relic_flag(self):
        flags = {"relic": {"value": 1}}
        assert "RELIC" in parse_flags(flags)

    def test_plain_int_value(self):
        # Non-dict value: plain 1
        flags = {"lore": 1}
        result = parse_flags(flags)
        assert "LORE" in result


# ---------------------------------------------------------------------------
# parse_set_bonuses
# ---------------------------------------------------------------------------


class TestParseSetBonuses:
    def test_empty_list(self):
        assert parse_set_bonuses({}) == []

    def test_skips_bonuses_without_effect(self):
        item = {"setbonus_list": [{"requireditems": 2, "effect": ""}]}
        assert parse_set_bonuses(item) == []

    def test_parses_single_bonus(self):
        item = {
            "setbonus_list": [
                {
                    "requireditems": 3,
                    "effect": "Applies Focus: Smite",
                    "descriptiontag_1": "+100 potency",
                }
            ]
        }
        result = parse_set_bonuses(item)
        assert len(result) == 1
        assert result[0].required_items == 3
        assert result[0].effect == "Applies Focus: Smite"
        assert "+100 potency" in result[0].lines

    def test_sorted_by_required_items(self):
        item = {
            "setbonus_list": [
                {"requireditems": 5, "effect": "Five piece"},
                {"requireditems": 2, "effect": "Two piece"},
                {"requireditems": 3, "effect": "Three piece"},
            ]
        }
        result = parse_set_bonuses(item)
        assert [e.required_items for e in result] == [2, 3, 5]

    def test_multiple_description_tags(self):
        item = {
            "setbonus_list": [
                {
                    "requireditems": 2,
                    "effect": "Some effect",
                    "descriptiontag_1": "Line one",
                    "descriptiontag_2": "Line two",
                    "descriptiontag_3": "Line three",
                }
            ]
        }
        result = parse_set_bonuses(item)
        assert result[0].lines == ["Line one", "Line two", "Line three"]

    def test_stats_and_effect_combined(self):
        # A tier with raw stat fields AND an effect string (e.g. adornment sets):
        # stats become the headline, the applies-effect drops into the lines.
        item = {
            "setbonus_list": [
                {
                    "int": 120,
                    "sta": 135,
                    "requireditems": 2,
                    "effect": "Applies Enhance: Void Bane.",
                    "descriptiontag_1": "Enhances the base damage of Void Bane.",
                }
            ]
        }
        result = parse_set_bonuses(item)
        assert result[0].effect == "+120 Intelligence, +135 Stamina"
        assert result[0].lines == [
            "Applies Enhance: Void Bane.",
            "Enhances the base damage of Void Bane.",
        ]

    def test_individual_attributes_named(self):
        # str/agi/wis/int are named individually in a set bonus, not collapsed
        # into the "Primary Attributes" group label used by the main stat block.
        item = {"setbonus_list": [{"requireditems": 2, "int": 50, "wis": 30}]}
        result = parse_set_bonuses(item)
        assert result[0].effect == "+50 Intelligence, +30 Wisdom"

    def test_empty_description_tags_skipped(self):
        item = {
            "setbonus_list": [
                {
                    "requireditems": 2,
                    "effect": "Effect",
                    "descriptiontag_1": "Real line",
                    "descriptiontag_2": "  ",  # whitespace only → stripped and skipped
                    "descriptiontag_3": "Another line",
                }
            ]
        }
        result = parse_set_bonuses(item)
        # Whitespace-only tags are skipped
        assert "  " not in result[0].lines


# ---------------------------------------------------------------------------
# parse_item — smoke test
# ---------------------------------------------------------------------------


class TestParseItem:
    def _minimal_item(self):
        return {
            "id": "99999",
            "displayname": "Faded Hood",
            "tier": "FABLED",
            "iconid": "1234",
            "typeinfo": {
                "knowledgedesc": "Cloth Armor",
                "classes": {"wizard": {"displayname": "Wizard", "level": 90}},
            },
            "slot_list": [{"name": "Head"}],
            "flags": {},
            "modifiers": {},
            "effect_list": [],
            "adornment_list": [],
            "adornmentslot_list": [],
            "setbonus_list": [],
        }

    def test_returns_item_data(self):
        from backend.census.models import ItemData

        item = parse_item(self._minimal_item())
        assert isinstance(item, ItemData)

    def test_name(self):
        item = parse_item(self._minimal_item())
        assert item.name == "Faded Hood"

    def test_id(self):
        item = parse_item(self._minimal_item())
        assert item.id == "99999"

    def test_quality_lowercased(self):
        item = parse_item(self._minimal_item())
        assert item.quality == "fabled"

    def test_slot_type(self):
        item = parse_item(self._minimal_item())
        assert item.slot_type == "Head"

    def test_armor_type(self):
        item = parse_item(self._minimal_item())
        assert item.armor_type == "Cloth Armor"

    def test_classes(self):
        item = parse_item(self._minimal_item())
        assert "Wizard" in item.classes

    def test_no_stats_for_empty_modifiers(self):
        item = parse_item(self._minimal_item())
        assert item.stats == []

    def test_no_effects_for_empty_effect_list(self):
        item = parse_item(self._minimal_item())
        assert item.effects == []

    def test_no_flags_for_empty_flags(self):
        item = parse_item(self._minimal_item())
        assert item.flags == []


class TestParseItemIlvl:
    def _gear(self, *, item_type="Armor", tier="FABLED", leveltouse=100, potency=None):
        modifiers = {}
        if potency is not None:
            modifiers["potency"] = {"value": potency, "displayname": "Potency"}
        return {
            "id": "1",
            "displayname": "Test Gear",
            "type": item_type,
            "tier": tier,
            "leveltouse": leveltouse,
            "typeinfo": {"classes": {}},
            "slot_list": [{"name": "Chest"}],
            "flags": {},
            "modifiers": modifiers,
            "effect_list": [],
            "adornment_list": [],
            "adornmentslot_list": [],
            "setbonus_list": [],
        }

    def test_gear_gets_numeric_ilvl(self):
        # Fabled (5), level 100, no potency -> (1.0) * (300 + 23*5) = 415.
        assert parse_item(self._gear()).ilvl == 415.0

    def test_potency_boosts_ilvl(self):
        # Potency adds a positive log bonus on top of the base.
        assert parse_item(self._gear(potency=1000.0)).ilvl > 415.0

    def test_weapon_and_shield_are_gear(self):
        assert parse_item(self._gear(item_type="Weapon")).ilvl == 415.0
        assert parse_item(self._gear(item_type="Shield")).ilvl == 415.0

    def test_non_gear_has_no_ilvl(self):
        assert parse_item(self._gear(item_type="Spell Scroll")).ilvl is None

    def test_gear_without_level_has_no_ilvl(self):
        assert parse_item(self._gear(leveltouse=0)).ilvl is None
