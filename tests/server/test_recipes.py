"""Tests for the recipes route — filter helpers and search endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.recipes import (
    BENCH_DISPLAY,
    TIER_FUEL,
    _bench_label,
    _fuel_to_craft_tier,
    _resolve_bench_param,
)

# ---------------------------------------------------------------------------
# Pure helper unit tests (no HTTP needed)
# ---------------------------------------------------------------------------


class TestFuelToCraftTier:
    def test_known_prefix_returns_tier(self):
        assert _fuel_to_craft_tier("Basic Kindling") == "T1"
        assert _fuel_to_craft_tier("Glowing Kindling") == "T2"
        assert _fuel_to_craft_tier("Smoldering Kindling") == "T3"
        assert _fuel_to_craft_tier("Formless Kindling") == "T14"

    def test_case_insensitive(self):
        assert _fuel_to_craft_tier("basic kindling") == "T1"
        assert _fuel_to_craft_tier("BASIC KINDLING") == "T1"

    def test_unknown_prefix_returns_none(self):
        assert _fuel_to_craft_tier("Unknown Kindling") is None

    def test_none_input_returns_none(self):
        assert _fuel_to_craft_tier(None) is None

    def test_empty_string_returns_none(self):
        assert _fuel_to_craft_tier("") is None

    def test_all_tiers_covered(self):
        """Every entry in TIER_FUEL round-trips through _fuel_to_craft_tier."""
        for tier, prefix in TIER_FUEL.items():
            result = _fuel_to_craft_tier(f"{prefix} Kindling")
            assert result == tier, f"Expected {tier} for prefix {prefix!r}"


class TestBenchLabel:
    def test_known_bench_key_returns_display_label(self):
        assert _bench_label("forge") == "Armorer / Weaponsmith"
        assert _bench_label("work_desk") == "Sage"
        assert _bench_label("sewing_table") == "Tailor"

    def test_unknown_key_titlifies(self):
        assert _bench_label("my_custom_bench") == "My Custom Bench"

    def test_none_returns_none(self):
        assert _bench_label(None) is None


class TestResolveBenchParam:
    def test_raw_key_passthrough(self):
        # Keys that are already in BENCH_DISPLAY come back unchanged.
        for key in BENCH_DISPLAY:
            assert _resolve_bench_param(key) == key

    def test_display_label_resolves_to_key(self):
        assert _resolve_bench_param("Sage") == "work_desk"
        assert _resolve_bench_param("Tailor") == "sewing_table"

    def test_case_insensitive_label(self):
        assert _resolve_bench_param("sage") == "work_desk"
        assert _resolve_bench_param("SAGE") == "work_desk"

    def test_none_returns_none(self):
        assert _resolve_bench_param(None) is None

    def test_unknown_value_returned_as_is(self):
        # Unrecognised bench value passes through unchanged so the SQL can
        # attempt a match (or return nothing) rather than silently dropping it.
        assert _resolve_bench_param("mystery_bench") == "mystery_bench"


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recipe_filters(app):
    """GET /api/recipes/filters returns tiers, benches, and adventure classes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/recipes/filters")

    assert r.status_code == 200
    data = r.json()
    assert "craft_tiers" in data
    assert "benches" in data
    assert "adventure_classes" in data

    # Spot-check content
    assert "T1" in data["craft_tiers"]
    assert "T14" in data["craft_tiers"]
    bench_keys = {b["key"] for b in data["benches"]}
    assert "forge" in bench_keys
    assert "Wizard" in data["adventure_classes"]


@pytest.mark.asyncio
async def test_search_recipes_no_filters_returns_empty(app):
    """At least one filter must be provided; bare search returns empty."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    with patch("backend.server.api.recipes.RECIPES_DB_PATH", mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/search")

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_recipes_db_unavailable(app):
    """503 when recipes DB doesn't exist."""
    mock_db = MagicMock()
    mock_db.exists.return_value = False

    with patch("backend.server.api.recipes.RECIPES_DB_PATH", mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/search?q=fireball")

    assert r.status_code == 503


@pytest.mark.asyncio
async def test_search_recipes_class_filter_without_items_db(app):
    """503 when class_name filter used but items DB is absent."""
    mock_recipes_db = MagicMock()
    mock_recipes_db.exists.return_value = True
    mock_items_db = MagicMock()
    mock_items_db.exists.return_value = False

    with (
        patch("backend.server.api.recipes.RECIPES_DB_PATH", mock_recipes_db),
        patch("backend.server.api.recipes.ITEMS_DB_PATH", mock_items_db),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/search?class_name=wizard")

    assert r.status_code == 503
