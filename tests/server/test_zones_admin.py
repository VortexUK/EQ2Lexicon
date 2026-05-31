"""Tests for the editor-gated write endpoints on /api/zones/{zone}/encounters."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.auth_deps import require_editor


@pytest.fixture
def editor_override(app):
    """Bypass require_editor by returning a fake session."""
    app.dependency_overrides[require_editor] = lambda: {"id": "admin-1", "username": "admin", "is_admin": True}
    yield
    app.dependency_overrides.pop(require_editor, None)


@pytest.mark.asyncio
async def test_create_encounter_requires_editor(app):
    """No session → 401/403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/zones/Shard of Hate/encounters",
            json={"primary_mob": "Hackerman"},
        )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_encounter_happy_path(app, editor_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.find_by_name",
            return_value={"id": 12, "name": "Shard of Hate"},
        ),
        patch(
            "backend.server.api.zones_admin.zones_db.add_encounter",
            return_value={
                "id": 99,
                "zone_id": 12,
                "encounter_name": "Newboss",
                "position": 7,
                "stage": None,
                "wiki_url": None,
                "mobs": [{"mob_name": "Newboss", "position": 0}],
            },
        ) as add_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Shard of Hate/encounters",
                json={"primary_mob": "Newboss"},
            )
    assert r.status_code == 200
    assert r.json()["encounter_name"] == "Newboss"
    add_mock.assert_called_once()
    kwargs = add_mock.call_args.kwargs
    assert kwargs["zone_id"] == 12
    assert kwargs["primary_mob"] == "Newboss"


@pytest.mark.asyncio
async def test_create_encounter_unknown_zone_404(app, editor_override):
    with patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Imaginary/encounters",
                json={"primary_mob": "Nobody"},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_edit_encounter_omitted_fields_not_passed(app, editor_override):
    """Client omits stage + wiki_url; route must NOT pass them to update_encounter
    (so the sentinel default kicks in and the columns stay untouched)."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.find_by_name",
            return_value={"id": 1, "name": "Z"},
        ),
        patch(
            "backend.server.api.zones_admin.zones_db.update_encounter",
            return_value={
                "id": 5,
                "zone_id": 1,
                "encounter_name": "Renamed",
                "position": 1,
                "stage": "kept",
                "wiki_url": "kept",
                "mobs": [{"mob_name": "Renamed", "position": 0}],
            },
        ) as upd_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/Z/encounters/5",
                json={"primary_mob": "Renamed"},  # stage + wiki_url omitted
            )
    assert r.status_code == 200
    kwargs = upd_mock.call_args.kwargs
    assert "stage" not in kwargs
    assert "wiki_url" not in kwargs
    assert kwargs.get("primary_mob") == "Renamed"


@pytest.mark.asyncio
async def test_edit_encounter_explicit_null_clears(app, editor_override):
    """Client sends stage=null → route must pass stage=None to update_encounter
    (the sentinel-default machinery interprets None as 'clear it')."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.find_by_name",
            return_value={"id": 1, "name": "Z"},
        ),
        patch(
            "backend.server.api.zones_admin.zones_db.update_encounter",
            return_value={
                "id": 5,
                "zone_id": 1,
                "encounter_name": "X",
                "position": 1,
                "stage": None,
                "wiki_url": None,
                "mobs": [{"mob_name": "X", "position": 0}],
            },
        ) as upd_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/Z/encounters/5",
                json={"stage": None, "wiki_url": None},
            )
    assert r.status_code == 200
    kwargs = upd_mock.call_args.kwargs
    assert kwargs.get("stage") is None
    assert kwargs.get("wiki_url") is None
    assert "primary_mob" not in kwargs


@pytest.mark.asyncio
async def test_delete_encounter_404_when_missing(app, editor_override):
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch("backend.server.api.zones_admin.zones_db.delete_encounter", return_value=False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Z/encounters/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reorder_encounters_400_on_bad_permutation(app, editor_override):
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.reorder_encounters",
            side_effect=ValueError("not a permutation"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/Z/encounters/reorder",
                json={"ordered_encounter_ids": [1, 2]},
            )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_add_mob_happy_path(app, editor_override):
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.add_mob",
            return_value={"id": 77, "mob_name": "Sib", "position": 1},
        ) as add_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Z/encounters/5/mobs",
                json={"mob_name": "Sib"},
            )
    assert r.status_code == 200
    assert r.json()["position"] == 1
    add_mock.assert_called_once()
    kwargs = add_mock.call_args.kwargs
    assert kwargs["mob_name"] == "Sib"
    assert kwargs["make_primary"] is False


@pytest.mark.asyncio
async def test_delete_mob_422_on_invariant_violation(app, editor_override):
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.delete_mob",
            side_effect=ValueError("cannot delete the last mob of an encounter"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Z/encounters/5/mobs/77")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_promote_mob_route(app, editor_override):
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.promote_mob",
            return_value={"id": 77, "mob_name": "X", "position": 0},
        ) as prom_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/zones/Z/encounters/5/mobs/77/promote")
    assert r.status_code == 200
    assert r.json()["position"] == 0
    prom_mock.assert_called_once_with(77)
