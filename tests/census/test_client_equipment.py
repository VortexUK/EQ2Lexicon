"""Tests for CensusClient._resolve_item_meta — the items.db → Census → cache
fallback path used by _parse_equipment.

Before the fix this was items.db-only, which meant a cold items.db at character-
fetch time caused the equipment rows to be cached forever with the literal
"Item #<id>" placeholder (PR #21's persistent cache then served that placeholder
indefinitely). The fallback prevents new cache rows from ever being born stale.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.census.client import CensusClient


@pytest.fixture
async def client() -> AsyncGenerator[CensusClient]:
    """Yield a CensusClient and close it on test exit.

    Without this, the underlying aiohttp ClientSession leaks to GC, which
    fires aiohttp's 'Unclosed client session' destructor warning at process
    exit. On CI (Linux) that warning's logger.error call raises
    ValueError: I/O operation on closed file because pytest has closed
    stdout — the unraisable exception fails the test session with exit 1.
    """
    c = CensusClient(service_id="test")
    try:
        yield c
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_resolve_item_meta_returns_items_db_row_on_hit(client, monkeypatch):
    """Hot items.db → return the row directly, no Census fetch, no cache write."""
    from backend.eq2db.items import catalogue as item_db_module

    expected_row = {"displayname": "Hot Item", "tier": "FABLED", "iconid": 1234}

    async def _fake_find(item_id, *args, **kwargs):
        assert item_id == 42
        return expected_row

    monkeypatch.setattr(item_db_module, "find_by_id", _fake_find)

    client._fetch = AsyncMock(side_effect=AssertionError("_fetch must NOT fire on items.db hit"))
    client._cache_item = MagicMock(side_effect=AssertionError("_cache_item must NOT fire on items.db hit"))

    got = await client._resolve_item_meta(42)
    assert got is expected_row


@pytest.mark.asyncio
async def test_resolve_item_meta_falls_back_to_census_and_caches(client, monkeypatch):
    """items.db miss → single Census fetch → result persisted to items.db via
    _cache_item → returned to the caller. This is the path that prevents the
    'Item #<id>' placeholder from getting baked into the persistent cache."""
    from backend.eq2db.items import catalogue as item_db_module

    async def _fake_find(item_id, *args, **kwargs):
        return None  # items.db cold for this ID

    monkeypatch.setattr(item_db_module, "find_by_id", _fake_find)

    census_raw = {
        "id": "99999",
        "displayname": "Recovered Item",
        "tier": "MYTHICAL",
        "iconid": 7777,
    }

    client._build_params = MagicMock(return_value={"name": "99999"})
    client._fetch = AsyncMock(return_value={"item_list": [census_raw]})
    client._cache_item = MagicMock()

    got = await client._resolve_item_meta(99999)
    assert got is census_raw
    client._fetch.assert_awaited_once()
    client._cache_item.assert_called_once_with(census_raw)


@pytest.mark.asyncio
async def test_resolve_item_meta_returns_none_when_census_also_misses(client, monkeypatch):
    """items.db miss + Census miss → return None so the caller can render
    the 'Item #<id>' placeholder rather than crash. No cache write — we
    don't want to memoise 'unknown' against the ID."""
    from backend.eq2db.items import catalogue as item_db_module

    async def _fake_find(item_id, *args, **kwargs):
        return None

    monkeypatch.setattr(item_db_module, "find_by_id", _fake_find)

    client._build_params = MagicMock(return_value={"name": "111"})
    client._fetch = AsyncMock(return_value={"item_list": []})
    client._cache_item = MagicMock(side_effect=AssertionError("must not cache an empty result"))

    got = await client._resolve_item_meta(111)
    assert got is None
    client._fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_item_meta_handles_census_unreachable(client, monkeypatch):
    """Census API call returns None (network error already swallowed inside
    _fetch) → _resolve_item_meta returns None, no cache write, no crash."""
    from backend.eq2db.items import catalogue as item_db_module

    async def _fake_find(item_id, *args, **kwargs):
        return None

    monkeypatch.setattr(item_db_module, "find_by_id", _fake_find)

    client._build_params = MagicMock(return_value={"name": "222"})
    client._fetch = AsyncMock(return_value=None)
    client._cache_item = MagicMock(side_effect=AssertionError("must not cache on Census failure"))

    got = await client._resolve_item_meta(222)
    assert got is None
