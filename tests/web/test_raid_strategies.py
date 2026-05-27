"""Tests for the raid-strategies route — read, officer/admin-write, and the
(zone, position) → encounter resolution path."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _fake_zone() -> dict:
    """Hydrated zone shape that ``zones_db.find_by_name`` would return — enough
    fields for the strategy route to resolve (zone, position) → encounter."""
    return {
        "name": "The Emerald Halls",
        "expansion_short": "EoF",
        "expansion_name": "Echoes of Faydwer",
        "bosses": [
            {"encounter_name": "Prince Thirneg", "position": 1, "stage": "First Floor"},
            {"encounter_name": "Wuoshi", "position": 13, "stage": "Third Floor"},
        ],
    }


def _writer_client(app):
    """Override ``require_officer_or_admin`` so the test acts as an authorised
    strategy writer (admin or officer — the dep treats them equivalently)."""
    from web.routes.raid_strategies import require_officer_or_admin

    app.dependency_overrides[require_officer_or_admin] = lambda: {"id": "admin-1", "username": "admin"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# GET /api/zones/{zone}/encounters/{position}/strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_strategy_unknown_zone_is_404(app):
    with patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/Nowhere/encounters/1/strategy")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_strategy_unknown_position_is_404(app):
    """Zone exists but no encounter at that position."""
    with patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/999/strategy")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_strategy_returns_existing_content(app):
    with (
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "web.routes.raid_strategies._read_strategy_sync",
            return_value={
                "id": 1,
                "mob_name": "Prince Thirneg",
                "position": 1,
                "strategy_md": "## Tactics\n\nTank-and-spank.",
                "source": "manual",
                "last_edited_at": 1716000000,
                "last_edited_by": "admin-1",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy")

    assert r.status_code == 200
    data = r.json()
    assert data["zone_name"] == "The Emerald Halls"
    assert data["encounter_name"] == "Prince Thirneg"
    assert data["position"] == 1
    assert "Tank-and-spank" in data["markdown"]
    assert data["source"] == "manual"
    assert data["last_edited_by"] == "admin-1"


@pytest.mark.asyncio
async def test_get_strategy_no_content_yet_is_404(app):
    """Encounter resolves but no strategy row exists yet — 404 lets the
    frontend fall back to the placeholder cleanly."""
    with (
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("web.routes.raid_strategies._read_strategy_sync", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/zones/{zone}/encounters/{position}/strategy  (officer-or-admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_strategy_unauthenticated_is_401(app):
    """No session at all → 401 (require_user_session inside require_officer_or_admin)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/zones/The Emerald Halls/encounters/1/strategy",
            json={"markdown": "hello"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_put_strategy_session_but_not_admin_or_officer_is_403(app):
    """Signed in, but not an admin and not an officer → 403.

    ``require_officer_or_admin`` calls ``require_user_session(request)``
    directly (not via ``Depends``), so we patch the imported symbol rather
    than overriding through ``app.dependency_overrides``."""
    with (
        patch("web.routes.raid_strategies.require_user_session", return_value={"id": "rando-9", "username": "rando"}),
        patch("web.routes.raid_strategies.is_admin", return_value=False),
        patch(
            "web.routes.raid_strategies.get_active_claims",
            return_value={"approved": [], "pending": None},
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "hello"},
            )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_strategy_officer_path_allows_write(app):
    """Non-admin officer → request passes through to the write helper."""

    class _Cached:
        guild_name = "Exordium"

    fresh_row = {
        "id": 1,
        "mob_name": "Prince Thirneg",
        "position": 1,
        "strategy_md": "officer wrote this",
        "source": "manual",
        "last_edited_at": 1716200000,
        "last_edited_by": "officer-9",
    }

    with (
        patch(
            "web.routes.raid_strategies.require_user_session", return_value={"id": "officer-9", "username": "officer"}
        ),
        patch("web.routes.raid_strategies.is_admin", return_value=False),
        patch(
            "web.routes.raid_strategies.get_active_claims",
            return_value={
                "approved": [{"character_name": "Sigarth", "is_primary": 1}],
                "pending": None,
            },
        ),
        patch("web.routes.raid_strategies.character_cache.get_stale", return_value=(_Cached(), True)),
        patch("web.routes.raid_strategies._officer_chars", return_value={"sigarth"}),
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("web.routes.raid_strategies._write_strategy_sync", return_value=fresh_row) as m_write,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "officer wrote this"},
            )

    assert r.status_code == 200
    assert m_write.call_args.kwargs["editor_discord_id"] == "officer-9"
    assert r.json()["markdown"] == "officer wrote this"


@pytest.mark.asyncio
async def test_put_strategy_officer_path_403_on_cold_cache(app):
    """Primary character set but their character_cache entry is cold → 403.

    Fail-closed is deliberate: an empty cache shouldn't silently widen who
    can write. Visiting /character/<name> once warms it."""
    with (
        patch(
            "web.routes.raid_strategies.require_user_session", return_value={"id": "officer-9", "username": "officer"}
        ),
        patch("web.routes.raid_strategies.is_admin", return_value=False),
        patch(
            "web.routes.raid_strategies.get_active_claims",
            return_value={
                "approved": [{"character_name": "Sigarth", "is_primary": 1}],
                "pending": None,
            },
        ),
        patch("web.routes.raid_strategies.character_cache.get_stale", return_value=(None, False)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "blocked"},
            )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_strategy_rejects_empty_body(app):
    with patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "   "},
            )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_strategy_writes_and_returns_row(app):
    """Admin writes strategy → upsert helper called with the right args, fresh
    row returned including last_edited_by stamp."""
    fresh_row = {
        "id": 1,
        "mob_name": "Prince Thirneg",
        "position": 1,
        "strategy_md": "# new",
        "source": "manual",
        "last_edited_at": 1716200000,
        "last_edited_by": "admin-1",
    }
    with (
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("web.routes.raid_strategies._write_strategy_sync", return_value=fresh_row) as m_write,
    ):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "# new", "edit_note": "first cut"},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["markdown"] == "# new"
    assert data["last_edited_by"] == "admin-1"
    assert data["source"] == "manual"

    # The write helper got the canonical zone + curator encounter_name,
    # not whatever the URL had (proves the zone-resolution step ran).
    call_kwargs = m_write.call_args.kwargs
    assert call_kwargs["zone_name"] == "The Emerald Halls"
    assert call_kwargs["encounter_name"] == "Prince Thirneg"
    assert call_kwargs["position"] == 1
    assert call_kwargs["editor_discord_id"] == "admin-1"
    assert call_kwargs["edit_note"] == "first cut"
    assert call_kwargs["expansion_short"] == "EoF"


# ---------------------------------------------------------------------------
# GET /api/zones/{zone}/encounters/{position}/strategy/revisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_revisions_unknown_encounter_is_404(app):
    with patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/999/strategy/revisions")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_revisions_returns_empty_when_no_strategy_yet(app):
    """Encounter resolves but no strategy has been written yet → empty list,
    not 404. Lets the disclosure show "no history" cleanly."""
    with (
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("web.routes.raid_strategies._read_revisions_sync", return_value=[]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy/revisions")
    assert r.status_code == 200
    data = r.json()
    assert data["zone_name"] == "The Emerald Halls"
    assert data["encounter_name"] == "Prince Thirneg"
    assert data["revisions"] == []


@pytest.mark.asyncio
async def test_get_revisions_returns_newest_first(app):
    """Helper returns rows newest-first (per raids_db.encounter_revisions);
    the route preserves that order and surfaces all six fields."""
    fake_revisions = [
        {
            "id": 3,
            "encounter_id": 1,
            "edited_at": 1716200000,
            "edited_by": "admin-1",
            "before_md": "v2",
            "after_md": "v3",
            "edit_note": "tighten phase 2 wording",
        },
        {
            "id": 2,
            "encounter_id": 1,
            "edited_at": 1716100000,
            "edited_by": "admin-1",
            "before_md": "v1",
            "after_md": "v2",
            "edit_note": None,
        },
        {
            "id": 1,
            "encounter_id": 1,
            "edited_at": 1716000000,
            "edited_by": "admin-1",
            "before_md": None,
            "after_md": "v1",
            "edit_note": "initial",
        },
    ]
    with (
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("web.routes.raid_strategies._read_revisions_sync", return_value=fake_revisions),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy/revisions")

    assert r.status_code == 200
    data = r.json()
    assert [rev["id"] for rev in data["revisions"]] == [3, 2, 1]
    assert data["revisions"][2]["before_md"] is None  # first revision
    assert data["revisions"][0]["edit_note"] == "tighten phase 2 wording"
    assert data["revisions"][1]["edit_note"] is None


@pytest.mark.asyncio
async def test_put_strategy_unknown_encounter_is_404(app):
    with (
        patch("web.routes.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
    ):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/999/strategy",
                json={"markdown": "hello"},
            )
    assert r.status_code == 404
