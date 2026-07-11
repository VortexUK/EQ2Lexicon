"""HTTP-layer tests for web/routes/item.py — search, detail, spell-scroll, filters.

COV-004 scenarios: stat_filter parsing (gte/lte), tier exact vs LIKE, item_type
routing (typeinfo_name vs classification_list), JOIN parameter ordering, non-numeric
item ID → 400, Census fallback, craftable/non-craftable spell-scroll, and the filters
endpoint. Each test encodes one named behaviour, not line-coverage.
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared DB setup — minimal items + item_stats tables
# ---------------------------------------------------------------------------

_ITEMS_DDL = """
CREATE TABLE items (
    id                   INTEGER PRIMARY KEY,
    displayname          TEXT    NOT NULL,
    displayname_lower    TEXT    NOT NULL,
    tier_display         TEXT,
    slot                 TEXT,
    typeinfo_name        TEXT,
    level_to_use         INTEGER DEFAULT 0,
    class_label          TEXT,
    icon_id              INTEGER,
    classes_json         TEXT    DEFAULT '{}',
    classification_list  TEXT,
    visible              INTEGER DEFAULT 1,
    flag_pvp             INTEGER DEFAULT 0,
    tierid               INTEGER DEFAULT 0,
    type                 TEXT    DEFAULT 'Item'
);
CREATE TABLE item_stats (
    item_id  INTEGER NOT NULL,
    stat     TEXT    NOT NULL,
    value    REAL    NOT NULL,
    PRIMARY KEY (item_id, stat)
);
"""


def _seed_items_db(path: Path) -> None:
    """Create and populate a minimal items DB for testing."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_ITEMS_DDL)
    conn.executemany(
        "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,1,0,?,?)",
        [
            # id, displayname, displayname_lower, tier_display, slot, typeinfo_name,
            # level_to_use, class_label, icon_id, classes_json, classification_list,
            # tierid, type
            (
                1,
                "Legendary Shield",
                "legendary shield",
                "LEGENDARY",
                "Secondary",
                "shield",
                90,
                "All Fighters",
                100,
                "{}",
                None,
                4,
                "Item",
            ),
            (
                2,
                "Fabled Ring",
                "fabled ring",
                "FABLED",
                "Finger",
                "ring",
                95,
                "All Classes",
                101,
                "{}",
                None,
                5,
                "Item",
            ),
            (
                3,
                "Common Helm",
                "common helm",
                "COMMON",
                "Head",
                "armor",
                80,
                "All Fighters",
                102,
                "{}",
                None,
                0,
                "Item",
            ),
            (
                4,
                "Material Ore",
                "material ore",
                "COMMON",
                None,
                "material",
                1,
                None,
                103,
                "{}",
                '["materials"]',
                0,
                "Item",
            ),
            (5, "Storage Box", "storage box", "COMMON", None, "container", 1, None, 104, "{}", None, 0, "Container"),
            (
                6,
                "Itemcontainer Bag",
                "itemcontainer bag",
                "COMMON",
                None,
                "itemcontainer",
                1,
                None,
                105,
                "{}",
                None,
                0,
                "Container",
            ),
            (
                7,
                "Uncommonly Good",
                "uncommonly good",
                "UNCOMMON",
                "Head",
                "armor",
                85,
                "All Scouts",
                106,
                "{}",
                None,
                1,
                "Item",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO item_stats VALUES (?,?,?)",
        [
            (1, "Strength", 60.0),
            (2, "Strength", 100.0),
            (1, "Stamina", 40.0),
            (7, "Strength", 30.0),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def items_db_path(tmp_path: Path) -> Generator[Path]:
    """Yield a path to a seeded items DB; patch census.db.DB_PATH for its lifetime."""
    db_file = tmp_path / "items.db"
    _seed_items_db(db_file)
    with patch("backend.eq2db.items.DB_PATH", db_file):
        with patch("backend.server.api.item.DB_PATH", db_file):
            yield db_file


# ---------------------------------------------------------------------------
# /api/items/search — stat_filter parsing
# ---------------------------------------------------------------------------


async def test_search_with_stat_filter_gte_returns_qualifying_items(app, items_db_path):
    """stat_filter=Strength:gte:50 returns only items with Strength >= 50."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search?stat_filter=Strength:gte:50")
    assert r.status_code == 200
    body = r.json()
    ids = {row["id"] for row in body["results"]}
    # id=1 has Strength=60 (qualifies), id=2 has Strength=100 (qualifies),
    # id=7 has Strength=30 (does NOT qualify)
    assert 1 in ids
    assert 2 in ids
    assert 7 not in ids


async def test_search_with_stat_filter_lte_returns_qualifying_items(app, items_db_path):
    """stat_filter=Strength:lte:50 returns only items with Strength <= 50."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search?stat_filter=Strength:lte:50")
    assert r.status_code == 200
    body = r.json()
    ids = {row["id"] for row in body["results"]}
    # id=7 has Strength=30 (qualifies), id=1 has Strength=60 (does NOT qualify)
    assert 7 in ids
    assert 1 not in ids


# ---------------------------------------------------------------------------
# /api/items/search — tier filter
# ---------------------------------------------------------------------------


async def test_search_tier_common_exact_match_excludes_uncommon(app, items_db_path):
    """tier=Common uses exact match so UNCOMMON items are excluded."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search?tier=Common")
    assert r.status_code == 200
    body = r.json()
    for row in body["results"]:
        assert row["tier"] == "COMMON", f"Unexpected tier: {row['tier']}"


async def test_search_tier_fabled_like_match_finds_fabled(app, items_db_path):
    """tier=Fabled uses LIKE matching to find items with FABLED in their tier."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search?tier=Fabled")
    assert r.status_code == 200
    body = r.json()
    ids = {row["id"] for row in body["results"]}
    assert 2 in ids  # "FABLED" matches
    # Common helm should not appear
    assert 3 not in ids


# ---------------------------------------------------------------------------
# /api/items/search — item_type routing
# ---------------------------------------------------------------------------


async def test_search_item_type_material_uses_classification_list(app, items_db_path):
    """item_type=Material filters via classification_list column, not typeinfo_name."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search?item_type=Material")
    assert r.status_code == 200
    body = r.json()
    ids = {row["id"] for row in body["results"]}
    # Only item 4 has '["materials"]' in classification_list
    assert 4 in ids
    assert 1 not in ids


async def test_search_item_type_container_matches_both_container_and_itemcontainer(app, items_db_path):
    """item_type=Container returns rows with typeinfo_name in ('container', 'itemcontainer')."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search?item_type=Container")
    assert r.status_code == 200
    body = r.json()
    ids = {row["id"] for row in body["results"]}
    assert 5 in ids  # typeinfo_name='container'
    assert 6 in ids  # typeinfo_name='itemcontainer'
    assert 1 not in ids


# ---------------------------------------------------------------------------
# /api/items/search — no filter returns empty
# ---------------------------------------------------------------------------


async def test_search_no_filters_returns_empty_results(app, items_db_path):
    """A search with zero filters returns an empty result set (not an error)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/items/search")
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# /api/items/search — DB unavailable
# ---------------------------------------------------------------------------


async def test_search_with_filter_when_db_missing_returns_503(app):
    """If the items DB does not exist, the endpoint returns 503."""
    with patch("backend.server.api.item.DB_PATH", Path("/nonexistent/items.db")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/items/search?name=sword")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /api/item/{item_id} — non-numeric ID
# ---------------------------------------------------------------------------


async def test_item_detail_non_numeric_id_returns_400(app):
    """A non-numeric item_id path param returns 400 (our check, not FastAPI's)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/item/not-a-number")
    assert r.status_code == 400
    assert "numeric" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /api/item/{item_id} — Census hit + 404
# ---------------------------------------------------------------------------


async def test_item_detail_unknown_id_falls_back_to_census_and_returns_item(app):
    """Numeric item_id hits Census; a known item is returned as 200."""
    from backend.census.models import ItemData

    fake_item = ItemData(
        id="12345",
        name="Test Sword",
        quality="Legendary",
        description="A sharp blade",
        icon_id="100",
        icon_bytes=None,
        slot_type="Primary",
        armor_type="Weapon",
        mitigation=None,
        item_level=90,
        required_level=85,
        classes=["Guardian", "Berserker"],
    )

    mock_client = AsyncMock()
    mock_client.get_item = AsyncMock(return_value=fake_item)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.server.api.item.shared_census_client", return_value=mock_ctx):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/item/12345")

    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Test Sword"
    assert body["quality"] == "Legendary"
    assert body["classes_label"] == "All Warriors"


async def test_item_detail_missing_from_census_returns_404(app):
    """When Census returns None for the item_id, the endpoint returns 404."""
    mock_client = AsyncMock()
    mock_client.get_item = AsyncMock(return_value=None)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.server.api.item.shared_census_client", return_value=mock_ctx):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/item/99999")

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/spell-scroll — craftable vs non-craftable
# ---------------------------------------------------------------------------


async def test_spell_scroll_apprentice_is_not_craftable(app):
    """Apprentice tier is not in CRAFTABLE_TIERS → craftable=False, recipe=None."""
    with (
        patch("backend.server.api.item.DB_PATH", Path("/nonexistent/items.db")),
        patch("backend.server.api.item.RECIPES_DB_PATH", Path("/nonexistent/recipes.db")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/spell-scroll?name=Sanctuary&tier=Apprentice")
    assert r.status_code == 200
    body = r.json()
    assert body["craftable"] is False
    assert body["recipe"] is None


async def test_spell_scroll_expert_tier_is_craftable(app):
    """Expert tier is in CRAFTABLE_TIERS → craftable=True (recipe may be None if no DB)."""
    with (
        patch("backend.server.api.item.DB_PATH", Path("/nonexistent/items.db")),
        patch("backend.server.api.item.RECIPES_DB_PATH", Path("/nonexistent/recipes.db")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/spell-scroll?name=Sanctuary&tier=Expert")
    assert r.status_code == 200
    body = r.json()
    assert body["craftable"] is True
    # No recipe DB → recipe is None but craftable is still True
    assert body["recipe"] is None


async def test_spell_scroll_expert_returns_recipe_when_found(app, tmp_path):
    """Expert tier returns a recipe when the recipes DB has a match."""
    # Create a real but empty recipes DB file so RECIPES_DB_PATH.exists() is True.
    fake_recipes_db = tmp_path / "recipes.db"
    fake_recipes_db.touch()

    fake_recipe = {
        "primary_comp": "Coral",
        "primary_qty": 2,
        "secondary_comps": [{"description": "Vellum", "quantity": 1}],
        "fuel_comp": "Candle",
        "fuel_qty": 1,
    }

    with (
        patch("backend.server.api.item._recipes.find_by_spell", return_value=[fake_recipe]),
        patch("backend.server.api.item.DB_PATH", Path("/nonexistent/items.db")),
        patch("backend.server.api.item.RECIPES_DB_PATH", fake_recipes_db),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/spell-scroll?name=Sanctuary+III&tier=Expert")
    assert r.status_code == 200
    body = r.json()
    assert body["craftable"] is True
    assert body["recipe"]["primary_comp"] == "Coral"
    assert body["recipe"]["primary_qty"] == 2
    assert len(body["recipe"]["secondary_comps"]) == 1


# ---------------------------------------------------------------------------
# /api/items/filters — server_max_level
# ---------------------------------------------------------------------------


async def test_items_filters_returns_server_max_level(app):
    """GET /api/items/filters returns server_max_level from the server context."""
    from backend.server.server_context import Server

    fake_server = Server(
        world="Varsoon",
        subdomain="varsoon",
        display_name="Varsoon",
        max_level=120,
        current_xpac="ToV",
        launch_dt=None,
    )

    with patch("backend.server.api.item.current_server", return_value=fake_server):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/items/filters")
    assert r.status_code == 200
    body = r.json()
    assert body["server_max_level"] == 120
    # Tiers and slots are static (empty in this impl) — key presence is the contract
    assert "tiers" in body
    assert "slots" in body
    assert "item_types" in body
