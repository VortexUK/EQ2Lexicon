"""Tests for uploader Discord identity resolution in parses list/detail.

Extracted from test_parses.py:542-562 (TestUploaderDiscordId) and
test_parses.py:1223-1347 (uploader-identity resolution) per TEST-004 / Phase 2b.3.

The plugin stamps source_dsn as "plugin:<discord_id>" on every upload;
/api/parses and /api/parses/{id} should resolve that into a display name
(joined from users.discord_name) so the frontend can render the supporter
badge next to the uploader. Non-plugin uploads (source_dsn="eq2act" /
"local") carry None for both fields.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_require_user, make_fake_user
from tests.web._parses_fixtures import _FAKE_ENCOUNTER

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


class TestUploaderDiscordId:
    def test_plugin_prefix_returns_id(self):
        from web.routes.parses.list import _uploader_discord_id

        assert _uploader_discord_id("plugin:12345") == "12345"

    def test_eq2act_returns_none(self):
        from web.routes.parses.list import _uploader_discord_id

        assert _uploader_discord_id("eq2act") is None

    def test_empty_returns_none(self):
        from web.routes.parses.list import _uploader_discord_id

        assert _uploader_discord_id("") is None
        assert _uploader_discord_id(None) is None

    def test_plugin_with_no_id_returns_none(self):
        from web.routes.parses.list import _uploader_discord_id

        assert _uploader_discord_id("plugin:") is None


# ---------------------------------------------------------------------------
# Uploader Discord identity resolution (parses list + detail)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_parses_resolves_uploader_discord_identity(app):
    """A plugin-uploaded fight gets the uploader's discord_id + display
    name on the canonical encounter AND on its uploads[] entry. The
    display-name resolution is a single batched DB query — patched here
    to avoid touching the real users.db."""
    plugin_uploaded = dict(
        _FAKE_ENCOUNTER,
        source_dsn="plugin:discord-1234",
        combatant_count=2,
        player_count=1,
    )
    fake_list_sync = MagicMock(return_value=[plugin_uploaded])

    async def fake_resolve(ids):
        # Pin the contract: the route batches into one call with the
        # unique ID set.
        assert sorted(ids) == ["discord-1234"]
        return {"discord-1234": "Alice"}

    with (
        patch("web.routes.parses.list._require_user", _fake_user),
        patch("web.routes.parses.list._list_encounters_sync", fake_list_sync),
        patch(
            "web.routes.parses.list.users_db.get_display_names_for_discord_ids",
            fake_resolve,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")

    assert r.status_code == 200
    enc = r.json()["results"][0]
    assert enc["uploaded_by"] == "Menludiir"  # character name preserved
    assert enc["uploader_discord_id"] == "discord-1234"
    assert enc["uploader_display_name"] == "Alice"
    assert enc["uploads"][0]["uploader_discord_id"] == "discord-1234"
    assert enc["uploads"][0]["uploader_display_name"] == "Alice"


@pytest.mark.asyncio
async def test_list_parses_non_plugin_upload_has_null_uploader_identity(app):
    """source_dsn = 'eq2act' (the manual-import path) carries no Discord
    identity. Both new fields should be None — the frontend then falls
    back to just rendering the character name."""
    fake_list_sync = MagicMock(return_value=[dict(_FAKE_ENCOUNTER, combatant_count=2, player_count=1)])

    # Resolver gets called with an empty list (the route doesn't
    # pre-skip the call; get_display_names_for_discord_ids short-circuits
    # empty input internally). Track its argument so we can assert no
    # plugin IDs were ever resolved for a non-plugin upload.
    seen_ids: list[list[str]] = []

    async def fake_resolve(ids):
        seen_ids.append(list(ids))
        return {}

    with (
        patch("web.routes.parses.list._require_user", _fake_user),
        patch("web.routes.parses.list._list_encounters_sync", fake_list_sync),
        patch("web.routes.parses.list.users_db.get_display_names_for_discord_ids", fake_resolve),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")

    assert r.status_code == 200
    enc = r.json()["results"][0]
    assert enc["uploaded_by"] == "Menludiir"
    assert enc["uploader_discord_id"] is None
    assert enc["uploader_display_name"] is None
    # Resolver may have been called (with an empty list — see above) but
    # must never receive a plugin discord_id for this non-plugin upload.
    for call_ids in seen_ids:
        assert call_ids == [], f"resolver got unexpected ids for non-plugin upload: {call_ids}"


@pytest.mark.asyncio
async def test_list_parses_batches_unique_uploader_ids(app):
    """Two raiders uploading the same fight → resolver called ONCE with
    both unique IDs, not twice. Catches an N+1 regression."""
    base = 1716561116
    raider_a = dict(
        _FAKE_ENCOUNTER,
        id=1,
        uploaded_by="Menludiir",
        source_dsn="plugin:discord-A",
        started_at=base,
        combatant_count=2,
        player_count=1,
    )
    raider_b = dict(
        _FAKE_ENCOUNTER,
        id=2,
        uploaded_by="Sihtric",
        source_dsn="plugin:discord-B",
        started_at=base + 5,
        duration_s=50,
        combatant_count=2,
        player_count=1,
    )
    fake_list_sync = MagicMock(return_value=[raider_b, raider_a])

    call_count = 0

    async def fake_resolve(ids):
        nonlocal call_count
        call_count += 1
        return {did: f"Name-{did[-1]}" for did in ids}

    with (
        patch("web.routes.parses.list._require_user", _fake_user),
        patch("web.routes.parses.list._list_encounters_sync", fake_list_sync),
        patch("web.routes.parses.list.users_db.get_display_names_for_discord_ids", fake_resolve),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")

    assert r.status_code == 200
    assert call_count == 1, f"resolver should be called once, was {call_count}"
    enc = r.json()["results"][0]
    # Mirror grouping collapsed the two raiders; both their identities
    # are surfaced via uploads[].
    upload_ids = sorted(u["uploader_discord_id"] for u in enc["uploads"])
    assert upload_ids == ["discord-A", "discord-B"]
