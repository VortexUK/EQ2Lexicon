"""HTTP-layer tests for web/routes/character/upgrades.py — COV-006.

Covers:
  GET /api/character/{name}/upgrade-materials
    — spells DB missing → 503
    — recipes DB missing → 503
    — character not found → 404
    — empty spell_ids → zero counts, no ingredients
    — no sub-expert spells → zero counts, no ingredients
    — happy path: aggregates ingredients sorted by category/qty
    — two recipes sharing an ingredient sum their quantities
    — sort order: primary first, then secondary, then fuel
  GET /api/character/{name}/upgrade-recipes
    — spells DB missing → 503
    — character not found → 404
    — happy path returns RecipeResult list
    — character name too long → 400
  _lookup_items_by_name
    — exact match on stripped "Raw X" (pass-1)
    — pass-1 miss triggers LIKE fuzzy search (pass-2)
    — non-"Raw" name uses exact match only
    — items DB absent → returns empty dict

All Census + DB calls are mocked — no real network or disk IO.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers: minimal character cache object
# ---------------------------------------------------------------------------


class _FakeCharCached:
    def __init__(self, spell_ids=None, guild_name="Exordium"):
        self.spell_ids = spell_ids or []
        self.guild_name = guild_name


# ---------------------------------------------------------------------------
# GET /api/character/{name}/upgrade-materials
# ---------------------------------------------------------------------------


class TestGetUpgradeMaterials:
    async def test_spells_db_missing_returns_503(self, app):
        """If the spells database file doesn't exist, returns 503."""
        with (
            patch(
                "backend.server.api.character.upgrades._SPELLS_DB",
                new=Path("/nonexistent/spells.db"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 503
        assert "Spells" in r.json()["detail"]

    async def test_recipes_db_missing_returns_503(self, app, tmp_path):
        """If the spells DB exists but recipes DB doesn't, returns 503."""
        fake_spells_db = tmp_path / "spells.db"
        fake_spells_db.touch()
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells_db),
            patch(
                "backend.server.api.character.upgrades._RECIPES_DB",
                new=Path("/nonexistent/recipes.db"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 503
        assert "Recipes" in r.json()["detail"]

    async def test_character_not_found_returns_404(self, app, tmp_path):
        """Character doesn't exist on Census → 404."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(None, False),
            ),
            patch("backend.server.api.character.upgrades.shared_census_client") as mock_ctx,
        ):
            mock_client = AsyncMock()
            mock_client.get_character = AsyncMock(return_value=None)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Ghost/upgrade-materials")
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    async def test_empty_spell_ids_returns_zero_counts(self, app, tmp_path):
        """Cached character with no spell_ids → all-zero response."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        cached = _FakeCharCached(spell_ids=[])
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 200
        body = r.json()
        assert body["spells_needing_upgrade"] == 0
        assert body["spells_with_recipe"] == 0
        assert body["ingredients"] == []

    async def test_no_sub_expert_spells_returns_zero_counts(self, app, tmp_path):
        """Character has only Expert spells — nothing to upgrade."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        cached = _FakeCharCached(spell_ids=[101])
        # All spells are already Expert tier
        expert_row = {
            "name": "Divine Favor",
            "tier_name": "Expert",
            "type": "spells",
            "given_by": "spellscroll",
            "level": 90,
        }
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=[expert_row],
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 200
        body = r.json()
        assert body["spells_needing_upgrade"] == 0
        assert body["ingredients"] == []

    async def test_happy_path_returns_sorted_ingredients(self, app, tmp_path):
        """Sub-Expert spells → ingredients returned, sorted primary first."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        cached = _FakeCharCached(spell_ids=[101])
        adept_row = {
            "name": "Divine Favor",
            "tier_name": "Adept",
            "type": "spells",
            "given_by": "spellscroll",
            "level": 90,
        }
        fake_recipe = {
            "primary_comp": "Lead Cluster",
            "primary_qty": 4,
            "secondary_comps": [],
            "fuel_comp": "Coal",
            "fuel_qty": 1,
        }
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=[adept_row],
            ),
            patch(
                "backend.server.api.character.upgrades._find_spell_recipes",
                return_value={"Divine Favor": fake_recipe},
            ),
            patch(
                "backend.server.api.character.upgrades._lookup_items_by_name",
                return_value={},
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 200
        body = r.json()
        assert body["spells_needing_upgrade"] == 1
        assert body["spells_with_recipe"] == 1
        cats = [i["category"] for i in body["ingredients"]]
        # Primary before fuel
        assert cats.index("primary") < cats.index("fuel")

    async def test_two_recipes_sum_shared_ingredient(self, app, tmp_path):
        """Same ingredient across two recipes → quantities summed."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        cached = _FakeCharCached(spell_ids=[101, 102])
        rows = {
            101: {"name": "Spell A", "tier_name": "Adept", "type": "spells", "given_by": "spellscroll", "level": 90},
            102: {
                "name": "Spell B",
                "tier_name": "Journeyman",
                "type": "spells",
                "given_by": "spellscroll",
                "level": 90,
            },
        }
        recipes = {
            "Spell A": {
                "primary_comp": "Lead Cluster",
                "primary_qty": 3,
                "secondary_comps": [],
                "fuel_comp": None,
                "fuel_qty": None,
            },
            "Spell B": {
                "primary_comp": "Lead Cluster",
                "primary_qty": 5,
                "secondary_comps": [],
                "fuel_comp": None,
                "fuel_qty": None,
            },
        }
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=list(rows.values()),
            ),
            patch("backend.server.api.character.upgrades._find_spell_recipes", return_value=recipes),
            patch("backend.server.api.character.upgrades._lookup_items_by_name", return_value={}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 200
        body = r.json()
        # Should have exactly one "Lead Cluster" entry with qty=8
        lead = next(i for i in body["ingredients"] if "Lead" in i["name"])
        assert lead["quantity"] == 8


# ---------------------------------------------------------------------------
# GET /api/character/{name}/upgrade-recipes
# ---------------------------------------------------------------------------


class TestGetUpgradeRecipes:
    async def test_name_too_long_returns_400(self, app):
        """Character name over 64 chars → 400 before any DB check."""
        long_name = "A" * 65
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(f"/api/character/{long_name}/upgrade-recipes")
        assert r.status_code == 400

    async def test_spells_db_missing_returns_503(self, app):
        """Spells DB absent → 503."""
        with patch(
            "backend.server.api.character.upgrades._SPELLS_DB",
            new=Path("/nonexistent/spells.db"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-recipes")
        assert r.status_code == 503

    async def test_character_not_found_returns_404(self, app, tmp_path):
        """Character missing from Census → 404."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(None, False),
            ),
            patch("backend.server.api.character.upgrades.shared_census_client") as mock_ctx,
        ):
            mock_client = AsyncMock()
            mock_client.get_character = AsyncMock(return_value=None)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Ghost/upgrade-recipes")
        assert r.status_code == 404

    async def test_happy_path_returns_recipe_list(self, app, tmp_path):
        """Sub-Expert spells with recipes → list of RecipeResult objects."""
        fake_spells = tmp_path / "s.db"
        fake_spells.touch()
        fake_recipes = tmp_path / "r.db"
        fake_recipes.touch()
        cached = _FakeCharCached(spell_ids=[101])
        adept_row = {
            "name": "Divine Favor",
            "tier_name": "Adept",
            "type": "spells",
            "given_by": "spellscroll",
            "level": 90,
        }
        fake_recipe = {
            "id": 999,
            "name": "Expert: Divine Favor",
            "bench": "Chemistry Table",
            "primary_comp": "Lead Cluster",
            "primary_qty": 4,
            "secondary_comps": [],
            "fuel_comp": "Coal",
            "fuel_qty": 1,
            "crafted_tier": "Expert",
            "out_formed_id": 555,
            "out_formed_count": 1,
        }
        with (
            patch("backend.server.api.character.upgrades._SPELLS_DB", new=fake_spells),
            patch("backend.server.api.character.upgrades._RECIPES_DB", new=fake_recipes),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=[adept_row],
            ),
            patch(
                "backend.server.api.character.upgrades._find_spell_recipes",
                return_value={"Divine Favor": fake_recipe},
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-recipes")
        assert r.status_code == 200
        body = r.json()
        assert body["spells_needing_upgrade"] == 1
        assert body["spells_with_recipe"] == 1
        assert len(body["results"]) == 1
        assert body["results"][0]["primary_comp"] == "Lead Cluster"


# ---------------------------------------------------------------------------
# _lookup_items_by_name (pure unit tests — no HTTP layer)
# ---------------------------------------------------------------------------


class TestLookupItemsByName:
    def _create_items_db(self, tmp_path: Path) -> Path:
        """Create a minimal items DB with a few test rows."""
        db_path = tmp_path / "items.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """CREATE TABLE items (
                    id INTEGER PRIMARY KEY,
                    displayname TEXT NOT NULL,
                    displayname_lower TEXT NOT NULL,
                    icon_id INTEGER,
                    tier_display TEXT,
                    description TEXT,
                    item_level INTEGER DEFAULT 0,
                    flag_no_value INTEGER DEFAULT 0,
                    max_stack_size INTEGER DEFAULT 1
                )"""
            )
            conn.executemany(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (1, "Lead Cluster", "lead cluster", 100, "COMMON", None, 1, 1, 800),
                    (2, "Rough Opaline", "rough opaline", 101, "COMMON", None, 1, 1, 800),
                    (3, "Severed Root", "severed root", 102, "COMMON", None, 1, 1, 800),
                ],
            )
            conn.commit()
        return db_path

    def test_exact_match_after_stripping_raw_prefix(self, tmp_path):
        """'Raw Lead Cluster' → stripped to 'Lead Cluster' → found (pass-1)."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        db_path = self._create_items_db(tmp_path)
        with patch("backend.server.api.character.upgrades._ITEMS_DB", new=db_path):
            result = _lookup_items_by_name(["Raw Lead Cluster"])
        assert "raw lead cluster" in result
        assert result["raw lead cluster"]["display_name"] == "Lead Cluster"

    def test_fuzzy_pass2_for_renamed_raw_material(self, tmp_path):
        """'Raw Opaline' doesn't exist; fuzzy LIKE finds 'Rough Opaline' (pass-2)."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        db_path = self._create_items_db(tmp_path)
        with patch("backend.server.api.character.upgrades._ITEMS_DB", new=db_path):
            result = _lookup_items_by_name(["Raw Opaline"])
        assert "raw opaline" in result
        assert result["raw opaline"]["display_name"] == "Rough Opaline"

    def test_non_raw_uses_exact_match(self, tmp_path):
        """Non-'Raw' name: 'Severed Root' → exact match only, no fuzzy."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        db_path = self._create_items_db(tmp_path)
        with patch("backend.server.api.character.upgrades._ITEMS_DB", new=db_path):
            result = _lookup_items_by_name(["Severed Root"])
        assert "severed root" in result
        assert result["severed root"]["display_name"] == "Severed Root"

    def test_missing_items_absent_from_result(self, tmp_path):
        """Items not in the DB are simply missing from the returned dict."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        db_path = self._create_items_db(tmp_path)
        with patch("backend.server.api.character.upgrades._ITEMS_DB", new=db_path):
            result = _lookup_items_by_name(["Nonexistent Material"])
        assert "nonexistent material" not in result

    def test_returns_empty_when_items_db_absent(self, tmp_path):
        """If the items DB file doesn't exist, returns an empty dict."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        missing = tmp_path / "no_items.db"
        with patch("backend.server.api.character.upgrades._ITEMS_DB", new=missing):
            result = _lookup_items_by_name(["Lead Cluster"])
        assert result == {}
