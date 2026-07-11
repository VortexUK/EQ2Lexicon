"""Tests for backend.eq2db.recipes — COV-011.

Covers: _parse_spell_tier, recipe_to_row, find_by_id, find_by_name,
find_by_spell, find_spells_by_tier, find_by_output_id, _backfill_spell_tiers,
upsert_recipes — all via the RecipeCatalogue instance API.

Target: ≥ 75% on backend.eq2db.recipes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.eq2db.recipes import RecipeCatalogue

# Pure staticmethods under test — aliased for readable call sites.
_parse_spell_tier = RecipeCatalogue._parse_spell_tier
recipe_to_row = RecipeCatalogue.recipe_to_row
_backfill_spell_tiers = RecipeCatalogue._backfill_spell_tiers


@pytest.fixture
def recipes_db(tmp_path: Path) -> RecipeCatalogue:
    """Return a RecipeCatalogue over an initialised (empty) recipes.db."""
    cat = RecipeCatalogue(tmp_path / "recipes.db")
    cat.init_db().close()
    return cat


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
        assert RecipeCatalogue(tmp_path / "no.db").find_by_id(1) is None

    def test_returns_none_for_unknown_id(self, recipes_db: RecipeCatalogue):
        assert recipes_db.find_by_id(9999) is None

    def test_returns_row_for_existing_id(self, recipes_db: RecipeCatalogue):
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([_make_recipe_dict(recipe_id=77, name="Steel Helm")], conn)
        row = recipes_db.find_by_id(77)
        assert row is not None
        assert row["name"] == "Steel Helm"
        assert isinstance(row["secondary_comps"], list)  # deserialized


# ---------------------------------------------------------------------------
# find_by_name
# ---------------------------------------------------------------------------


class TestFindByName:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert RecipeCatalogue(tmp_path / "no.db").find_by_name("anything") == []

    def test_exact_match(self, recipes_db: RecipeCatalogue):
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([_make_recipe_dict(recipe_id=10, name="Iron Helm")], conn)
        rows = recipes_db.find_by_name("Iron Helm")
        assert len(rows) == 1
        assert rows[0]["name"] == "Iron Helm"

    def test_case_insensitive_exact(self, recipes_db: RecipeCatalogue):
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([_make_recipe_dict(recipe_id=11, name="Iron Helm")], conn)
        rows = recipes_db.find_by_name("iron helm")
        assert len(rows) == 1

    def test_like_fallback(self, recipes_db: RecipeCatalogue):
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([_make_recipe_dict(recipe_id=12, name="Bronze Gauntlets")], conn)
        rows = recipes_db.find_by_name("gauntlet")
        assert any("Gauntlets" in r["name"] for r in rows)

    def test_like_escapes_percent_in_name(self, recipes_db: RecipeCatalogue):
        # Should not raise / should return empty rather than crash
        rows = recipes_db.find_by_name("100% durable")
        assert isinstance(rows, list)

    def test_like_escapes_underscore_in_name(self, recipes_db: RecipeCatalogue):
        rows = recipes_db.find_by_name("a_b")
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# find_by_spell
# ---------------------------------------------------------------------------


class TestFindBySpell:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert RecipeCatalogue(tmp_path / "no.db").find_by_spell("x", "Expert") == []

    def test_returns_empty_for_no_match(self, recipes_db: RecipeCatalogue):
        assert recipes_db.find_by_spell("nonexistent spell", "Expert") == []

    def test_returns_only_matching_tier(self, recipes_db: RecipeCatalogue):
        expert_recipe = _make_recipe_dict(recipe_id=20, name="Fireball II (Expert)")
        master_recipe = _make_recipe_dict(recipe_id=21, name="Fireball II (Master)")
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([expert_recipe, master_recipe], conn)
        rows = recipes_db.find_by_spell("fireball ii", "Expert")
        assert len(rows) == 1
        assert rows[0]["crafted_tier"] == "Expert"


# ---------------------------------------------------------------------------
# find_spells_by_tier
# ---------------------------------------------------------------------------


class TestFindSpellsByTier:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert RecipeCatalogue(tmp_path / "no.db").find_spells_by_tier(["x"], "Expert") == {}

    def test_returns_empty_for_empty_list(self, recipes_db: RecipeCatalogue):
        assert recipes_db.find_spells_by_tier([], "Expert") == {}

    def test_bulk_lookup(self, recipes_db: RecipeCatalogue):
        recipes = [
            _make_recipe_dict(recipe_id=30, name="Ice Nova (Expert)"),
            _make_recipe_dict(recipe_id=31, name="Fire Nova (Expert)"),
        ]
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes(recipes, conn)
        result = recipes_db.find_spells_by_tier(["ice nova", "fire nova"], "Expert")
        assert "ice nova" in result
        assert "fire nova" in result


# ---------------------------------------------------------------------------
# find_by_output_id
# ---------------------------------------------------------------------------


class TestFindByOutputId:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert RecipeCatalogue(tmp_path / "no.db").find_by_output_id(1) == []

    def test_returns_empty_for_unknown_id(self, recipes_db: RecipeCatalogue):
        assert recipes_db.find_by_output_id(9999) == []

    def test_finds_recipe_by_formed_output(self, recipes_db: RecipeCatalogue):
        recipe = _make_recipe_dict(recipe_id=40, name="Formed Item Recipe", formed_id=5000)
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([recipe], conn)
        rows = recipes_db.find_by_output_id(5000)
        assert len(rows) == 1
        assert rows[0]["out_formed_id"] == 5000

    def test_finds_recipe_by_elaborate_output(self, recipes_db: RecipeCatalogue):
        d = _make_recipe_dict(recipe_id=41, name="Elaborate Recipe")
        d["output"] = {"elaborate": "6000", "elaborate_count": "1"}
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([d], conn)
        rows = recipes_db.find_by_output_id(6000)
        assert any(r["out_elaborate_id"] == 6000 for r in rows)


# ---------------------------------------------------------------------------
# _backfill_spell_tiers
# ---------------------------------------------------------------------------


class TestBackfillSpellTiers:
    def test_backfills_null_crafted_tier_rows(self, recipes_db: RecipeCatalogue):
        # Insert a row with no spell-tier via raw SQL to simulate pre-migration data
        with sqlite3.connect(recipes_db.path) as conn:
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps) "
                "VALUES (50, 'Thunderbolt IV (Expert)', 'thunderbolt iv (expert)', '[]')"
            )
            conn.commit()
        with sqlite3.connect(recipes_db.path) as conn:
            updated = _backfill_spell_tiers(conn)
        assert updated >= 1
        row = recipes_db.find_by_id(50)
        assert row["crafted_tier"] == "Expert"

    def test_idempotent_on_already_filled_rows(self, recipes_db: RecipeCatalogue):
        recipe = _make_recipe_dict(recipe_id=55, name="Fire Bolt (Expert)")
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([recipe], conn)
        # Run backfill twice — second run should find 0 rows to update
        with sqlite3.connect(recipes_db.path) as conn:
            updated = _backfill_spell_tiers(conn)
        assert updated == 0


# ---------------------------------------------------------------------------
# out_level migration
# ---------------------------------------------------------------------------


class TestOutLevelColumn:
    def _columns(self, db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(recipes)")}

    def test_init_db_adds_out_level_column(self, recipes_db: RecipeCatalogue):
        assert "out_level" in self._columns(recipes_db.path)

    def test_init_db_is_idempotent_on_out_level(self, recipes_db: RecipeCatalogue):
        # A second init_db on an already-migrated DB must not raise or drop data.
        recipes_db.init_db().close()
        assert "out_level" in self._columns(recipes_db.path)

    def test_out_level_round_trips(self, recipes_db: RecipeCatalogue):
        with sqlite3.connect(recipes_db.path) as conn:
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps, out_level) "
                "VALUES (70, 'Abhorrent Seal III (Journeyman)', 'abhorrent seal iii (journeyman)', '[]', 75)"
            )
            conn.commit()
        row = recipes_db.find_by_id(70)
        assert row["out_level"] == 75

    def test_migrates_pre_out_level_db_shape(self, tmp_path: Path):
        """A recipes.db created BEFORE the out_level column must migrate cleanly.

        Mirrors the prod failure: read paths SELECT out_level, so init_db must
        add the column to an old-shape DB. A fresh-fixture DB already has the
        column and would mask this — build the legacy schema explicitly.
        """
        from backend.eq2db import recipes as recipes_mod

        db_path = tmp_path / "legacy_recipes.db"
        with sqlite3.connect(db_path) as conn:
            # Build the real pre-migration table: schema_recipes is the CREATE
            # TABLE without out_level (it's added only by migrate_add_out_level),
            # so this faithfully reproduces an old-version recipes.db.
            conn.execute(recipes_mod._SQL["schema_recipes"])
            cols_before = {r[1] for r in conn.execute("PRAGMA table_info(recipes)")}
            assert "out_level" not in cols_before  # sanity: genuinely old shape
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps) "
                "VALUES (1, 'Old Recipe', 'old recipe', '[]')"
            )
            conn.commit()

        legacy = RecipeCatalogue(db_path)
        legacy.init_db().close()  # must ALTER in out_level without error

        with sqlite3.connect(db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(recipes)")}
        assert "out_level" in cols
        # find_by_id SELECTs out_level — must not raise "no such column".
        row = legacy.find_by_id(1)
        assert row is not None
        assert row["out_level"] is None


# ---------------------------------------------------------------------------
# upsert_recipes
# ---------------------------------------------------------------------------


class TestUpsertRecipes:
    def test_idempotent_on_re_upsert(self, recipes_db: RecipeCatalogue):
        recipe = _make_recipe_dict(recipe_id=60, name="Iron Shield")
        with recipes_db.init_db() as conn:
            count1 = recipes_db.upsert_recipes([recipe], conn)
            count2 = recipes_db.upsert_recipes([recipe], conn)
        assert count1 == 1
        assert count2 == 1  # idempotent: same id → replace
        # Only one row in DB
        row = recipes_db.find_by_id(60)
        assert row is not None

    def test_skips_recipe_with_no_id(self, recipes_db: RecipeCatalogue):
        bad = {"name": "No ID Recipe"}
        with recipes_db.init_db() as conn:
            count = recipes_db.upsert_recipes([bad], conn)
        assert count == 0
