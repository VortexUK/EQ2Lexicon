"""HTTP-layer tests for backend/server/api/character/upgrades.py — COV-006.

Covers:
  GET /api/character/{name}/upgrade-materials
    — spells catalogue not ready → 503
    — recipes catalogue not ready → 503
    — character not found → 404
    — empty spell_ids → zero counts, no ingredients
    — no sub-expert spells → zero counts, no ingredients
    — happy path: aggregates ingredients sorted by category/qty
    — two recipes sharing an ingredient sum their quantities
    — sort order: primary first, then secondary, then fuel
  GET /api/character/{name}/upgrade-recipes
    — spells catalogue not ready → 503
    — character not found → 404
    — happy path returns RecipeResult list
    — character name too long → 400
  _lookup_items_by_name
    — exact match on stripped "Raw X" (pass-1)
    — pass-1 miss triggers LIKE fuzzy search (pass-2)
    — non-"Raw" name uses exact match only
    — items catalogue not ready (empty schema) → returns empty dict

Postgres edition: the old ``_SPELLS_DB``/``_RECIPES_DB`` file-exists gates
became ``catalogue.ready()`` probes, so the 503 paths patch ``ready`` on the
shared catalogue instances. The _lookup_items_by_name tests seed the leased
items schema via ``pg_conn`` (the ``items_schema`` fixture re-points the
shared catalogue at the lease). All Census calls stay mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from tests.fixtures.pg import pg_conn

# ---------------------------------------------------------------------------
# Helpers: minimal character cache object + catalogue ready() gates
# ---------------------------------------------------------------------------


class _FakeCharCached:
    def __init__(self, spell_ids=None, guild_name="Exordium"):
        self.spell_ids = spell_ids or []
        self.guild_name = guild_name


def _spells_ready(value: bool):
    """Patch the spells catalogue's ready() gate — the Postgres analog of
    the old ``_SPELLS_DB`` path patch (exists ↔ ready)."""
    return patch("backend.server.api.character.upgrades._spells.ready", return_value=value)


def _recipes_ready(value: bool):
    """Patch the recipes catalogue's ready() gate — the Postgres analog of
    the old ``_RECIPES_DB`` path patch (exists ↔ ready)."""
    return patch("backend.server.api.character.upgrades._recipes.ready", return_value=value)


# ---------------------------------------------------------------------------
# GET /api/character/{name}/upgrade-materials
# ---------------------------------------------------------------------------


class TestGetUpgradeMaterials:
    async def test_spells_db_missing_returns_503(self, app):
        """If the spells catalogue isn't loaded, returns 503."""
        with _spells_ready(False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 503
        assert "Spells" in r.json()["detail"]

    async def test_recipes_db_missing_returns_503(self, app):
        """If the spells catalogue is loaded but recipes isn't, returns 503."""
        with _spells_ready(True), _recipes_ready(False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-materials")
        assert r.status_code == 503
        assert "Recipes" in r.json()["detail"]

    async def test_character_not_found_returns_404(self, app):
        """Character doesn't exist on Census → 404."""
        with (
            _spells_ready(True),
            _recipes_ready(True),
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

    async def test_empty_spell_ids_returns_zero_counts(self, app):
        """Cached character with no spell_ids → all-zero response."""
        cached = _FakeCharCached(spell_ids=[])
        with (
            _spells_ready(True),
            _recipes_ready(True),
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

    async def test_no_sub_expert_spells_returns_zero_counts(self, app):
        """Character has only Expert spells — nothing to upgrade."""
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
            _spells_ready(True),
            _recipes_ready(True),
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

    async def test_happy_path_returns_sorted_ingredients(self, app):
        """Sub-Expert spells → ingredients returned, sorted primary first."""
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
            _spells_ready(True),
            _recipes_ready(True),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=[adept_row],
            ),
            patch(
                "backend.server.api.character.upgrades._recipes.find_spells_by_tier",
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

    async def test_two_recipes_sum_shared_ingredient(self, app):
        """Same ingredient across two recipes → quantities summed."""
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
            _spells_ready(True),
            _recipes_ready(True),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=list(rows.values()),
            ),
            patch("backend.server.api.character.upgrades._recipes.find_spells_by_tier", return_value=recipes),
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
        """Spells catalogue not loaded → 503."""
        with _spells_ready(False):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/upgrade-recipes")
        assert r.status_code == 503

    async def test_character_not_found_returns_404(self, app):
        """Character missing from Census → 404."""
        with (
            _spells_ready(True),
            _recipes_ready(True),
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

    async def test_happy_path_returns_recipe_list(self, app):
        """Sub-Expert spells with recipes → list of RecipeResult objects."""
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
            _spells_ready(True),
            _recipes_ready(True),
            patch(
                "backend.server.api.character.upgrades.character_cache.get_stale",
                return_value=(cached, True),
            ),
            patch(
                "backend.server.api.character.upgrades._spells.character_upgradeable_spells",
                return_value=[adept_row],
            ),
            patch(
                "backend.server.api.character.upgrades._recipes.find_spells_by_tier",
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
    def _seed_items(self, schema: str) -> None:
        """Seed a few item rows into the leased items schema."""
        with pg_conn(schema) as conn:
            conn.cursor().executemany(
                "INSERT INTO items (id, displayname, displayname_lower, icon_id, tier_display, "
                "description, item_level, flag_no_value, max_stack_size) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (1, "Lead Cluster", "lead cluster", 100, "COMMON", None, 1, 1, 800),
                    (2, "Rough Opaline", "rough opaline", 101, "COMMON", None, 1, 1, 800),
                    (3, "Severed Root", "severed root", 102, "COMMON", None, 1, 1, 800),
                ],
            )

    def test_exact_match_after_stripping_raw_prefix(self, items_schema):
        """'Raw Lead Cluster' → stripped to 'Lead Cluster' → found (pass-1)."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        self._seed_items(items_schema)
        result = _lookup_items_by_name(["Raw Lead Cluster"])
        assert "raw lead cluster" in result
        assert result["raw lead cluster"]["display_name"] == "Lead Cluster"

    def test_fuzzy_pass2_for_renamed_raw_material(self, items_schema):
        """'Raw Opaline' doesn't exist; fuzzy LIKE finds 'Rough Opaline' (pass-2)."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        self._seed_items(items_schema)
        result = _lookup_items_by_name(["Raw Opaline"])
        assert "raw opaline" in result
        assert result["raw opaline"]["display_name"] == "Rough Opaline"

    def test_non_raw_uses_exact_match(self, items_schema):
        """Non-'Raw' name: 'Severed Root' → exact match only, no fuzzy."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        self._seed_items(items_schema)
        result = _lookup_items_by_name(["Severed Root"])
        assert "severed root" in result
        assert result["severed root"]["display_name"] == "Severed Root"

    def test_missing_items_absent_from_result(self, items_schema):
        """Items not in the DB are simply missing from the returned dict."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        self._seed_items(items_schema)
        result = _lookup_items_by_name(["Nonexistent Material"])
        assert "nonexistent material" not in result

    def test_returns_empty_when_items_db_absent(self, items_schema):
        """An EMPTY leased items schema reads as not-ready → empty dict (the
        Postgres analog of the old missing-catalogue-file case)."""
        from backend.server.api.character.upgrades import _lookup_items_by_name

        result = _lookup_items_by_name(["Lead Cluster"])
        assert result == {}
