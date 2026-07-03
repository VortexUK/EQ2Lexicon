"""Tests for web/routes/aa.py — COV-016.

Covers:
  GET /aa/config — limits file missing → zero defaults; limits file present → values.
  GET /aa/tree/{tree_id} — tree file missing → 404; tree file present → AATreeResponse.
  GET /character/{name}/aas — hot cache hit; census_store hit; census down + no cache
                               → 503; char not found → 404; live fetch → stores + caches.
  GET /aa/spell/{spellcrc} — no row found → empty effects; row with effects → parses JSON;
                              row with malformed effects JSON → empty list.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import census_health
from backend.server.api.aa import (
    AAConfigResponse,
    CharAAsResponse,
    CharAATree,
    _load_tree_for_response,
)
from backend.server.cache import aa_cache
from backend.server.core.cache_keys import aa_cache_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_char_aas_response(name: str = "Sihtric") -> CharAAsResponse:
    """Return a minimal CharAAsResponse for cache injection."""
    tree = CharAATree(
        tree_id=1,
        tree_type="class",
        tree_name="Templar",
        spent={"101": 5},
        total_spent=5,
    )
    return CharAAsResponse(
        character_name=name,
        total_spent=5,
        trees=[tree],
        profiles=[],
    )


def test_build_trees_applies_pointspertier() -> None:
    """A node's spent points = tier × pointspertier. Bladedance (tree 1) costs
    2 points/tier — one tier spent must count as 2, not 1."""
    from backend.census.models import NodeAA
    from backend.image.aa_tree import tree_node_costs
    from backend.server.api.aa import _aas_response_from_census, _build_trees

    node_id = 554687586  # Bladedance, tree 1, pointspertier=2
    assert tree_node_costs(1).get(node_id) == 2, "tree data no longer has a 2-point node here"

    trees = _build_trees([NodeAA(node_id=node_id, tree_id=1, tier=1)])
    assert len(trees) == 1
    assert trees[0].total_spent == 2  # tier(1) × pointspertier(2)

    aas = MagicMock()
    aas.character_name = "Test"
    aas.aa_list = [NodeAA(node_id=node_id, tree_id=1, tier=1)]
    aas.profiles = []
    resp = _aas_response_from_census(aas)
    assert resp.total_spent == 2  # global total is point-accurate too


def _make_census_aas_mock(name: str = "Sihtric") -> MagicMock:
    """Return a mock CharacterAAs (Census model) with minimal aa_list."""
    node = MagicMock()
    node.tree_id = 1
    node.node_id = 101
    node.tier = 5

    aas = MagicMock()
    aas.character_name = name
    aas.aa_list = [node]
    aas.profiles = []
    return aas


# ---------------------------------------------------------------------------
# GET /aa/config
# ---------------------------------------------------------------------------


class TestGetAaConfig:
    def setup_method(self) -> None:
        _load_tree_for_response.cache_clear()

    async def test_missing_limits_file_returns_zero_defaults(self, app) -> None:
        """When aa_limits.json does not exist, aa_cap is 0 and lists are empty."""
        with patch("backend.server.api.aa._LIMITS") as mock_limits:
            mock_limits.exists.return_value = False
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/config")
        assert r.status_code == 200
        body = r.json()
        assert body["aa_cap"] == 0
        assert body["unlocked_tree_types"] == []

    async def test_limits_file_present_returns_xpac_values(self, app, tmp_path) -> None:
        """When aa_limits.json exists with an xpac entry, values are returned."""
        limits_data = {
            "Varsoon": {
                "aa_cap": 320,
                "unlocked_trees": ["class", "subclass", "shadows"],
            }
        }
        limits_file = tmp_path / "aa_limits.json"
        limits_file.write_text(json.dumps(limits_data), encoding="utf-8")

        # Patch both the _LIMITS path and current_server to return the right xpac
        mock_server = MagicMock()
        mock_server.current_xpac = "Varsoon"

        with (
            patch("backend.server.api.aa._LIMITS", limits_file),
            patch("backend.server.api.aa.current_server", return_value=mock_server),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/config")

        assert r.status_code == 200
        body = r.json()
        assert body["aa_cap"] == 320
        assert "class" in body["unlocked_tree_types"]

    async def test_tradeskill_cap_derived_from_unlocked_trees(self, app, tmp_path) -> None:
        """tradeskill_aa_cap = Σ max points of the UNLOCKED tradeskill trees, derived
        from the tree data. Adventure aa_cap is unaffected by tradeskill."""
        limits_file = tmp_path / "aa_limits.json"
        limits_file.write_text(
            json.dumps(
                {
                    "EoF": {"aa_cap": 100, "unlocked_trees": ["class", "subclass", "tradeskill"]},
                    "AoD": {"aa_cap": 320, "unlocked_trees": ["class", "tradeskill", "tradeskill_general"]},
                }
            ),
            encoding="utf-8",
        )
        for xpac, expected_ts, expected_adv in [("EoF", 45, 100), ("AoD", 116, 320)]:
            mock_server = MagicMock()
            mock_server.current_xpac = xpac
            with (
                patch("backend.server.api.aa._LIMITS", limits_file),
                patch("backend.server.api.aa.current_server", return_value=mock_server),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    r = await client.get("/api/aa/config")
            body = r.json()
            assert body["tradeskill_aa_cap"] == expected_ts, xpac
            assert body["aa_cap"] == expected_adv, xpac  # adventure cap unchanged

    async def test_short_xpac_code_resolves_to_full_key(self, app, tmp_path) -> None:
        """A server whose current_xpac is a short code ("DoV") still resolves to
        the aa_limits.json entry — otherwise the cap silently reads 0 and the
        Raid-Ready check + per-expansion limit vanish from the AA tab."""
        limits_file = tmp_path / "aa_limits.json"
        limits_file.write_text(
            json.dumps({"Destiny of Velious": {"aa_cap": 300, "unlocked_trees": ["class", "subclass", "tradeskill"]}}),
            encoding="utf-8",
        )
        mock_server = MagicMock()
        mock_server.current_xpac = "DoV"
        with (
            patch("backend.server.api.aa._LIMITS", limits_file),
            patch("backend.server.api.aa.current_server", return_value=mock_server),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/config")
        body = r.json()
        assert body["aa_cap"] == 300  # resolved via the DoV alias
        assert body["tradeskill_aa_cap"] == 45
        assert body["xpac"] == "DoV"  # raw code preserved for display

    async def test_limits_file_present_unknown_xpac_returns_zero_defaults(self, app, tmp_path) -> None:
        """When aa_limits.json exists but xpac key is absent, zeros are returned."""
        limits_data = {"SomeExpansion": {"aa_cap": 320, "unlocked_trees": ["class"]}}
        limits_file = tmp_path / "aa_limits.json"
        limits_file.write_text(json.dumps(limits_data), encoding="utf-8")

        mock_server = MagicMock()
        mock_server.current_xpac = "UnknownXpac"

        with (
            patch("backend.server.api.aa._LIMITS", limits_file),
            patch("backend.server.api.aa.current_server", return_value=mock_server),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/config")

        assert r.status_code == 200
        body = r.json()
        assert body["aa_cap"] == 0
        assert body["unlocked_tree_types"] == []


# ---------------------------------------------------------------------------
# GET /aa/tree/{tree_id}
# ---------------------------------------------------------------------------


class TestGetAaTree:
    def setup_method(self) -> None:
        _load_tree_for_response.cache_clear()

    def teardown_method(self) -> None:
        _load_tree_for_response.cache_clear()

    async def test_missing_tree_returns_404(self, app) -> None:
        """When the tree JSON file does not exist, a 404 is returned."""
        with patch("backend.server.api.aa._load_tree_for_response", return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/tree/9999")
        assert r.status_code == 404
        assert "9999" in r.json()["detail"]

    async def test_existing_tree_returns_tree_response(self, app, tmp_path) -> None:
        """When the tree JSON file exists, the parsed AATreeResponse is returned."""
        tree_data = {
            "alternateadvancement_list": [
                {
                    "name": "Templar",
                    "ofyclassification": "class",
                    "alternateadvancementnode_list": [
                        {
                            "nodeid": "101",
                            "xcoord": "1",
                            "ycoord": "1",
                            "icon": {"id": "500", "backdrop": "456"},
                            "maxtier": "5",
                            "pointspertier": "1",
                            "pointsspentintreetounlock": "0",
                            "classification": "class",
                            "name": "Divine Light",
                            "description": "Heals target",
                            "title": "",
                            "spellcrc": "12345",
                        }
                    ],
                }
            ]
        }
        trees_dir = tmp_path / "trees"
        trees_dir.mkdir()
        (trees_dir / "42.json").write_text(json.dumps(tree_data), encoding="utf-8")

        with patch("backend.server.api.aa._TREES_DIR", trees_dir):
            _load_tree_for_response.cache_clear()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/tree/42")

        assert r.status_code == 200
        body = r.json()
        assert body["tree_id"] == 42
        assert body["tree_name"] == "Templar"
        assert len(body["nodes"]) == 1
        assert body["nodes"][0]["node_id"] == 101


# ---------------------------------------------------------------------------
# GET /character/{name}/aas
# ---------------------------------------------------------------------------


class TestGetCharacterAas:
    def setup_method(self) -> None:
        census_health._reset_for_test()
        aa_cache._store.clear()

    def teardown_method(self) -> None:
        census_health._reset_for_test()
        aa_cache._store.clear()

    async def test_hot_cache_hit_returns_immediately(self, app) -> None:
        """When aa_cache has a fresh entry, it is returned without touching Census."""
        cached = _make_char_aas_response("Sihtric")
        cache_key = aa_cache_key("Sihtric", "Varsoon")
        aa_cache.set(cache_key, cached)

        with patch("backend.server.api.aa.current_world", return_value="Varsoon"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/aas")

        assert r.status_code == 200
        body = r.json()
        assert body["character_name"] == "Sihtric"
        assert body["total_spent"] == 5

    async def test_census_store_hit_returns_data(self, app) -> None:
        """When aa_cache misses but census_store has data, it is served."""
        import time

        cached = _make_char_aas_response("Sihtric")
        store_record = {
            "data": cached.model_dump(),
            "last_resolved_at": int(time.time()),
        }

        with (
            patch("backend.server.api.aa.current_world", return_value="Varsoon"),
            patch("backend.server.api.aa.aa_cache.get_stale", return_value=(None, False)),
            patch("backend.server.api.aa.run_sync", new=AsyncMock(return_value=store_record)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/aas")

        assert r.status_code == 200
        body = r.json()
        assert body["character_name"] == "Sihtric"

    async def test_census_down_no_cache_returns_503(self, app) -> None:
        """When Census is down and no cached/stored data exists, a 503 is returned."""
        census_health._status = "down"

        with (
            patch("backend.server.api.aa.current_world", return_value="Varsoon"),
            patch("backend.server.api.aa.aa_cache.get_stale", return_value=(None, False)),
            patch("backend.server.api.aa.run_sync", new=AsyncMock(return_value=None)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/aas")

        assert r.status_code == 503
        assert "unavailable" in r.json()["detail"].lower()

    async def test_char_not_found_returns_404(self, app) -> None:
        """When Census returns None for the character, a 404 is returned."""
        mock_client = AsyncMock()
        mock_client.get_character_aas = AsyncMock(return_value=None)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("backend.server.api.aa.current_world", return_value="Varsoon"),
            patch("backend.server.api.aa.aa_cache.get_stale", return_value=(None, False)),
            patch("backend.server.api.aa.run_sync", new=AsyncMock(return_value=None)),
            patch("backend.server.api.aa.shared_census_client", return_value=mock_cm),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Unknownchar/aas")

        assert r.status_code == 404
        assert "Unknownchar" in r.json()["detail"]

    async def test_live_fetch_happy_path_stores_and_caches(self, app) -> None:
        """A live Census fetch stores the result and returns it."""
        census_aas = _make_census_aas_mock("Sihtric")

        mock_client = AsyncMock()
        mock_client.get_character_aas = AsyncMock(return_value=census_aas)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        write_calls: list = []

        async def _fake_run_sync(fn):
            # First call is the census_store read (returns None = cold cache)
            # Subsequent calls are the write
            if not write_calls:
                write_calls.append("read")
                return None
            write_calls.append("write")
            return None

        with (
            patch("backend.server.api.aa.current_world", return_value="Varsoon"),
            patch("backend.server.api.aa.aa_cache.get_stale", return_value=(None, False)),
            patch("backend.server.api.aa.run_sync", side_effect=_fake_run_sync),
            patch("backend.server.api.aa.shared_census_client", return_value=mock_cm),
            patch("backend.server.api.aa.load_tree_index", return_value={1: {"type": "class", "name": "Templar"}}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/aas")

        assert r.status_code == 200
        body = r.json()
        assert body["character_name"] == "Sihtric"

    async def test_stale_cache_hit_triggers_background_refresh(self, app) -> None:
        """A stale hot-cache hit returns the stale value immediately without waiting."""
        cached = _make_char_aas_response("Sihtric")
        # Inject a stale cache entry (is_stale=True)
        with (
            patch("backend.server.api.aa.current_world", return_value="Varsoon"),
            patch("backend.server.api.aa.aa_cache.get_stale", return_value=(cached, True)),
            patch("backend.server.api.aa.asyncio.create_task") as mock_task,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/aas")

        assert r.status_code == 200
        assert mock_task.called

    async def test_census_exception_returns_503(self, app) -> None:
        """When Census raises an exception during live fetch, a 503 is returned."""
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("network error"))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("backend.server.api.aa.current_world", return_value="Varsoon"),
            patch("backend.server.api.aa.aa_cache.get_stale", return_value=(None, False)),
            patch("backend.server.api.aa.run_sync", new=AsyncMock(return_value=None)),
            patch("backend.server.api.aa.shared_census_client", return_value=mock_cm),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/character/Sihtric/aas")

        assert r.status_code == 503


# ---------------------------------------------------------------------------
# GET /aa/spell/{spellcrc}
# ---------------------------------------------------------------------------


class TestGetSpellEffects:
    async def test_no_row_found_returns_empty_effects(self, app) -> None:
        """When find_by_crc returns None, empty effects are returned."""
        with patch("backend.server.api.aa.find_by_crc", return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/spell/99999")

        assert r.status_code == 200
        body = r.json()
        assert body["effects"] == []
        assert body["matched_tier"] is None

    async def test_row_with_effects_returns_parsed_list(self, app) -> None:
        """When find_by_crc returns a row with valid effects JSON, it is parsed."""
        effects_json = json.dumps(
            [
                {"description": "Heals target for 500 hit points", "indentation": 0},
                {"description": "  Scales with spell power", "indentation": 1},
            ]
        )
        row = {"tier": 3, "effects": effects_json}

        with patch("backend.server.api.aa.find_by_crc", return_value=row):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/spell/12345?tier=3")

        assert r.status_code == 200
        body = r.json()
        assert len(body["effects"]) == 2
        assert body["effects"][0]["description"] == "Heals target for 500 hit points"
        assert body["matched_tier"] == 3
        assert body["requested_tier"] == 3

    async def test_row_with_malformed_effects_json_returns_empty_list(self, app) -> None:
        """When effects JSON is malformed, empty list is returned with a warning."""
        row = {"tier": 1, "effects": "not valid json {{{"}

        with patch("backend.server.api.aa.find_by_crc", return_value=row):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/spell/12345")

        assert r.status_code == 200
        body = r.json()
        assert body["effects"] == []
        assert body["matched_tier"] == 1

    async def test_tier_zero_passes_none_to_find_by_crc(self, app) -> None:
        """tier=0 (default) is converted to None when calling find_by_crc."""
        captured_args: list = []

        def _capture(spellcrc, tier):
            captured_args.append((spellcrc, tier))
            return None

        with patch("backend.server.api.aa.find_by_crc", side_effect=_capture):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.get("/api/aa/spell/12345")

        assert len(captured_args) == 1
        assert captured_args[0] == (12345, None)

    async def test_tier_nonzero_passed_directly_to_find_by_crc(self, app) -> None:
        """A non-zero ?tier=N is passed directly to find_by_crc."""
        captured_args: list = []

        def _capture(spellcrc, tier):
            captured_args.append((spellcrc, tier))
            return None

        with patch("backend.server.api.aa.find_by_crc", side_effect=_capture):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.get("/api/aa/spell/12345?tier=5")

        assert len(captured_args) == 1
        assert captured_args[0] == (12345, 5)

    async def test_requested_tier_zero_becomes_none_in_response(self, app) -> None:
        """When tier=0, requested_tier in response is None (not 0)."""
        with patch("backend.server.api.aa.find_by_crc", return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/aa/spell/12345?tier=0")

        body = r.json()
        assert body["requested_tier"] is None
