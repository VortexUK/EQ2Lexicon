"""Roster mutations invalidate the _cached_zones_data cache.

Regression test for the user-reported issue: adding bosses via the editor
didn't show up in /api/rankings/filters until the process restarted.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.rankings import _cached_zones_data, invalidate_zones_cache
from backend.server.auth_deps import require_editor

# ---------------------------------------------------------------------------
# Unit test — helper contract
# ---------------------------------------------------------------------------


def test_invalidate_zones_cache_clears_lru_cache() -> None:
    # Prime the cache by calling once (may return empty dict if zones.db absent)
    _cached_zones_data()
    info_before = _cached_zones_data.cache_info()
    assert info_before.currsize >= 0  # cached or not, the call worked

    # Call again — should hit cache
    _cached_zones_data()
    info_after_second_call = _cached_zones_data.cache_info()
    assert info_after_second_call.hits > info_before.hits

    # Invalidate
    invalidate_zones_cache()

    info_after_clear = _cached_zones_data.cache_info()
    assert info_after_clear.currsize == 0


# ---------------------------------------------------------------------------
# Integration tests — each mutation endpoint calls invalidate_zones_cache
# ---------------------------------------------------------------------------


@pytest.fixture
def editor_override(app):
    """Bypass require_editor by returning a fake session."""
    app.dependency_overrides[require_editor] = lambda: {"id": "admin-1", "username": "admin", "is_admin": True}
    yield
    app.dependency_overrides.pop(require_editor, None)


@pytest.mark.asyncio
async def test_create_encounter_invalidates_cache(app, editor_override) -> None:
    """POST /zones/.../encounters clears the zones cache on success."""
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
        ),
    ):
        # Prime the cache so currsize is 1 going in
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/zones/Shard of Hate/encounters",
                json={"primary_mob": "Newboss"},
            )
    assert r.status_code == 200
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_reorder_encounters_invalidates_cache(app, editor_override) -> None:
    """PUT /zones/.../encounters/reorder clears the zones cache on success."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.find_by_name",
            return_value={"id": 1, "name": "Z", "bosses": []},
        ),
        patch("backend.server.api.zones_admin.zones_db.reorder_encounters"),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/Z/encounters/reorder",
                json={"ordered_encounter_ids": [1, 2]},
            )
    assert r.status_code == 200
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_edit_encounter_invalidates_cache(app, editor_override) -> None:
    """PUT /zones/.../encounters/{id} clears the zones cache on success."""
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
                "stage": None,
                "wiki_url": None,
                "mobs": [{"mob_name": "Renamed", "position": 0}],
            },
        ),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put("/api/zones/Z/encounters/5", json={"primary_mob": "Renamed"})
    assert r.status_code == 200
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_delete_encounter_invalidates_cache(app, editor_override) -> None:
    """DELETE /zones/.../encounters/{id} clears the zones cache on success."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch("backend.server.api.zones_admin.zones_db.delete_encounter", return_value=True),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Z/encounters/5")
    assert r.status_code == 204
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_delete_encounter_no_invalidation_on_404(app, editor_override) -> None:
    """DELETE that 404s does NOT clear the cache (nothing changed)."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch("backend.server.api.zones_admin.zones_db.delete_encounter", return_value=False),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Z/encounters/9999")
    assert r.status_code == 404
    # Cache must NOT have been cleared — the encounter didn't exist
    assert _cached_zones_data.cache_info().currsize == 1


@pytest.mark.asyncio
async def test_create_mob_invalidates_cache(app, editor_override) -> None:
    """POST /zones/.../encounters/{id}/mobs clears the zones cache on success."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.add_mob",
            return_value={"id": 77, "mob_name": "Sib", "position": 1},
        ),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/zones/Z/encounters/5/mobs", json={"mob_name": "Sib"})
    assert r.status_code == 200
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_edit_mob_invalidates_cache(app, editor_override) -> None:
    """PUT /zones/.../encounters/{id}/mobs/{mob_id} clears the cache on success."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.update_mob",
            return_value={"id": 77, "mob_name": "Renamed", "position": 1},
        ),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put("/api/zones/Z/encounters/5/mobs/77", json={"mob_name": "Renamed"})
    assert r.status_code == 200
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_promote_mob_invalidates_cache(app, editor_override) -> None:
    """POST /zones/.../encounters/{id}/mobs/{mob_id}/promote clears the cache."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch(
            "backend.server.api.zones_admin.zones_db.promote_mob",
            return_value={"id": 77, "mob_name": "X", "position": 0},
        ),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/zones/Z/encounters/5/mobs/77/promote")
    assert r.status_code == 200
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_delete_mob_invalidates_cache(app, editor_override) -> None:
    """DELETE /zones/.../encounters/{id}/mobs/{mob_id} clears the cache on success."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch("backend.server.api.zones_admin.zones_db.delete_mob", return_value=True),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Z/encounters/5/mobs/77")
    assert r.status_code == 204
    assert _cached_zones_data.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_delete_mob_no_invalidation_on_404(app, editor_override) -> None:
    """DELETE mob that 404s does NOT clear the cache (nothing changed)."""
    with (
        patch("backend.server.api.zones_admin.zones_db.find_by_name", return_value={"id": 1, "name": "Z"}),
        patch("backend.server.api.zones_admin.zones_db.delete_mob", return_value=False),
    ):
        _cached_zones_data()
        assert _cached_zones_data.cache_info().currsize == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/zones/Z/encounters/5/mobs/9999")
    assert r.status_code == 404
    # Cache must NOT have been cleared — the mob didn't exist
    assert _cached_zones_data.cache_info().currsize == 1


# ---------------------------------------------------------------------------
# Phase 5 — invalidate_zones_cache also nukes combatant is_player
# ---------------------------------------------------------------------------

import sqlite3
from pathlib import Path

from backend.server.parses import db as parses_db


@pytest.fixture
def parses_db_in_memory(monkeypatch):
    """Shared in-memory parses DB for the duration of the test.

    Patches invalidate_is_player_cache so it operates on the in-memory
    connection rather than opening a new connection to DB_PATH."""
    conn = parses_db.init_db(Path(":memory:"))
    monkeypatch.setattr(parses_db, "DB_PATH", Path(":memory:"))
    monkeypatch.setattr(parses_db, "init_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        parses_db,
        "invalidate_is_player_cache",
        lambda *a, **k: parses_db.invalidate_is_player_cache_with_conn(conn),
    )
    try:
        yield conn
    finally:
        conn.close()


def test_invalidate_zones_cache_nukes_combatant_is_player(parses_db_in_memory):
    from backend.server.api.rankings import invalidate_zones_cache

    cur = parses_db_in_memory.execute(
        """
        INSERT INTO encounters (
            act_encid, title, zone, started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, source_dsn, ingested_at, world
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("invZ", "Test", "Z", 1, 2, 1, 100, 100.0, 0, 0, "test", 1, "Varsoon"),
    )
    enc_id = int(cur.lastrowid or 0)
    parses_db_in_memory.execute(
        "INSERT INTO combatants (encounter_id, name, ally, is_player) VALUES (?, ?, ?, ?)",
        (enc_id, "Alpha", 1, 1),
    )
    parses_db_in_memory.execute(
        "INSERT INTO combatants (encounter_id, name, ally, is_player) VALUES (?, ?, ?, ?)",
        (enc_id, "Bravo", 1, 0),
    )
    parses_db_in_memory.commit()

    invalidate_zones_cache()

    rows = parses_db_in_memory.execute("SELECT is_player FROM combatants").fetchall()
    assert all(r[0] is None for r in rows), "every is_player must be NULL after invalidate_zones_cache"
