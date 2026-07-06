"""Tests for census.spells_db — pure-logic helpers and DB operations."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from backend.eq2db.spells import (
    _passes_spellcheck,
    character_upgradeable_spells,
    find_by_crc,
    find_by_id,
    find_by_ids,
    init_db,
    load_blocklist,
    spell_count,
    spell_to_row,
    strip_roman,
    unique_highest_entries,
    upgradeable_crcs,
    upsert_spells,
)

# ---------------------------------------------------------------------------
# strip_roman
# ---------------------------------------------------------------------------


class TestStripRoman:
    def test_strips_single_digit(self):
        assert strip_roman("Divine Strike I") == "Divine Strike"

    def test_strips_multidigit(self):
        assert strip_roman("Fiery Blast XIV") == "Fiery Blast"

    def test_strips_xx(self):
        assert strip_roman("Something XX") == "Something"

    def test_case_insensitive(self):
        assert strip_roman("Test Spell iv") == "Test Spell"

    def test_leaves_non_roman_words(self):
        assert strip_roman("Healing Touch") == "Healing Touch"

    def test_leaves_roman_in_middle(self):
        # Roman numeral that is NOT a trailing suffix should not be stripped
        assert strip_roman("Mark IV Turret") == "Mark IV Turret"

    def test_empty_string(self):
        assert strip_roman("") == ""

    def test_single_word_roman(self):
        # Just "I" by itself — the regex requires a preceding space, so it is left unchanged
        assert strip_roman("I") == "I"

    def test_strips_xix(self):
        assert strip_roman("Ancient Fury XIX") == "Ancient Fury"


# ---------------------------------------------------------------------------
# _passes_spellcheck
# ---------------------------------------------------------------------------


class TestPassesSpellcheck:
    def test_valid_spell(self):
        row = {"level": 10, "type": "spells", "given_by": "any"}
        assert _passes_spellcheck(row) == 1

    def test_valid_art(self):
        row = {"level": 5, "type": "arts", "given_by": "any"}
        assert _passes_spellcheck(row) == 1

    def test_zero_level_fails(self):
        row = {"level": 0, "type": "spells", "given_by": "any"}
        assert _passes_spellcheck(row) == 0

    def test_negative_level_fails(self):
        row = {"level": -1, "type": "spells", "given_by": "any"}
        assert _passes_spellcheck(row) == 0

    def test_none_level_fails(self):
        row = {"level": None, "type": "spells", "given_by": "any"}
        assert _passes_spellcheck(row) == 0

    def test_wrong_type_fails(self):
        row = {"level": 10, "type": "tradeskill", "given_by": "any"}
        assert _passes_spellcheck(row) == 0

    def test_pcinnate_type_fails(self):
        row = {"level": 10, "type": "pcinnates", "given_by": "any"}
        assert _passes_spellcheck(row) == 0

    def test_given_by_aa_fails(self):
        row = {"level": 10, "type": "spells", "given_by": "alternateadvancement"}
        assert _passes_spellcheck(row) == 0

    def test_given_by_class_fails(self):
        row = {"level": 10, "type": "arts", "given_by": "class"}
        assert _passes_spellcheck(row) == 0

    def test_missing_keys_level_zero(self):
        # Missing level defaults to 0, should fail
        row = {"type": "spells", "given_by": "any"}
        assert _passes_spellcheck(row) == 0


# ---------------------------------------------------------------------------
# spell_to_row
# ---------------------------------------------------------------------------


class TestSpellToRow:
    def _minimal_spell(self, **overrides):
        base = {
            "id": 123456,
            "name": "Divine Strike III",
            "tier_name": "Adept",
            "type": "spells",
            "level": 20,
            "given_by": "any",
            "icon": {"id": 500, "backdrop": 456},
        }
        base.update(overrides)
        return base

    def test_name_and_base_name(self):
        row = spell_to_row(self._minimal_spell())
        assert row["name"] == "Divine Strike III"
        assert row["base_name"] == "Divine Strike"
        assert row["name_lower"] == "divine strike iii"
        assert row["base_name_lower"] == "divine strike"

    def test_icon_fields(self):
        row = spell_to_row(self._minimal_spell())
        assert row["icon_id"] == 500
        assert row["icon_backdrop"] == 456

    def test_icon_missing(self):
        spell = self._minimal_spell()
        del spell["icon"]
        row = spell_to_row(spell)
        assert row["icon_id"] is None
        assert row["icon_backdrop"] is None

    def test_passes_spellcheck_set_correctly(self):
        row = spell_to_row(self._minimal_spell(level=10, type="spells", given_by="any"))
        assert row["passes_spellcheck"] == 1

    def test_passes_spellcheck_zero_for_aa(self):
        row = spell_to_row(self._minimal_spell(given_by="alternateadvancement"))
        assert row["passes_spellcheck"] == 0

    def test_effects_empty_list(self):
        row = spell_to_row(self._minimal_spell())
        assert row["effects"] == "[]"

    def test_effects_parsed(self):
        spell = self._minimal_spell(
            effect_list=[
                {"description": "Heals target", "indentation": 1},
                {"description": "  Also buffs", "indentation": 2},
            ]
        )
        row = spell_to_row(spell)
        parsed = json.loads(row["effects"])
        assert len(parsed) == 2
        assert parsed[0]["description"] == "Heals target"
        assert parsed[0]["indentation"] == 1
        assert parsed[1]["indentation"] == 2

    def test_cast_secs_conversion(self):
        row = spell_to_row(self._minimal_spell(cast_secs_hundredths=150))
        assert row["cast_secs"] == pytest.approx(1.5)

    def test_recovery_secs_conversion(self):
        row = spell_to_row(self._minimal_spell(recovery_secs_tenths=5))
        assert row["recovery_secs"] == pytest.approx(0.5)

    def test_description_dict_becomes_none(self):
        row = spell_to_row(self._minimal_spell(description={}))
        assert row["description"] is None


# ---------------------------------------------------------------------------
# unique_highest_entries
# ---------------------------------------------------------------------------


class TestUniqueHighestEntries:
    def test_keeps_highest_level(self):
        entries = [
            {"name": "Divine Strike I", "type": "spells", "level": 10},
            {"name": "Divine Strike II", "type": "spells", "level": 20},
            {"name": "Divine Strike III", "type": "spells", "level": 30},
        ]
        result = unique_highest_entries(entries)
        assert len(result) == 1
        assert result[0]["level"] == 30

    def test_different_types_kept_separately(self):
        entries = [
            {"name": "Wound I", "type": "spells", "level": 10},
            {"name": "Wound I", "type": "arts", "level": 10},
        ]
        result = unique_highest_entries(entries)
        assert len(result) == 2

    def test_different_base_names_kept(self):
        entries = [
            {"name": "Fireball I", "type": "spells", "level": 10},
            {"name": "Ice Bolt I", "type": "spells", "level": 10},
        ]
        result = unique_highest_entries(entries)
        assert len(result) == 2

    def test_empty_input(self):
        assert unique_highest_entries([]) == []

    def test_single_entry(self):
        entries = [{"name": "Heal", "type": "spells", "level": 15}]
        result = unique_highest_entries(entries)
        assert len(result) == 1

    def test_works_with_spell_entry_objects(self):
        from backend.census.models import SpellEntry

        entries = [
            SpellEntry(name="Divine Strike I", tier="Adept", spell_type="spells", level=10),
            SpellEntry(name="Divine Strike II", tier="Master", spell_type="spells", level=20),
        ]
        result = unique_highest_entries(entries)
        assert len(result) == 1
        assert result[0].level == 20

    def test_mixed_objects_and_dicts(self):
        # Edge case: all dict-based entries with None level default to 0
        entries = [
            {"name": "Fiery Blast I", "type": "spells", "level": None},
            {"name": "Fiery Blast III", "type": "spells", "level": 30},
        ]
        result = unique_highest_entries(entries)
        assert len(result) == 1
        assert result[0]["level"] == 30


# ---------------------------------------------------------------------------
# load_blocklist
# ---------------------------------------------------------------------------


class TestLoadBlocklist:
    def test_reads_json(self, tmp_path):
        p = tmp_path / "blocklist.json"
        p.write_text('{"blocked": ["Fighting Chance I", "Fiery Blast"]}', encoding="utf-8")
        result = load_blocklist(p)
        assert "fighting chance" in result  # Roman stripped
        assert "fiery blast" in result

    def test_returns_empty_if_missing(self, tmp_path):
        p = tmp_path / "nonexistent.json"
        result = load_blocklist(p)
        assert not result  # empty Blocklist is falsy

    def test_all_lowercase(self, tmp_path):
        p = tmp_path / "blocklist.json"
        p.write_text('{"blocked": ["GREAT FIRE BLAST"]}', encoding="utf-8")
        result = load_blocklist(p)
        assert "great fire blast" in result

    def test_invalid_json_returns_empty(self, tmp_path):
        p = tmp_path / "blocklist.json"
        p.write_text("not valid json", encoding="utf-8")
        result = load_blocklist(p)
        assert not result  # empty Blocklist is falsy

    def test_non_string_entries_skipped(self, tmp_path):
        p = tmp_path / "blocklist.json"
        p.write_text('{"blocked": ["Valid Spell", 42, null]}', encoding="utf-8")
        result = load_blocklist(p)
        assert "valid spell" in result
        assert "42" not in result


# ---------------------------------------------------------------------------
# DB operations (find_by_id, find_by_ids, upsert_spells, spell_count)
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "spells.db"
    conn = init_db(path)
    conn.close()
    return path


def _make_spell(
    id: int,
    name: str = "Test Spell I",
    level: int = 10,
    spell_type: str = "spells",
    given_by: str = "any",
    crc: int = 100,
    tier: int = 3,
) -> dict:
    return {
        "id": id,
        "name": name,
        "tier_name": "Adept",
        "tier": tier,
        "type": spell_type,
        "level": level,
        "given_by": given_by,
        "crc": crc,
        "icon": {"id": 500, "backdrop": 456},
    }


class TestUpgradeableCrcs:
    def test_multi_tier_crc_upgradeable_single_tier_not(self, db):
        conn = sqlite3.connect(db)
        upsert_spells(
            [
                # crc 200 — a real spell line with two tiers → upgradeable
                {**_make_spell(1, name="Restoration VI", crc=200, tier=1), "tier_name": "Apprentice"},
                {**_make_spell(2, name="Restoration VI", crc=200, tier=5), "tier_name": "Master"},
                # crc 300 — a single-tier utility cast → NOT upgradeable
                {**_make_spell(3, name="Cure", crc=300, tier=1), "tier_name": "Apprentice"},
            ],
            conn,
        )
        conn.close()
        assert upgradeable_crcs({200, 300}, path=db) == {200}

    def test_empty_input_and_missing_db(self, tmp_path):
        assert upgradeable_crcs(set(), path=tmp_path / "spells.db") == set()
        assert upgradeable_crcs({1, 2}, path=tmp_path / "does_not_exist.db") == set()


class TestCharacterUpgradeableSpells:
    """The single source of truth shared by the spells tab and upgrade checker.
    Keeps scribed/trained/auto-granted upgradeable spells alike; drops AA
    abilities, single-tier utility casts, and level-0 rows."""

    def test_keeps_all_acquisition_paths_drops_aa_and_utility(self, db):
        conn = sqlite3.connect(db)
        upsert_spells(
            [
                # Owned rows (in the character's spell_ids), one per line.
                _make_spell(1, name="Scribed Line I", given_by="spellscroll", crc=201, level=30, tier=1),
                _make_spell(2, name="Trained Line I", given_by="classtraining", crc=202, level=60, tier=1),
                _make_spell(3, name="Base Line I", given_by="class", crc=203, level=40, tier=1),
                _make_spell(4, name="AA Line I", given_by="alternateadvancement", crc=204, level=50, tier=1),
                _make_spell(5, name="Utility Cure", given_by="class", crc=205, level=10, tier=1),
                _make_spell(6, name="Zero Line I", given_by="spellscroll", crc=206, level=0, tier=1),
                # Extra higher tiers (NOT owned) with a DISTINCT tier_name so crc
                # 201-204 span >1 tier = upgradeable. 205 (utility) stays single-
                # tier. 206 excluded by level before the upgradeable check.
                {
                    **_make_spell(11, name="Scribed Line I", given_by="spellscroll", crc=201, level=30),
                    "tier_name": "Master",
                },
                {
                    **_make_spell(12, name="Trained Line I", given_by="classtraining", crc=202, level=60),
                    "tier_name": "Master",
                },
                {**_make_spell(13, name="Base Line I", given_by="class", crc=203, level=40), "tier_name": "Master"},
                {
                    **_make_spell(14, name="AA Line I", given_by="alternateadvancement", crc=204, level=50),
                    "tier_name": "Master",
                },
            ],
            conn,
        )
        conn.close()

        rows = character_upgradeable_spells([1, 2, 3, 4, 5, 6], path=db)
        names = {r["name"] for r in rows}
        assert names == {"Scribed Line I", "Trained Line I", "Base Line I"}
        assert "AA Line I" not in names  # given_by=alternateadvancement → AA tab
        assert "Utility Cure" not in names  # single-tier → not upgradeable
        assert "Zero Line I" not in names  # level 0

    def test_deduplicates_to_highest_owned_tier(self, db):
        conn = sqlite3.connect(db)
        upsert_spells(
            [
                {**_make_spell(1, name="Fireball I", crc=300, level=10), "tier_name": "Apprentice"},
                {**_make_spell(2, name="Fireball II", crc=300, level=20), "tier_name": "Adept"},
            ],
            conn,
        )
        conn.close()
        rows = character_upgradeable_spells([1, 2], path=db)
        assert len(rows) == 1
        assert rows[0]["level"] == 20  # highest owned rank kept

    def test_empty_and_missing_db(self, tmp_path):
        assert character_upgradeable_spells([], path=tmp_path / "spells.db") == []
        assert character_upgradeable_spells([1], path=tmp_path / "missing.db") == []


class TestFindById:
    def test_returns_none_when_db_missing(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        assert find_by_id(9999, path=missing) is None

    def test_returns_none_for_unknown_id(self, db):
        assert find_by_id(9999, path=db) is None

    def test_returns_row_when_present(self, db):
        conn = sqlite3.connect(db)
        upsert_spells([_make_spell(id=1001, name="Firebolt I")], conn)
        conn.close()

        row = find_by_id(1001, path=db)
        assert row is not None
        assert row["id"] == 1001
        assert row["name"] == "Firebolt I"

    def test_row_has_expected_keys(self, db):
        conn = sqlite3.connect(db)
        upsert_spells([_make_spell(id=2001)], conn)
        conn.close()

        row = find_by_id(2001, path=db)
        for key in ("id", "name", "level", "type", "given_by", "tier_name", "passes_spellcheck"):
            assert key in row


class TestFindByIds:
    def test_returns_empty_dict_when_db_missing(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        assert find_by_ids([1, 2, 3], path=missing) == {}

    def test_returns_empty_dict_for_empty_list(self, db):
        assert find_by_ids([], path=db) == {}

    def test_returns_matched_ids_only(self, db):
        conn = sqlite3.connect(db)
        upsert_spells(
            [
                _make_spell(id=10, name="Spell A"),
                _make_spell(id=20, name="Spell B"),
            ],
            conn,
        )
        conn.close()

        result = find_by_ids([10, 20, 999], path=db)
        assert set(result.keys()) == {10, 20}
        assert result[10]["name"] == "Spell A"
        assert result[20]["name"] == "Spell B"
        assert 999 not in result

    def test_values_are_dicts(self, db):
        conn = sqlite3.connect(db)
        upsert_spells([_make_spell(id=50)], conn)
        conn.close()

        result = find_by_ids([50], path=db)
        assert isinstance(result[50], dict)


class TestUpsertSpellsAndCount:
    def test_inserts_rows(self, db):
        conn = sqlite3.connect(db)
        n = upsert_spells(
            [
                _make_spell(id=100, name="Spell One"),
                _make_spell(id=101, name="Spell Two"),
            ],
            conn,
        )
        assert n == 2
        assert spell_count(conn) == 2
        conn.close()

    def test_upsert_replaces_existing(self, db):
        conn = sqlite3.connect(db)
        upsert_spells([_make_spell(id=200, name="Original")], conn)
        upsert_spells([_make_spell(id=200, name="Updated")], conn)
        assert spell_count(conn) == 1

        row = find_by_id(200, path=db)
        assert row["name"] == "Updated"
        conn.close()

    def test_skips_rows_without_id(self, db):
        conn = sqlite3.connect(db)
        spell = _make_spell(id=300)
        del spell["id"]
        n = upsert_spells([spell], conn)
        assert n == 0
        assert spell_count(conn) == 0
        conn.close()


class TestFindByCrc:
    def setup_method(self):
        # Clear the lru_cache before each test so results don't bleed
        find_by_crc.cache_clear()

    def test_returns_none_when_db_missing(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        find_by_crc.cache_clear()
        assert find_by_crc(crc=999, tier=3, path=missing) is None

    def test_returns_exact_tier(self, db):
        conn = sqlite3.connect(db)
        upsert_spells(
            [
                _make_spell(id=1, name="Wound I", crc=555, tier=1),
                _make_spell(id=2, name="Wound II", crc=555, tier=2),
                _make_spell(id=3, name="Wound III", crc=555, tier=3),
            ],
            conn,
        )
        conn.close()

        row = find_by_crc(crc=555, tier=2, path=db)
        assert row is not None
        assert row["tier"] == 2

    def test_falls_back_to_highest_tier(self, db):
        conn = sqlite3.connect(db)
        upsert_spells(
            [
                _make_spell(id=10, name="Bolt I", crc=777, tier=1),
                _make_spell(id=11, name="Bolt III", crc=777, tier=3),
            ],
            conn,
        )
        conn.close()

        # Request tier=2 which doesn't exist → should get tier=3 (highest)
        row = find_by_crc(crc=777, tier=2, path=db)
        assert row is not None
        assert row["tier"] == 3

    def test_lru_cache_returns_same_result(self, db):
        conn = sqlite3.connect(db)
        upsert_spells([_make_spell(id=20, name="Cached Spell", crc=888, tier=1)], conn)
        conn.close()

        result1 = find_by_crc(crc=888, tier=1, path=db)
        result2 = find_by_crc(crc=888, tier=1, path=db)
        assert result1 == result2
        assert result1 is not None
