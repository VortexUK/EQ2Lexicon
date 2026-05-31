"""Tests for the raid-strategies route — read, officer/admin-write, and the
(zone, position) → encounter resolution path."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import db as users_db

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
    """Override ``require_editor`` so the test acts as an authorised content
    editor — bypasses the admin/contributor/officer fanout and just returns a
    fixed user dict."""
    from backend.server.auth_deps import require_editor

    app.dependency_overrides[require_editor] = lambda: {"id": "admin-1", "username": "admin"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# GET /api/zones/{zone}/encounters/{position}/strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_strategy_unknown_zone_is_404(app):
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/Nowhere/encounters/1/strategy")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_strategy_unknown_position_is_404(app):
    """Zone exists but no encounter at that position."""
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/999/strategy")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_strategy_returns_existing_content(app):
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._read_strategy_sync",
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
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_strategy_sync", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/zones/{zone}/encounters/{position}/strategy  (officer-or-admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_strategy_unauthenticated_is_401(app):
    """No session at all → 401 (require_user_session inside require_editor)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/zones/The Emerald Halls/encounters/1/strategy",
            json={"markdown": "hello"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_put_strategy_session_but_no_role_is_403(app):
    """Signed in, but not admin, contributor, or officer → 403.

    ``require_editor`` calls ``require_user_session(request)`` directly (not
    via ``Depends``), so we patch the imported symbol on web.auth_deps where
    the dep now lives. The capability primitives are stubbed so the dep
    falls through to the dynamic officer branch, which finds no primary
    guild and 403s."""
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "rando-9", "username": "rando"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=True),
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
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
async def test_put_strategy_contributor_path_allows_write(app):
    """Non-admin contributor → request passes through to the write helper.

    Capability resolution: user_has_capability_via_db returns True (the
    JOIN'd contributor→edit_content row), so we short-circuit before the
    officer branch."""
    fresh_row = {
        "id": 1,
        "mob_name": "Prince Thirneg",
        "position": 1,
        "strategy_md": "contributor wrote this",
        "source": "manual",
        "last_edited_at": 1716300000,
        "last_edited_by": "contrib-7",
    }
    with (
        patch(
            "backend.server.auth_deps.require_user_session", return_value={"id": "contrib-7", "username": "contributor"}
        ),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=True
        ),
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._write_strategy_sync", return_value=fresh_row) as m_write,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "contributor wrote this"},
            )

    assert r.status_code == 200
    assert m_write.call_args.kwargs["editor_discord_id"] == "contrib-7"


@pytest.mark.asyncio
async def test_put_strategy_officer_path_allows_write(app):
    """Non-admin / non-contributor officer → request passes through.

    Officer is the most expensive branch (cache + Census fallback) so it's
    checked last in require_editor."""

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
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "officer-9", "username": "officer"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=True),
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={
                "approved": [{"character_name": "Sigarth", "is_primary": 1}],
                "pending": None,
            },
        ),
        patch("backend.server.core.primary_guild.character_cache.get_stale", return_value=(_Cached(), True)),
        patch("backend.server.api.guild._officer_chars", return_value={"sigarth"}),
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._write_strategy_sync", return_value=fresh_row) as m_write,
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
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "officer-9", "username": "officer"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=True),
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={
                "approved": [{"character_name": "Sigarth", "is_primary": 1}],
                "pending": None,
            },
        ),
        patch("backend.server.core.primary_guild.character_cache.get_stale", return_value=(None, False)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "blocked"},
            )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_strategy_rejects_empty_body(app):
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
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
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._write_strategy_sync", return_value=fresh_row) as m_write,
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
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/999/strategy/revisions")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_revisions_returns_empty_when_no_strategy_yet(app):
    """Encounter resolves but no strategy has been written yet → empty list,
    not 404. Lets the disclosure show "no history" cleanly."""
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_revisions_sync", return_value=[]),
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
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_revisions_sync", return_value=fake_revisions),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy/revisions")

    assert r.status_code == 200
    data = r.json()
    assert [rev["id"] for rev in data["revisions"]] == [3, 2, 1]
    assert data["revisions"][2]["before_md"] is None  # first revision
    assert data["revisions"][0]["edit_note"] == "tighten phase 2 wording"
    assert data["revisions"][1]["edit_note"] is None


# ---------------------------------------------------------------------------
# Zone-level overview — GET + PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_overview_unknown_zone_is_404(app):
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/Nowhere/overview")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_overview_no_content_is_404(app):
    """Zone exists but no overview written yet → 404 (cleanly mappable to the
    empty state on the frontend, same shape as the strategy GET)."""
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_overview_sync", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/overview")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_overview_returns_existing_content(app):
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._read_overview_sync",
            return_value={
                "zone_name": "The Emerald Halls",
                "overview_md": "## Strategy notes\n\nPull boss after adds.",
                "source": "manual",
                "last_edited_at": 1716000000,
                "last_edited_by": "admin-1",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/overview")

    assert r.status_code == 200
    data = r.json()
    assert data["zone_name"] == "The Emerald Halls"
    assert "Pull boss after adds" in data["markdown"]
    assert data["source"] == "manual"
    assert data["last_edited_by"] == "admin-1"


@pytest.mark.asyncio
async def test_put_overview_unauthenticated_is_401(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/zones/The Emerald Halls/overview",
            json={"markdown": "hello"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_put_overview_rejects_empty_body(app):
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "   "},
            )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_overview_writes_and_returns_row(app):
    """Writer auth passes → upsert helper called with the canonical zone +
    expansion_short, fresh row returned with editor stamped."""
    fresh_row = {
        "zone_name": "The Emerald Halls",
        "overview_md": "# new",
        "source": "manual",
        "last_edited_at": 1716200000,
        "last_edited_by": "admin-1",
    }
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._write_overview_sync", return_value=fresh_row) as m_write,
    ):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "# new"},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["markdown"] == "# new"
    assert data["last_edited_by"] == "admin-1"

    call_kwargs = m_write.call_args.kwargs
    assert call_kwargs["zone_name"] == "The Emerald Halls"
    assert call_kwargs["editor_discord_id"] == "admin-1"
    assert call_kwargs["expansion_short"] == "EoF"


@pytest.mark.asyncio
async def test_put_overview_unknown_zone_is_404(app):
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=None):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/Nowhere/overview",
                json={"markdown": "hello"},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_put_strategy_unknown_encounter_is_404(app):
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
    ):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/999/strategy",
                json={"markdown": "hello"},
            )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Editor name enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_revisions_enriches_edited_by_name_for_known_user(app):
    """When the editor's discord_id is in the users table, edited_by_name is
    populated on every RevisionEntry for that user."""
    # Seed the user into the test DB so get_display_names_for_discord_ids finds them.
    _path = users_db.DB_PATH
    await users_db.upsert_user(
        discord_id="admin-known",
        discord_name="Knowledgeable Admin",
        discord_username="kadmin",
        avatar=None,
        path=_path,
    )

    fake_revisions = [
        {
            "id": 1,
            "encounter_id": 1,
            "edited_at": 1716000000,
            "edited_by": "admin-known",
            "before_md": None,
            "after_md": "v1",
            "edit_note": "initial",
        },
    ]
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_revisions_sync", return_value=fake_revisions),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy/revisions")

    assert r.status_code == 200
    revs = r.json()["revisions"]
    assert len(revs) == 1
    assert revs[0]["edited_by"] == "admin-known"
    assert revs[0]["edited_by_name"] == "Knowledgeable Admin"


@pytest.mark.asyncio
async def test_get_revisions_edited_by_name_none_for_eq2i_scrape(app):
    """'eq2i_scrape' is a source token, not a discord_id.
    edited_by_name must be None — the frontend fmtEditor handles the label."""
    fake_revisions = [
        {
            "id": 1,
            "encounter_id": 1,
            "edited_at": 1716000000,
            "edited_by": "eq2i_scrape",
            "before_md": None,
            "after_md": "scraped content",
            "edit_note": None,
        },
    ]
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_revisions_sync", return_value=fake_revisions),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy/revisions")

    assert r.status_code == 200
    revs = r.json()["revisions"]
    assert revs[0]["edited_by"] == "eq2i_scrape"
    assert revs[0]["edited_by_name"] is None


@pytest.mark.asyncio
async def test_get_revisions_edited_by_name_none_for_unknown_id(app):
    """An id that isn't in the users table → edited_by_name stays None."""
    fake_revisions = [
        {
            "id": 1,
            "encounter_id": 1,
            "edited_at": 1716000000,
            "edited_by": "9999999999999999999",
            "before_md": None,
            "after_md": "mystery edit",
            "edit_note": None,
        },
    ]
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._read_revisions_sync", return_value=fake_revisions),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy/revisions")

    assert r.status_code == 200
    revs = r.json()["revisions"]
    assert revs[0]["edited_by_name"] is None


@pytest.mark.asyncio
async def test_get_strategy_response_includes_last_edited_by_name(app):
    """StrategyResponse now carries last_edited_by_name resolved from users."""
    _path = users_db.DB_PATH
    await users_db.upsert_user(
        discord_id="editor-555",
        discord_name="Templar Guildmaster",
        discord_username="tguild",
        avatar=None,
        path=_path,
    )
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._read_strategy_sync",
            return_value={
                "id": 1,
                "mob_name": "Prince Thirneg",
                "position": 1,
                "strategy_md": "## Tactics\n\nDPS race.",
                "source": "manual",
                "last_edited_at": 1716000000,
                "last_edited_by": "editor-555",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy")

    assert r.status_code == 200
    data = r.json()
    assert data["last_edited_by"] == "editor-555"
    assert data["last_edited_by_name"] == "Templar Guildmaster"


@pytest.mark.asyncio
async def test_get_strategy_last_edited_by_name_none_for_scrape(app):
    """'eq2i_scrape' in last_edited_by → last_edited_by_name is None."""
    with (
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._read_strategy_sync",
            return_value={
                "id": 1,
                "mob_name": "Prince Thirneg",
                "position": 1,
                "strategy_md": "scraped content",
                "source": "scrape",
                "last_edited_at": 1716000000,
                "last_edited_by": "eq2i_scrape",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/strategy")

    assert r.status_code == 200
    data = r.json()
    assert data["last_edited_by"] == "eq2i_scrape"
    assert data["last_edited_by_name"] is None
