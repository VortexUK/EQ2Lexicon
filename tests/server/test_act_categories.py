"""Tests for /api/act/categories — contributor-defined trigger categories.

Categories are synthetic encounters under the "General" raid zone, so the
existing per-encounter trigger routes address them via
/api/zones/General/encounters/{position}/... — covered here end-to-end
against a real tmp raids.db (no resolution mocking: the General branch in
_resolve_encounter_sync bypasses zones_db entirely)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def raids_tmp(tmp_path, monkeypatch):
    """Point the raids catalogue at a fresh tmp DB for each test."""
    from backend.eq2db.raids import catalogue
    from backend.server.api.act import _shared

    db_file = tmp_path / "raids.db"
    monkeypatch.setattr(catalogue, "path", db_file)
    # The module-level "already inited" flag would skip schema creation on
    # the fresh file.
    monkeypatch.setattr(_shared, "_RAIDS_DB_INIT_DONE", False)
    return db_file


def _writer_client(app):
    from backend.server.auth_deps import require_editor

    app.dependency_overrides[require_editor] = lambda: {
        "id": "admin-1",
        "username": "admin",
    }
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_create_requires_editor(app, raids_tmp):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/act/categories", json={"name": "Death Saves"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_category_lifecycle(app, raids_tmp):
    async with _writer_client(app) as client:
        # Empty at first.
        r = await client.get("/api/act/categories")
        assert r.status_code == 200
        assert r.json() == []

        # Create.
        r = await client.post("/api/act/categories", json={"name": "Death Saves"})
        assert r.status_code == 201
        cat = r.json()
        assert cat["name"] == "Death Saves"
        position = cat["position"]

        # Duplicate (case-insensitive) → 409.
        r = await client.post("/api/act/categories", json={"name": "death saves"})
        assert r.status_code == 409

        # Listed with zero counts — empty categories MUST appear here
        # (the pack omits them; this endpoint is the management view).
        r = await client.get("/api/act/categories")
        assert [c["name"] for c in r.json()] == ["Death Saves"]
        assert r.json()[0]["trigger_count"] == 0

        # The existing per-encounter routes address it as zone "General".
        base = f"/api/zones/General/encounters/{position}"
        r = await client.post(
            f"{base}/triggers",
            json={"regex": "protects your life", "sound_data": "death save", "sound_type": 3},
        )
        assert r.status_code == 201, r.text
        trigger_id = r.json()["id"]

        r = await client.get(f"{base}/triggers")
        assert [t["id"] for t in r.json()] == [trigger_id]

        # Counts update.
        r = await client.get("/api/act/categories")
        assert r.json()[0]["trigger_count"] == 1

        # The app pack ships the category as a General-zone encounter.
        r = await client.get("/api/act/pack")
        pack = r.json()
        general = next(z for z in pack["zones"] if z["zone"] == "General")
        assert general["encounters"][0]["mob"] == "Death Saves"
        assert len(general["encounters"][0]["triggers"]) == 1

        # Rename.
        r = await client.put(f"/api/act/categories/{position}", json={"name": "Death Prevents"})
        assert r.status_code == 200
        r = await client.get("/api/act/categories")
        assert r.json()[0]["name"] == "Death Prevents"

        # Delete cascades to the trigger.
        r = await client.delete(f"/api/act/categories/{position}")
        assert r.status_code == 200
        r = await client.get("/api/act/categories")
        assert r.json() == []
        r = await client.get(f"{base}/triggers")
        assert r.status_code == 404  # category gone → resolution fails


@pytest.mark.asyncio
async def test_empty_category_not_in_pack(app, raids_tmp):
    async with _writer_client(app) as client:
        r = await client.post("/api/act/categories", json={"name": "Cures"})
        assert r.status_code == 201
        r = await client.get("/api/act/pack")
        assert all(z["zone"] != "General" for z in r.json()["zones"])


@pytest.mark.asyncio
async def test_rename_collision_is_409(app, raids_tmp):
    async with _writer_client(app) as client:
        await client.post("/api/act/categories", json={"name": "Cures"})
        r = await client.post("/api/act/categories", json={"name": "Death Saves"})
        position = r.json()["position"]
        r = await client.put(f"/api/act/categories/{position}", json={"name": "CURES"})
        assert r.status_code == 409
