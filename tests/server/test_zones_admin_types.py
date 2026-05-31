"""Tests for the editor-gated zone-type tag endpoints (used by the
Dungeons curation UI on /raids).

Endpoints covered:
  - POST   /api/zones/{zone_name}/types        body: {"type": "dungeon"}
  - DELETE /api/zones/{zone_name}/types/{type}

Mirrors the patching style of tests/web/test_zones_admin.py — every
zones_db call is mocked so the tests are pure route/auth/validation
exercises, independent of any on-disk SQLite DB."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.auth_deps import require_editor

# A representative hydrated-zone shape — matches what zones_db.find_by_name
# / add_zone_type / remove_zone_type return.
_ZONE = {
    "id": 1,
    "name": "Crushbone Keep",
    "name_lower": "crushbone keep",
    "expansion_short": "RoK",
    "expansion_name": "Rise of Kunark",
    "types": ["dungeon"],
    "aliases": [],
    "bosses": [],
}


@pytest.fixture
def editor_override(app):
    """Bypass require_editor by returning a fake admin session."""
    app.dependency_overrides[require_editor] = lambda: {
        "id": "admin-1",
        "username": "admin",
        "is_admin": True,
    }
    yield
    app.dependency_overrides.pop(require_editor, None)


# ── Auth gating ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_type_requires_editor(app):
    """No session → 401/403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/zones/Crushbone Keep/types", json={"type": "dungeon"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_remove_type_requires_editor(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/zones/Crushbone Keep/types/dungeon")
    assert r.status_code in (401, 403)


# ── POST happy path + idempotency + invalidate hook ────────────────────────────


@pytest.mark.asyncio
async def test_add_type_happy_path(app, editor_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_zone_type",
            return_value=_ZONE,
        ) as add_mock,
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ) as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Crushbone Keep/types",
                json={"type": "dungeon"},
            )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Crushbone Keep"
    assert "dungeon" in body["types"]
    add_mock.assert_called_once_with("Crushbone Keep", "dungeon")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_add_type_idempotent_at_helper_level(app, editor_override):
    """The helper does INSERT OR IGNORE, so calling POST twice returns the
    same hydrated zone with no error. We verify the route forwards both
    calls and returns 200 either time."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_zone_type",
            return_value=_ZONE,
        ) as add_mock,
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.post(
                "/api/zones/Crushbone Keep/types",
                json={"type": "dungeon"},
            )
            r2 = await client.post(
                "/api/zones/Crushbone Keep/types",
                json={"type": "dungeon"},
            )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert add_mock.call_count == 2


@pytest.mark.asyncio
async def test_add_type_unknown_zone_404(app, editor_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_zone_type",
            return_value=None,
        ),
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ) as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Imaginary/types",
                json={"type": "dungeon"},
            )
    assert r.status_code == 404
    # Cache invalidation MUST NOT fire when the mutation didn't happen.
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_add_type_not_allowlisted_400(app, editor_override):
    """A non-allowlisted token (e.g. 'raid_x4') must be rejected at the route
    layer, BEFORE the helper is called. Important so contributors can't
    accidentally promote a zone into the raid index."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_zone_type",
        ) as add_mock,
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ) as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Crushbone Keep/types",
                json={"type": "raid_x4"},
            )
    assert r.status_code == 400
    add_mock.assert_not_called()
    invalidate_mock.assert_not_called()


# ── DELETE happy path + idempotency + 404 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_type_happy_path(app, editor_override):
    no_dungeon = {**_ZONE, "types": []}
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_zone_type",
            return_value=no_dungeon,
        ) as rm_mock,
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ) as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Crushbone Keep/types/dungeon")
    assert r.status_code == 200
    assert "dungeon" not in r.json()["types"]
    rm_mock.assert_called_once_with("Crushbone Keep", "dungeon")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_remove_type_idempotent_when_already_absent(app, editor_override):
    """When the tag isn't on the zone, the helper still returns the hydrated
    zone (with no error). The route returns 200 — it's a no-op."""
    no_dungeon = {**_ZONE, "types": []}
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_zone_type",
            return_value=no_dungeon,
        ),
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Crushbone Keep/types/dungeon")
    assert r.status_code == 200
    assert "dungeon" not in r.json()["types"]


@pytest.mark.asyncio
async def test_remove_type_unknown_zone_404(app, editor_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_zone_type",
            return_value=None,
        ),
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ) as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Imaginary/types/dungeon")
    assert r.status_code == 404
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_remove_type_not_allowlisted_400(app, editor_override):
    """Path-based type token must also pass the allowlist gate — otherwise
    a contributor could DELETE the raid_x4 tag from any raid zone."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_zone_type",
        ) as rm_mock,
        patch(
            "backend.server.api.zones_admin.invalidate_zones_cache",
        ) as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Crushbone Keep/types/raid_x4")
    assert r.status_code == 400
    rm_mock.assert_not_called()
    invalidate_mock.assert_not_called()


# ── Integration with the real zones_db helpers (no SQL mocks) ─────────────────


def test_add_then_remove_roundtrip_via_real_helpers(tmp_path):
    """Exercise the real add_zone_type / remove_zone_type helpers against a
    throwaway zones.db. Catches schema drift or SQL typos that the
    route-level mock tests above would miss. No HTTP layer — just the
    SQLite helpers — so we sidestep the run_sync ↔ mock-recursion issue
    and keep this test focused on the storage contract."""
    from backend.eq2db import zones as zones_db

    db_path = tmp_path / "zones.db"
    conn = zones_db.init_db(db_path)
    try:
        conn.execute(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence) VALUES (1, 'Crushbone Keep', 'crushbone keep', 'RoK', "
            "'Rise of Kunark', 'category')"
        )
        conn.commit()
    finally:
        conn.close()

    # Add
    r1 = zones_db.add_zone_type("Crushbone Keep", "dungeon", path=db_path)
    assert r1 is not None
    assert "dungeon" in r1["types"]

    # Idempotent re-add — no duplicate rows.
    r2 = zones_db.add_zone_type("Crushbone Keep", "dungeon", path=db_path)
    assert r2 is not None
    assert r2["types"].count("dungeon") == 1

    # Unknown zone → None (route layer turns this into a 404).
    assert zones_db.add_zone_type("Imaginary Zone", "dungeon", path=db_path) is None

    # Remove
    r3 = zones_db.remove_zone_type("Crushbone Keep", "dungeon", path=db_path)
    assert r3 is not None
    assert "dungeon" not in r3["types"]

    # Idempotent re-remove (no-op).
    r4 = zones_db.remove_zone_type("Crushbone Keep", "dungeon", path=db_path)
    assert r4 is not None
    assert "dungeon" not in r4["types"]

    # Unknown zone → None
    assert zones_db.remove_zone_type("Imaginary Zone", "dungeon", path=db_path) is None
