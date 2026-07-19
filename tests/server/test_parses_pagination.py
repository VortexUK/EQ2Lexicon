"""Tests for the /api/parses + admin-list pagination cursor (``before``).

The list endpoints serve a newest-first window; without a cursor, growth in
upload volume silently pushes older history out of view (the "several weeks
of bosses disappeared" incident of 2026-07 — nothing was deleted, the
newest-500-fights window just no longer reached past three days). These
tests pin the cursor contract: ``next_before`` handed out exactly when more
data exists, ``before`` filtering strictly older rows.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.parses import db as parses_db_mod
from tests.fixtures.users import make_fake_require_user, make_fake_user
from tests.server._parses_fixtures import _FAKE_ENCOUNTER

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


def _fight(enc_id: int, title: str, started_at: int) -> dict:
    """A standalone fight (distinct title → never mirror-grouped)."""
    return dict(
        _FAKE_ENCOUNTER,
        id=enc_id,
        act_encid=f"enc{enc_id}",
        title=title,
        started_at=started_at,
        ended_at=started_at + 60,
        combatant_count=2,
        player_count=1,
    )


# ---------------------------------------------------------------------------
# Endpoint: next_before cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_page_hands_out_next_before(app):
    """More fights than `limit` → next_before = oldest returned fight."""
    fights = [_fight(1, "Boss One", 3000), _fight(2, "Boss Two", 2000), _fight(3, "Boss Three", 1000)]
    fake_list_sync = MagicMock(return_value=fights)

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses?limit=2")

    assert r.status_code == 200
    data = r.json()
    assert [f["title"] for f in data["results"]] == ["Boss One", "Boss Two"]
    assert data["total"] == 3
    assert data["next_before"] == 2000  # oldest fight on this page
    # No cursor on the first page.
    assert fake_list_sync.call_args.args[5] is None


@pytest.mark.asyncio
async def test_last_page_has_no_next_before(app):
    """Everything fits in one page → no cursor (frontend hides the button)."""
    fake_list_sync = MagicMock(return_value=[_fight(1, "Boss One", 3000)])

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")

    data = r.json()
    assert data["total"] == 1
    assert data["next_before"] is None


@pytest.mark.asyncio
async def test_before_param_reaches_the_row_query(app):
    fake_list_sync = MagicMock(return_value=[_fight(3, "Boss Three", 1000)])

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses?before=2000")

    assert r.status_code == 200
    assert fake_list_sync.call_args.args[5] == 2000


# ---------------------------------------------------------------------------
# SQL: the before clause filters strictly-older rows (real temp DB)
# ---------------------------------------------------------------------------


def _seed_encounter(conn, enc_id: int, started_at: int, *, hidden_at: int | None = None) -> None:
    conn.execute(
        "INSERT INTO encounters (id, world, act_encid, title, started_at, ended_at, duration_s,"
        " source_dsn, ingested_at, hidden_at) VALUES (?, 'Varsoon', ?, ?, ?, ?, 60, 'act://x', ?, ?)",
        (enc_id, f"enc{enc_id}", f"Boss {enc_id}", started_at, started_at + 60, started_at, hidden_at),
    )


@pytest.fixture
def seeded_parses_db(tmp_path, monkeypatch):
    store = parses_db_mod.ParsesStore(tmp_path / "parses.db")
    monkeypatch.setattr(parses_db_mod.store, "path", store.path)
    conn = store.init_db()
    for enc_id, ts in ((1, 3000), (2, 2000), (3, 1000)):
        _seed_encounter(conn, enc_id, ts)
    conn.commit()
    conn.close()
    return store


def test_list_encounters_sync_before_filters_older_rows(seeded_parses_db):
    from backend.server.api.parses.list import _list_encounters_sync

    all_rows = _list_encounters_sync(100, None, None, "Varsoon")
    assert [r["id"] for r in all_rows] == [1, 2, 3]

    older = _list_encounters_sync(100, None, None, "Varsoon", None, 2000)
    assert [r["id"] for r in older] == [3]  # strictly older than 2000


def test_admin_list_before_cursor(seeded_parses_db):
    conn = seeded_parses_db.init_db()
    try:
        page1 = parses_db_mod.ParsesStore.list_encounters_for_admin(conn, world="Varsoon", limit=2)
        assert [r["id"] for r in page1] == [1, 2]
        page2 = parses_db_mod.ParsesStore.list_encounters_for_admin(
            conn, world="Varsoon", limit=2, before=page1[-1]["started_at"]
        )
        assert [r["id"] for r in page2] == [3]
    finally:
        conn.close()
