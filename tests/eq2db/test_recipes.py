"""Tests for backend.eq2db.recipes — COV-011.

Covers: _parse_spell_tier, recipe_to_row, find_by_id, find_by_name,
find_by_spell, find_spells_by_tier, find_by_output_id, upsert_recipes —
all via the RecipeCatalogue instance API.

Postgres edition: DB tests lease an isolated scratch schema via the
``recipes_schema`` fixture (tests/fixtures/catalogues_db.py) and construct
``RecipeCatalogue(recipes_schema)`` — the analog of the old
``RecipeCatalogue(tmp_db)``. Raw seeding/assertions go through
``pg_conn(schema)`` (dict rows, %s params). The former "missing DB file"
cases now assert the EMPTY-schema behaviour; the ``_backfill_spell_tiers``
startup backfill was deleted in the cutover (crafted_tier is computed by
recipe_to_row at write time; out_level is loader-owned) — its tests pin
the write-time equivalent.

Target: ≥ 75% on backend.eq2db.recipes.
"""

from __future__ import annotations

import pytest

from backend.eq2db.recipes import RecipeCatalogue
from tests.fixtures.pg import pg_conn

# Pure staticmethods under test — aliased for readable call sites.
_parse_spell_tier = RecipeCatalogue._parse_spell_tier
recipe_to_row = RecipeCatalogue.recipe_to_row


@pytest.fixture
def recipes_db(recipes_schema: str) -> RecipeCatalogue:
    """Return a RecipeCatalogue over an isolated leased (empty) scratch schema."""
    return RecipeCatalogue(recipes_schema)


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
    def test_returns_none_when_schema_empty(self, recipes_db: RecipeCatalogue):
        # (was test_returns_none_when_path_missing — a nonexistent DB file is
        # no longer a concept; the equivalent is an EMPTY leased schema.)
        assert recipes_db.find_by_id(1) is None
        assert recipes_db.ready() is False

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
    def test_returns_empty_when_schema_empty(self, recipes_db: RecipeCatalogue):
        # (was test_returns_empty_when_path_missing — see TestFindById.)
        assert recipes_db.find_by_name("anything") == []
        assert recipes_db.ready() is False

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
    def test_returns_empty_when_schema_empty(self, recipes_db: RecipeCatalogue):
        # (was test_returns_empty_when_path_missing — see TestFindById.)
        assert recipes_db.find_by_spell("x", "Expert") == []
        assert recipes_db.ready() is False

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
    def test_returns_empty_when_schema_empty(self, recipes_db: RecipeCatalogue):
        # (was test_returns_empty_when_path_missing — see TestFindById.)
        assert recipes_db.find_spells_by_tier(["x"], "Expert") == {}
        assert recipes_db.ready() is False

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
    def test_returns_empty_when_schema_empty(self, recipes_db: RecipeCatalogue):
        # (was test_returns_empty_when_path_missing — see TestFindById.)
        assert recipes_db.find_by_output_id(1) == []
        assert recipes_db.ready() is False

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
# spell-tier population at write time
# (descendants of the deleted _backfill_spell_tiers startup backfill)
# ---------------------------------------------------------------------------


class TestSpellTierAtWriteTime:
    """There is no startup spell-tier backfill —
    ``recipe_to_row`` (via ``_parse_spell_tier``) computes base_name_lower +
    crafted_tier at WRITE time, so a row can no longer arrive without them.
    Same intent as the old backfill tests: a tiered recipe name lands with
    its spell-tier columns populated, and re-running the write is a no-op."""

    def test_upsert_populates_crafted_tier(self, recipes_db: RecipeCatalogue):
        # (was test_backfills_null_crafted_tier_rows)
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([_make_recipe_dict(recipe_id=50, name="Thunderbolt IV (Expert)")], conn)
        row = recipes_db.find_by_id(50)
        assert row is not None
        assert row["crafted_tier"] == "Expert"
        assert row["base_name_lower"] == "thunderbolt iv"

    def test_idempotent_on_already_filled_rows(self, recipes_db: RecipeCatalogue):
        # The write-time analog of "a second backfill run finds 0 rows":
        # re-upserting an already-tiered recipe keeps the columns stable.
        recipe = _make_recipe_dict(recipe_id=55, name="Fire Bolt (Expert)")
        with recipes_db.init_db() as conn:
            recipes_db.upsert_recipes([recipe], conn)
            recipes_db.upsert_recipes([recipe], conn)
        rows = recipes_db.find_by_spell("fire bolt", "Expert")
        assert len(rows) == 1  # still one row — upsert, not duplicate
        assert rows[0]["crafted_tier"] == "Expert"


# ---------------------------------------------------------------------------
# out_level column (loader-owned; DDL in db/migrations/0009_recipes.sql)
# ---------------------------------------------------------------------------


class TestOutLevelColumn:
    def _columns(self, schema: str) -> set[str]:
        with pg_conn(schema) as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'recipes'",
                (schema,),
            ).fetchall()
        return {r["column_name"] for r in rows}

    def test_fresh_schema_has_out_level_column(self, recipes_db: RecipeCatalogue):
        # (out_level is a real column in 0009_recipes.sql.)
        assert "out_level" in self._columns(recipes_db.schema)

    def test_init_db_is_idempotent_on_out_level(self, recipes_db: RecipeCatalogue):
        # init_db is now a passive pooled handle — repeated calls must not
        # raise, mutate the schema, or drop data.
        recipes_db.init_db().close()
        recipes_db.init_db().close()
        assert "out_level" in self._columns(recipes_db.schema)

    def test_out_level_round_trips(self, recipes_db: RecipeCatalogue):
        with pg_conn(recipes_db.schema) as conn:
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps, out_level) "
                "VALUES (70, 'Abhorrent Seal III (Journeyman)', 'abhorrent seal iii (journeyman)', '[]', 75)"
            )
        row = recipes_db.find_by_id(70)
        assert row["out_level"] == 75

    def test_row_without_out_level_reads_as_none(self, recipes_db: RecipeCatalogue):
        """DDL is owned by
        db/migrations/0009_recipes.sql; out_level is loader-filled. What
        survives of the prod failure it pinned: read paths SELECT out_level,
        so a row written without it must read back cleanly as None — never
        raise "no such column"."""
        with pg_conn(recipes_db.schema) as conn:
            conn.execute(
                "INSERT INTO recipes (id, name, name_lower, secondary_comps) "
                "VALUES (1, 'Old Recipe', 'old recipe', '[]')"
            )
        # find_by_id SELECTs out_level — must not raise.
        row = recipes_db.find_by_id(1)
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


def test_classes_for_recipe(recipes_schema):
    from backend.eq2db.recipes import RecipeCatalogue

    cat = RecipeCatalogue(recipes_schema)
    conn = cat.init_db()
    try:
        conn.execute("INSERT INTO recipe_classes (recipe_id, class) VALUES (42, 'Sage'), (42, 'Alchemist')")
        conn.commit()
    finally:
        conn.close()
    assert cat.classes_for_recipe(42) == ["Alchemist", "Sage"]  # ordered
    assert cat.classes_for_recipe(999) == []
