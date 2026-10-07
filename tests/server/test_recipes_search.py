"""Tests for web/routes/recipes.py — COV-018.

Covers:
  GET /recipes/filters  — returns craft tiers, benches, and adventure classes.
  GET /recipes/search   — recipes catalogue not ready → 503;
                          no query params → empty results (no conditions);
                          name filter → paginates results;
                          tier filter → out_level range matching;
                          bench filter → bench key and display-label both accepted;
                          class_name filter → items catalogue not ready → 503;
                          class_name + items schema → item-id subquery;
                          craft_class filter → recipe_classes subquery;
                          page parameter → correct offset applied.

  Helper unit tests:
  _level_to_craft_tier  — crafted-item level → tier label (T1 … T14 / None).
  _bench_label          — raw key → display label; unknown key → title-cased.
  _resolve_bench_param  — accepts raw key or display label.

Postgres edition: seeded tests lease isolated recipes (and, for the
class-name filter, items) schemas; the route's import-frozen
``_RECIPES_SCHEMA`` search_path alias and the two SQL blocks that
hard-qualify the production ``items.items`` schema are re-pointed at the
leases via monkeypatch. The old file-exists 503 gates became
``catalogue.ready()`` probes, patched directly for the unavailable paths.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.recipes import (
    _bench_label,
    _level_to_craft_tier,
    _resolve_bench_param,
)
from tests.fixtures.pg import pg_conn

# ---------------------------------------------------------------------------
# Seeding helpers — minimal recipes / items rows in the leased schemas
# ---------------------------------------------------------------------------


def _seed_recipes(schema: str, rows: list[dict] | None = None) -> None:
    """Insert minimal recipe (+ recipe_classes) rows into the leased recipes
    schema — the shape search_recipes expects."""
    with pg_conn(schema) as conn:
        for row in rows or []:
            conn.execute(
                """INSERT INTO recipes (id, name, name_lower, bench, crafted_tier,
                   primary_comp, primary_qty, secondary_comps,
                   fuel_comp, fuel_qty, out_formed_id, out_formed_count, out_elaborate_id, out_level)
                   VALUES (%(id)s, %(name)s, %(name_lower)s, %(bench)s, %(crafted_tier)s,
                   %(primary_comp)s, %(primary_qty)s, %(secondary_comps)s,
                   %(fuel_comp)s, %(fuel_qty)s, %(out_formed_id)s, %(out_formed_count)s,
                   %(out_elaborate_id)s, %(out_level)s)""",
                {
                    "id": row.get("id", 1),
                    "name": row.get("name", "Test Recipe"),
                    "name_lower": row.get("name", "test recipe").lower(),
                    "bench": row.get("bench"),
                    "crafted_tier": row.get("crafted_tier"),
                    "primary_comp": row.get("primary_comp"),
                    "primary_qty": row.get("primary_qty"),
                    "secondary_comps": row.get("secondary_comps", "[]"),
                    "fuel_comp": row.get("fuel_comp"),
                    "fuel_qty": row.get("fuel_qty"),
                    "out_formed_id": row.get("out_formed_id"),
                    "out_formed_count": row.get("out_formed_count"),
                    "out_elaborate_id": row.get("out_elaborate_id"),
                    "out_level": row.get("out_level"),
                },
            )
            for cls in row.get("craft_classes", []):
                conn.execute(
                    "INSERT INTO recipe_classes (recipe_id, class) VALUES (%s, %s)",
                    (row.get("id", 1), cls),
                )


def _seed_items(schema: str, rows: list[dict] | None = None) -> None:
    """Insert minimal item rows (id + class_label) into the leased items
    schema (displayname columns are NOT NULL in the migration DDL)."""
    with pg_conn(schema) as conn:
        for row in rows or []:
            conn.execute(
                "INSERT INTO items (id, displayname, displayname_lower, class_label) VALUES (%s, %s, %s, %s)",
                (row["id"], f"Item {row['id']}", f"item {row['id']}", row.get("class_label")),
            )


@pytest.fixture
def recipes_route_schema(recipes_schema, monkeypatch):
    """Leased recipes schema with the route's import-frozen ``_RECIPES_SCHEMA``
    search_path alias re-pointed at it (``recipes_schema`` already re-points
    the shared catalogue, so ``ready()`` probes the lease)."""
    monkeypatch.setattr("backend.server.api.recipes._RECIPES_SCHEMA", recipes_schema)
    return recipes_schema


@pytest.fixture
def items_for_class_filter(items_schema, monkeypatch):
    """Leased items schema with the route's two cross-schema SQL blocks
    re-targeted at it. The route's SQL hard-qualifies ``items.items`` (the
    REAL session items schema), so without the rewrite the leased rows would
    be invisible to the class filter / label enrichment."""
    from backend.server.api import recipes as recipes_api

    for key in ("class_filter_subquery", "items_class_labels_by_ids", "items_class_labels_matching"):
        monkeypatch.setitem(
            recipes_api._SQL,
            key,
            recipes_api._SQL[key].replace("items.items", f'"{items_schema}".items'),
        )
    return items_schema


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
# ---------------------------------------------------------------------------


class TestLevelToTier:
    def test_known_levels_return_tier(self) -> None:
        assert _level_to_craft_tier(5) == "T1"
        assert _level_to_craft_tier(15) == "T2"
        assert _level_to_craft_tier(75) == "T8"
        assert _level_to_craft_tier(130) == "T14"

    def test_no_level_returns_none(self) -> None:
        assert _level_to_craft_tier(None) is None
        assert _level_to_craft_tier(0) is None


class TestBenchLabel:
    def test_known_bench_key_returns_display_name(self) -> None:
        assert _bench_label("work_desk") == "Sage"
        assert _bench_label("forge") == "Armorer / Weaponsmith"

    def test_unknown_bench_key_returns_title_cased(self) -> None:
        assert _bench_label("custom_bench") == "Custom Bench"

    def test_none_bench_returns_none(self) -> None:
        assert _bench_label(None) is None


class TestResolveBenchParam:
    def test_none_returns_none(self) -> None:
        assert _resolve_bench_param(None) is None

    def test_raw_bench_key_passes_through(self) -> None:
        assert _resolve_bench_param("work_desk") == "work_desk"

    def test_display_label_resolves_to_bench_key(self) -> None:
        assert _resolve_bench_param("Sage") == "work_desk"

    def test_case_insensitive_label_resolution(self) -> None:
        assert _resolve_bench_param("sage") == "work_desk"

    def test_unknown_value_passes_through(self) -> None:
        assert _resolve_bench_param("mystery_bench") == "mystery_bench"


# ---------------------------------------------------------------------------
# GET /recipes/filters
# ---------------------------------------------------------------------------


class TestGetRecipeFilters:
    async def test_returns_craft_tiers_list(self, app) -> None:
        """GET /recipes/filters returns all craft tier labels."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/filters")

        assert r.status_code == 200
        body = r.json()
        assert "T1" in body["craft_tiers"]
        assert "T14" in body["craft_tiers"]
        assert len(body["craft_tiers"]) == 14

    async def test_returns_bench_list(self, app) -> None:
        """GET /recipes/filters includes all bench entries."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/filters")

        body = r.json()
        bench_keys = {b["key"] for b in body["benches"]}
        assert "work_desk" in bench_keys
        assert "forge" in bench_keys

    async def test_returns_adventure_classes(self, app) -> None:
        """GET /recipes/filters includes adventure class names."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/filters")

        body = r.json()
        assert isinstance(body["adventure_classes"], list)
        assert len(body["adventure_classes"]) > 0


# ---------------------------------------------------------------------------
# GET /recipes/search — error paths
# ---------------------------------------------------------------------------


class TestSearchRecipesErrors:
    async def test_missing_recipes_db_returns_503(self, app) -> None:
        """When the recipes catalogue is not loaded (ready() False), a 503 is returned."""
        with patch("backend.server.api.recipes._recipes.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?q=test")

        assert r.status_code == 503
        assert "not available" in r.json()["detail"].lower()

    async def test_class_filter_missing_items_db_returns_503(self, app) -> None:
        """When class_name filter is used but the items catalogue is not loaded, a 503 is returned."""
        with (
            patch("backend.server.api.recipes._recipes.ready", return_value=True),
            patch("backend.server.api.recipes._items.ready", return_value=False),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?class_name=Templar")

        assert r.status_code == 503

    async def test_no_conditions_returns_empty(self, app) -> None:
        """When no filters are given, empty results are returned (no full-scan)."""
        with (
            patch("backend.server.api.recipes._recipes.ready", return_value=True),
            patch("backend.server.api.recipes._items.ready", return_value=True),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["results"] == []


# ---------------------------------------------------------------------------
# GET /recipes/search — happy paths
# ---------------------------------------------------------------------------


class TestSearchRecipesHappyPath:
    async def test_name_filter_returns_matching_recipes(self, app, recipes_route_schema) -> None:
        """q= filter matches on name_lower LIKE %q%."""
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Firestarter Scroll"},
                {"id": 2, "name": "Iceblast Scroll"},
                {"id": 3, "name": "Something Else"},
            ],
        )

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?q=scroll")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert len(body["results"]) == 2
        names = {rec["name"] for rec in body["results"]}
        assert "Firestarter Scroll" in names
        assert "Iceblast Scroll" in names

    async def test_tier_filter_matches_level_range(self, app, recipes_route_schema) -> None:
        """tier=T1 filters recipes whose out_level falls in the 1–9 bracket."""
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Low Recipe", "out_level": 5},  # T1
                {"id": 2, "name": "Higher Recipe", "out_level": 15},  # T2
            ],
        )

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?tier=T1")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["name"] == "Low Recipe"
        assert body["results"][0]["craft_tier"] == "T1"

    async def test_bench_filter_by_key(self, app, recipes_route_schema) -> None:
        """bench=work_desk filters by raw bench key."""
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Sage Recipe", "bench": "work_desk"},
                {"id": 2, "name": "Forge Recipe", "bench": "forge"},
            ],
        )

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?bench=work_desk")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["name"] == "Sage Recipe"
        assert body["results"][0]["bench_label"] == "Sage"

    async def test_bench_filter_by_display_label(self, app, recipes_route_schema) -> None:
        """bench=Sage (display label) resolves to work_desk and filters correctly."""
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Sage Recipe", "bench": "work_desk"},
            ],
        )

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?bench=Sage")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1

    async def test_craft_class_filter_uses_recipe_classes_table(self, app, recipes_route_schema) -> None:
        """craft_class= filter matches via the recipe_classes table."""
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Armorer Recipe", "craft_classes": ["Armorer"]},
                {"id": 2, "name": "Sage Recipe", "craft_classes": ["Sage"]},
            ],
        )

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?craft_class=Armorer")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["name"] == "Armorer Recipe"
        assert "Armorer" in body["results"][0]["craft_classes"]

    async def test_class_name_filter_returns_empty_when_no_item_ids_match(
        self, app, recipes_route_schema, items_for_class_filter
    ) -> None:
        """class_name filter with no matching items → empty results."""
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Some Recipe"},
            ],
        )
        _seed_items(
            items_for_class_filter,
            rows=[
                {"id": 999, "class_label": "Shadowknight"},
            ],
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/search?class_name=Templar")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["results"] == []

    async def test_class_name_filter_returns_matching_recipes(
        self, app, recipes_route_schema, items_for_class_filter
    ) -> None:
        """class_name filter narrows results via the items-schema subquery."""
        _seed_items(
            items_for_class_filter,
            rows=[
                {"id": 555, "class_label": "Templar"},
            ],
        )
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Templar Scroll", "out_elaborate_id": 555},
                {"id": 2, "name": "Shadowknight Scroll", "out_elaborate_id": 666},
            ],
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/search?class_name=templar")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["name"] == "Templar Scroll"

    async def test_pagination_page_2_returns_offset_results(self, app, recipes_route_schema) -> None:
        """page=2 returns the second page of results."""
        rows = [
            {"id": i, "name": f"Recipe {i:03d}", "out_level": 5}  # all T1
            for i in range(1, 30)  # 29 total
        ]
        _seed_recipes(recipes_route_schema, rows=rows)

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?tier=T1&page=2")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 29
        assert body["page"] == 2
        assert len(body["results"]) == 4  # 29 - 25 = 4 on page 2

    async def test_secondary_comps_parsed_from_json(self, app, recipes_route_schema) -> None:
        """secondary_comps JSON is parsed into IngredientResponse list."""
        sec = json.dumps(
            [
                {"description": "Noxious Coal", "quantity": 2},
            ]
        )
        _seed_recipes(
            recipes_route_schema,
            rows=[
                {"id": 1, "name": "Complex Recipe", "secondary_comps": sec, "out_level": 5},
            ],
        )

        with patch("backend.server.api.recipes._items.ready", return_value=False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/recipes/search?tier=T1")

        assert r.status_code == 200
        body = r.json()
        assert len(body["results"]) == 1
        sec_comps = body["results"][0]["secondary_comps"]
        assert len(sec_comps) == 1
        assert sec_comps[0]["description"] == "Noxious Coal"
        assert sec_comps[0]["quantity"] == 2
