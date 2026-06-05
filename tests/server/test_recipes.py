"""Tests for the recipes route — filter helpers and search endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.recipes import (
    BENCH_DISPLAY,
    CRAFT_TIERS,
    _bench_label,
    _craft_tier_to_level_range,
    _level_to_craft_tier,
    _resolve_bench_param,
)

# ---------------------------------------------------------------------------
# Pure helper unit tests (no HTTP needed)
# ---------------------------------------------------------------------------


class TestLevelToCraftTier:
    def test_known_levels_map_to_tiers(self):
        # Abhorrent Seal III (Journeyman) makes a level-75 scroll → T8
        assert _level_to_craft_tier(75) == "T8"
        assert _level_to_craft_tier(1) == "T1"
        assert _level_to_craft_tier(9) == "T1"
        assert _level_to_craft_tier(10) == "T2"
        assert _level_to_craft_tier(70) == "T8"
        assert _level_to_craft_tier(120) == "T13"

    def test_top_tier_is_capped(self):
        assert _level_to_craft_tier(130) == "T14"
        assert _level_to_craft_tier(200) == "T14"  # never exceeds T14

    def test_no_level_returns_none(self):
        assert _level_to_craft_tier(None) is None
        assert _level_to_craft_tier(0) is None
        assert _level_to_craft_tier(-5) is None


class TestCraftTierToLevelRange:
    def test_bounded_tiers(self):
        assert _craft_tier_to_level_range("T1") == (1, 9)
        assert _craft_tier_to_level_range("T2") == (10, 19)
        assert _craft_tier_to_level_range("T8") == (70, 79)

    def test_top_tier_is_open_ended(self):
        assert _craft_tier_to_level_range("T14") == (130, None)

    def test_case_insensitive(self):
        assert _craft_tier_to_level_range("t8") == (70, 79)

    def test_unknown_tier_returns_none(self):
        assert _craft_tier_to_level_range("T99") is None
        assert _craft_tier_to_level_range("garbage") is None

    def test_roundtrip_with_level_to_tier(self):
        """Every tier's lower bound maps back to that tier."""
        for tier in CRAFT_TIERS:
            lo, _hi = _craft_tier_to_level_range(tier)
            assert _level_to_craft_tier(lo) == tier, f"{tier} lower-bound roundtrip failed"


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


# ---------------------------------------------------------------------------
# craft_tier derived from out_level (the level → tier fix)
# ---------------------------------------------------------------------------


@pytest.fixture
def _recipes_db_with_level(tmp_path):
    """A 1-row recipes.db where the recipe makes a level-75 item (→ T8)."""
    import sqlite3

    from backend.eq2db.recipes import DB_PATH as _real  # noqa: F401  (ensures module import)
    from backend.eq2db.recipes import init_db as recipes_init_db

    db_path = tmp_path / "recipes.db"
    recipes_init_db(db_path).close()  # creates schema incl. out_level column
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO recipes (id, name, name_lower, secondary_comps, out_level) VALUES (?, ?, ?, '[]', ?)",
            (1, "Abhorrent Seal III (Journeyman)", "abhorrent seal iii (journeyman)", 75),
        )
        conn.commit()
    return db_path


@pytest.mark.asyncio
async def test_search_craft_tier_from_level(app, _recipes_db_with_level):
    """A level-75 recipe surfaces as craft_tier T8 (not the old fuel-derived value)."""
    no_items = MagicMock()
    no_items.exists.return_value = False  # skip class enrichment; craft_tier comes from out_level

    with (
        patch("backend.server.api.recipes.RECIPES_DB_PATH", _recipes_db_with_level),
        patch("backend.server.api.recipes.ITEMS_DB_PATH", no_items),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/recipes/search?q=abhorrent%20seal")

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["results"][0]["craft_tier"] == "T8"


@pytest.mark.asyncio
async def test_search_tier_filter_uses_level_range(app, _recipes_db_with_level):
    """?tier=T8 matches the level-75 recipe; ?tier=T3 (old wrong value) does not."""
    no_items = MagicMock()
    no_items.exists.return_value = False

    with (
        patch("backend.server.api.recipes.RECIPES_DB_PATH", _recipes_db_with_level),
        patch("backend.server.api.recipes.ITEMS_DB_PATH", no_items),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            hit = await client.get("/api/recipes/search?tier=T8")
            miss = await client.get("/api/recipes/search?tier=T3")

    assert hit.json()["total"] == 1
    assert miss.json()["total"] == 0
