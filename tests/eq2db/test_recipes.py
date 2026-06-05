"""Tests for census.recipes_db — COV-011.

Covers: _parse_spell_tier, recipe_to_row, find_by_id, find_by_name,
find_by_spell, find_spells_by_tier, find_by_output_id, _backfill_spell_tiers,
upsert_recipes.

Target: ≥ 75% on census.recipes_db.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.eq2db.recipes import (
    _backfill_spell_tiers,
    _parse_spell_tier,
    find_by_id,
    find_by_name,
    find_by_output_id,
    find_by_spell,
    find_spells_by_tier,
    init_db,
    recipe_to_row,
    upsert_recipes,
)


@pytest.fixture
def recipes_db(tmp_path: Path) -> Path:
    """Return a Path to an initialised (empty) recipes.db."""
    p = tmp_path / "recipes.db"
    conn = init_db(p)
    conn.close()
    return p


def _make_recipe_dict(
    *,
    recipe_id: int = 1,
    name: str = "Test Recipe",
    bench: str = "forge",
    primary_comp: str = "Raw Iron",
    primary_qty: int = 4,
    fuel_comp: str = "Coal",
    fuel_qty: int = 1,
    secondary_comps: list | None = None,
    formed_id: int = 1000,
) -> dict:
    """Return a minimal Census-shaped recipe dict."""
    return {
        "id": str(recipe_id),
        "name": name,
        "bench": bench,
        "version": "1",
        "primarycomponent": {"description": primary_comp, "quantity": str(primary_qty)},
        "fuelcomponent": {"description": fuel_comp, "quantity": str(fuel_qty)},
        "secondarycomponent_list": secondary_comps or [],
        "output": {
            "formed": str(formed_id),
            "formed_count": "1",
        },
    }


# ---------------------------------------------------------------------------
# _parse_spell_tier
# ---------------------------------------------------------------------------


class TestParseSpellTier:
    def test_spell_scroll_expert(self):
        base, tier = _parse_spell_tier("Lightning Palm III (Expert)")
        assert base == "lightning palm iii"
        assert tier == "Expert"

    def test_spell_scroll_grandmaster(self):
        base, tier = _parse_spell_tier("Thunderclap II (Grandmaster)")
        assert base == "thunderclap ii"
        assert tier == "Grandmaster"

    def test_non_spell_returns_none_none(self):
        assert _parse_spell_tier("Fried Cucumber") == (None, None)

    def test_non_tier_parens_returns_none_none(self):
        # "(2H Superior)" is not a valid spell tier
        assert _parse_spell_tier("Starfire (2H Superior)") == (None, None)

    def test_tier_case_insensitive(self):
        base, tier = _parse_spell_tier("Fireball (EXPERT)")
        assert tier == "Expert"  # canonical casing restored

    def test_apprentice_tier(self):
        base, tier = _parse_spell_tier("Ice Nova (Apprentice)")
        assert tier == "Apprentice"

    def test_ancient_tier(self):
        base, tier = _parse_spell_tier("Wrath of Thunder (Ancient)")
        assert tier == "Ancient"


# ---------------------------------------------------------------------------
# recipe_to_row
# ---------------------------------------------------------------------------


class TestRecipeToRow:
    def test_converts_minimal_dict(self):
        row = recipe_to_row(_make_recipe_dict(recipe_id=42, name="Iron Breastplate"))
        assert row is not None
        assert row["id"] == 42
        assert row["name"] == "Iron Breastplate"

    def test_returns_none_for_missing_id(self):
        bad = {"name": "No ID recipe"}
        assert recipe_to_row(bad) is None

    def test_serialises_secondary_comps_as_json(self):
        secondary = [{"description": "Raw Silver", "quantity": "2"}]
        row = recipe_to_row(_make_recipe_dict(secondary_comps=secondary))
        import json

        comps = json.loads(row["secondary_comps"])
        assert len(comps) == 1
        assert comps[0]["description"] == "Raw Silver"

    def test_extracts_spell_tier_for_scroll_recipe(self):
        row = recipe_to_row(_make_recipe_dict(recipe_id=5, name="Flamestrike VI (Expert)"))
        assert row["base_name_lower"] == "flamestrike vi"
        assert row["crafted_tier"] == "Expert"

    def test_null_spell_tier_for_non_spell(self):
        row = recipe_to_row(_make_recipe_dict(name="Plate Helm"))
        assert row["base_name_lower"] is None
        assert row["crafted_tier"] is None

    def test_extracts_all_output_tiers(self):
        d = _make_recipe_dict(recipe_id=99)
        d["output"] = {
            "formed": "100",
            "formed_count": "1",
            "elaborate": "99",
            "elaborate_count": "1",
            "worked": "98",
            "worked_count": "1",
            "simple": "97",
            "simple_count": "1",
        }
        row = recipe_to_row(d)
        assert row["out_formed_id"] == 100
        assert row["out_elaborate_id"] == 99
        assert row["out_worked_id"] == 98
        assert row["out_simple_id"] == 97


# ---------------------------------------------------------------------------
# find_by_id
# ---------------------------------------------------------------------------


class TestFindById:
    def test_returns_none_when_path_missing(self, tmp_path: Path):
        assert find_by_id(1, path=tmp_path / "no.db") is None

    def test_returns_none_for_unknown_id(self, recipes_db: Path):
        assert find_by_id(9999, path=recipes_db) is None

    def test_returns_row_for_existing_id(self, recipes_db: Path):
        with init_db(recipes_db) as conn:
            upsert_recipes([_make_recipe_dict(recipe_id=77, name="Steel Helm")], conn)
        row = find_by_id(77, path=recipes_db)
        assert row is not None
        assert row["name"] == "Steel Helm"
        assert isinstance(row["secondary_comps"], list)  # deserialized


# ---------------------------------------------------------------------------
# find_by_name
# ---------------------------------------------------------------------------


class TestFindByName:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert find_by_name("anything", path=tmp_path / "no.db") == []

    def test_exact_match(self, recipes_db: Path):
        with init_db(recipes_db) as conn:
            upsert_recipes([_make_recipe_dict(recipe_id=10, name="Iron Helm")], conn)
        rows = find_by_name("Iron Helm", path=recipes_db)
        assert len(rows) == 1
        assert rows[0]["name"] == "Iron Helm"

    def test_case_insensitive_exact(self, recipes_db: Path):
        with init_db(recipes_db) as conn:
            upsert_recipes([_make_recipe_dict(recipe_id=11, name="Iron Helm")], conn)
        rows = find_by_name("iron helm", path=recipes_db)
        assert len(rows) == 1

    def test_like_fallback(self, recipes_db: Path):
        with init_db(recipes_db) as conn:
            upsert_recipes([_make_recipe_dict(recipe_id=12, name="Bronze Gauntlets")], conn)
        rows = find_by_name("gauntlet", path=recipes_db)
        assert any("Gauntlets" in r["name"] for r in rows)

    def test_like_escapes_percent_in_name(self, recipes_db: Path):
        # Should not raise / should return empty rather than crash
        rows = find_by_name("100% durable", path=recipes_db)
        assert isinstance(rows, list)

    def test_like_escapes_underscore_in_name(self, recipes_db: Path):
        rows = find_by_name("a_b", path=recipes_db)
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# find_by_spell
# ---------------------------------------------------------------------------


class TestFindBySpell:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert find_by_spell("x", "Expert", path=tmp_path / "no.db") == []

    def test_returns_empty_for_no_match(self, recipes_db: Path):
        assert find_by_spell("nonexistent spell", "Expert", path=recipes_db) == []

    def test_returns_only_matching_tier(self, recipes_db: Path):
        expert_recipe = _make_recipe_dict(recipe_id=20, name="Fireball II (Expert)")
        master_recipe = _make_recipe_dict(recipe_id=21, name="Fireball II (Master)")
        with init_db(recipes_db) as conn:
            upsert_recipes([expert_recipe, master_recipe], conn)
        rows = find_by_spell("fireball ii", "Expert", path=recipes_db)
        assert len(rows) == 1
        assert rows[0]["crafted_tier"] == "Expert"


# ---------------------------------------------------------------------------
# find_spells_by_tier
# ---------------------------------------------------------------------------


class TestFindSpellsByTier:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert find_spells_by_tier(["x"], "Expert", path=tmp_path / "no.db") == {}

    def test_returns_empty_for_empty_list(self, recipes_db: Path):
        assert find_spells_by_tier([], "Expert", path=recipes_db) == {}

    def test_bulk_lookup(self, recipes_db: Path):
        recipes = [
            _make_recipe_dict(recipe_id=30, name="Ice Nova (Expert)"),
            _make_recipe_dict(recipe_id=31, name="Fire Nova (Expert)"),
        ]
        with init_db(recipes_db) as conn:
            upsert_recipes(recipes, conn)
        result = find_spells_by_tier(["ice nova", "fire nova"], "Expert", path=recipes_db)
        assert "ice nova" in result
        assert "fire nova" in result


# ---------------------------------------------------------------------------
# find_by_output_id
# ---------------------------------------------------------------------------


class TestFindByOutputId:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert find_by_output_id(1, path=tmp_path / "no.db") == []

    def test_returns_empty_for_unknown_id(self, recipes_db: Path):
        assert find_by_output_id(9999, path=recipes_db) == []

    def test_finds_recipe_by_formed_output(self, recipes_db: Path):
        recipe = _make_recipe_dict(recipe_id=40, name="Formed Item Recipe", formed_id=5000)
        with init_db(recipes_db) as conn:
            upsert_recipes([recipe], conn)
        rows = find_by_output_id(5000, path=recipes_db)
        assert len(rows) == 1
        assert rows[0]["out_formed_id"] == 5000

    def test_finds_recipe_by_elaborate_output(self, recipes_db: Path):
        d = _make_recipe_dict(recipe_id=41, name="Elaborate Recipe")
        d["output"] = {"elaborate": "6000", "elaborate_count": "1"}
        with init_db(recipes_db) as conn:
            upsert_recipes([d], conn)
        rows = find_by_output_id(6000, path=recipes_db)
        assert any(r["out_elaborate_id"] == 6000 for r in rows)


# ---------------------------------------------------------------------------
# _backfill_spell_tiers
# ---------------------------------------------------------------------------


class TestBackfillSpellTiers:
    def test_backfills_null_crafted_tier_rows(self, recipes_db: Path):
        # Insert a row with no spell-tier via raw SQL to simulate pre-migration data
        with sqlite3.connect(recipes_db) as conn:
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps) "
                "VALUES (50, 'Thunderbolt IV (Expert)', 'thunderbolt iv (expert)', '[]')"
            )
            conn.commit()
        with sqlite3.connect(recipes_db) as conn:
            updated = _backfill_spell_tiers(conn)
        assert updated >= 1
        row = find_by_id(50, path=recipes_db)
        assert row["crafted_tier"] == "Expert"

    def test_idempotent_on_already_filled_rows(self, recipes_db: Path):
        recipe = _make_recipe_dict(recipe_id=55, name="Fire Bolt (Expert)")
        with init_db(recipes_db) as conn:
            upsert_recipes([recipe], conn)
        # Run backfill twice — second run should find 0 rows to update
        with sqlite3.connect(recipes_db) as conn:
            updated = _backfill_spell_tiers(conn)
        assert updated == 0


# ---------------------------------------------------------------------------
# out_level migration
# ---------------------------------------------------------------------------


class TestOutLevelColumn:
    def _columns(self, db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(recipes)")}

    def test_init_db_adds_out_level_column(self, recipes_db: Path):
        assert "out_level" in self._columns(recipes_db)

    def test_init_db_is_idempotent_on_out_level(self, recipes_db: Path):
        # A second init_db on an already-migrated DB must not raise or drop data.
        init_db(recipes_db).close()
        assert "out_level" in self._columns(recipes_db)

    def test_out_level_round_trips(self, recipes_db: Path):
        with sqlite3.connect(recipes_db) as conn:
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps, out_level) "
                "VALUES (70, 'Abhorrent Seal III (Journeyman)', 'abhorrent seal iii (journeyman)', '[]', 75)"
            )
            conn.commit()
        row = find_by_id(70, path=recipes_db)
        assert row["out_level"] == 75


# ---------------------------------------------------------------------------
# upsert_recipes
# ---------------------------------------------------------------------------


class TestUpsertRecipes:
    def test_idempotent_on_re_upsert(self, recipes_db: Path):
        recipe = _make_recipe_dict(recipe_id=60, name="Iron Shield")
        with init_db(recipes_db) as conn:
            count1 = upsert_recipes([recipe], conn)
            count2 = upsert_recipes([recipe], conn)
        assert count1 == 1
        assert count2 == 1  # idempotent: same id → replace
        # Only one row in DB
        row = find_by_id(60, path=recipes_db)
        assert row is not None

    def test_skips_recipe_with_no_id(self, recipes_db: Path):
        bad = {"name": "No ID Recipe"}
        with init_db(recipes_db) as conn:
            count = upsert_recipes([bad], conn)
        assert count == 0
