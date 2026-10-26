"""Uploads from a skipped zone (INGEST_SKIP_ZONES) are answered "skipped" and
never stored.

Added for The Thundering Steppes' 2026 Halloween public quest: hundreds of
players per fight filled two thirds of the combatant rows in two weeks.
"skipped" is the answer clients already treat as done, so they neither retry
nor pause their upload queue.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.server._parses_ingest_fixtures import _fake_require_user, _minimal_payload, _signed_post_kwargs

_SKIP = frozenset({"the thundering steppes"})


async def _post(app, zone: str) -> tuple[int, dict, MagicMock]:
    payload = _minimal_payload()
    payload["encounter"]["zone"] = zone
    store = MagicMock(return_value=("inserted", 42, 1, 0, 0))
    with (
        patch("backend.server.api.parses.ingest._INGEST_SKIP_ZONES", _SKIP),
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=store),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    return r.status_code, r.json(), store


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["The Thundering Steppes", "the thundering steppes", "  THE THUNDERING STEPPES "])
async def test_upload_from_a_skipped_zone_is_answered_skipped_and_not_stored(app, zone):
    status, body, store = await _post(app, zone)
    assert status == 201
    assert body["status"] == "skipped"
    assert body["encounter_id"] is None
    store.assert_not_called()


@pytest.mark.asyncio
async def test_upload_from_any_other_zone_is_stored(app):
    status, body, store = await _post(app, "Shard of Hate")
    assert (status, body["status"]) == (201, "inserted")
    store.assert_called_once()


def test_the_setting_parses_names_and_can_be_emptied(monkeypatch):
    import importlib

    import backend.server.config as config

    try:
        monkeypatch.setenv("INGEST_SKIP_ZONES", " Zone A ; zone b,, ")
        assert importlib.reload(config).INGEST_SKIP_ZONES == frozenset({"zone a", "zone b"})
        monkeypatch.setenv("INGEST_SKIP_ZONES", "")
        assert importlib.reload(config).INGEST_SKIP_ZONES == frozenset()
        monkeypatch.delenv("INGEST_SKIP_ZONES")
        assert importlib.reload(config).INGEST_SKIP_ZONES == frozenset({"the thundering steppes"})
    finally:
        monkeypatch.delenv("INGEST_SKIP_ZONES", raising=False)
        importlib.reload(config)
