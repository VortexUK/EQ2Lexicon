"""Cache-first resolution tests for the parse-ingest path.

Verifies that _resolve_combatant_snapshots / _resolve_uploader_guild_async serve
from the durable census_store before ever touching Census, and write through to
it on a genuine Census fetch — so a character resolved once is never re-fetched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.census import store as census_store
from backend.server.api.parses import ingest


class _FakeResp:
    """Stand-in for a CharacterResponse: attribute access for
    _snapshot_from_cache + a model_dump() for the store write-through."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


@pytest.fixture
def store_db(tmp_path, monkeypatch):
    """Temp census_store wired into the ingest path via DB_PATH."""
    db = tmp_path / "census.db"
    monkeypatch.setattr(census_store.store, "path", db)
    conn = census_store.CensusStore(db).init_db()
    yield conn
    conn.close()


def _seed(conn, name, **data):
    census_store.CensusStore.upsert_character(conn, name, ingest._WORLD, data, resolved=True)


def _empty_cache():
    """A character_cache mock that always misses (forces the store path)."""
    m = MagicMock()
    m.get_stale.return_value = (None, False)
    return m


def _client_factory(client):
    """Patch value for shared_census_client — a zero-arg callable returning an
    async-context-manager that yields `client`."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.mark.asyncio
async def test_combatant_snapshot_served_from_store_without_census(store_db):
    _seed(store_db, "Raider", level=100, guild_name="Exordium", cls="Wizard", ilvl=350.0)

    client = MagicMock()
    client.get_character_guild_name = AsyncMock(side_effect=AssertionError("Census must not be called"))
    client.get_character = AsyncMock(side_effect=AssertionError("Census must not be called"))

    with (
        patch.object(ingest, "character_cache", _empty_cache()),
        patch.object(ingest, "shared_census_client", _client_factory(client)),
    ):
        out = await ingest._resolve_combatant_snapshots(["Raider"], None)

    snap = out["Raider"]
    assert snap.guild_name == "Exordium"
    assert snap.cls == "Wizard"
    assert snap.level == 100
    assert snap.ilvl == 350.0
    client.get_character_guild_name.assert_not_called()
    client.get_character.assert_not_called()


@pytest.mark.asyncio
async def test_uploader_guild_served_from_store_without_census_or_prewarm(store_db):
    _seed(store_db, "Uploader", level=100, guild_name="Exordium", cls="Templar", ilvl=340.0)

    client = MagicMock()
    client.get_character_guild_name = AsyncMock(side_effect=AssertionError("Census must not be called"))

    with (
        patch.object(ingest, "character_cache", _empty_cache()),
        patch.object(ingest, "shared_census_client", _client_factory(client)),
        patch.object(ingest, "_prewarm_guild_silently", new=AsyncMock()) as prewarm,
    ):
        result = await ingest._resolve_uploader_guild_async("Uploader", None)

    assert result == "Exordium"
    client.get_character_guild_name.assert_not_called()
    prewarm.assert_not_called()


@pytest.mark.asyncio
async def test_ilvl_backfill_writes_through_to_store(store_db):
    # Store has class/level/guild but no equipment → ilvl None triggers backfill.
    _seed(store_db, "Raider", level=100, guild_name="Exordium", cls="Wizard", ilvl=None)

    client = MagicMock()
    client.get_character = AsyncMock(return_value=object())  # non-None → backfill proceeds
    healed = _FakeResp(level=100, guild_name="Exordium", cls="Wizard", ilvl=350.0, id="1", world=ingest._WORLD)

    with (
        patch.object(ingest, "character_cache", _empty_cache()),
        patch.object(ingest, "shared_census_client", _client_factory(client)),
        patch("backend.server.api.character._build_char_response", return_value=healed),
    ):
        out = await ingest._resolve_combatant_snapshots(["Raider"], None)

    assert out["Raider"].ilvl == 350.0
    client.get_character.assert_awaited_once()
    # Written through to the durable store.
    rec = census_store.CensusStore.get_character(store_db, "Raider", ingest._WORLD)
    assert rec is not None
    assert rec["data"]["ilvl"] == 350.0

    # Second pass: store now has the ilvl → no backfill Census call.
    client2 = MagicMock()
    client2.get_character = AsyncMock(side_effect=AssertionError("must not re-fetch"))
    client2.get_character_guild_name = AsyncMock(side_effect=AssertionError("must not re-fetch"))
    with (
        patch.object(ingest, "character_cache", _empty_cache()),
        patch.object(ingest, "shared_census_client", _client_factory(client2)),
    ):
        out2 = await ingest._resolve_combatant_snapshots(["Raider"], None)
    assert out2["Raider"].ilvl == 350.0
    client2.get_character.assert_not_called()


@pytest.mark.asyncio
async def test_cached_snapshots_response_path_falls_back_to_store(store_db):
    _seed(store_db, "Raider", level=95, guild_name="Exordium", cls="Brigand", ilvl=300.0)
    with patch.object(ingest, "character_cache", _empty_cache()):
        out = ingest._cached_snapshots(["Raider", "Unknown"], None)
    assert out["Raider"].guild_name == "Exordium"
    assert out["Raider"].cls == "Brigand"
    assert "Unknown" not in out  # absent from both cache and store → skipped
