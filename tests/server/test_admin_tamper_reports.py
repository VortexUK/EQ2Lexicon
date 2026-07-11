"""Tests for the admin tamper-report endpoints.

GET  /api/admin/tamper-reports — list reports for the working set
POST /api/admin/tamper-reports/{id}/acknowledge — mark one reviewed

Both routes gate on ``_require_admin``; non-admin callers hit 401/403.
Covered cases:
  * auth gate
  * default ``status="pending"`` filter
  * ``status="ack"`` / ``status="all"`` switches
  * ``reason=...`` filter
  * pending_count returns the actual unack count regardless of filter
  * acknowledge flips one pending row + records the actor's discord_id
  * acknowledge is a no-op on an already-acknowledged or missing id
  * tamper-reports never appear on the public parses list (the whole point)
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.parses import db as parses_db
from tests.fixtures.users import make_fake_admin

_fake_admin_user = make_fake_admin(id="admin-1")


def _fake_admin(request=None):  # noqa: ARG001
    return _fake_admin_user


def _seed_tamper_reports(now: int = 1700000000) -> list[int]:
    """Drop a fixed set of reports into the test DB and return their ids,
    newest-first so tests can refer to them by index."""
    _wipe_tamper_reports()
    # The shared store's `path` is re-pointed by conftest, and init_db()
    # reads it at call time — so this argless call and the admin route's
    # argless call hit the same (redirected) DB.
    conn = parses_db.store.init_db()
    ids: list[int] = []
    try:
        # Newest first (descending reported_at)
        for i, reason in enumerate(
            [
                "title_enemy_mismatch",
                "stale_encounter",
                "recent_import_activity",
                "title_enemy_mismatch",
            ]
        ):
            rid = parses_db.store.insert_tamper_report(
                conn,
                world="Varsoon",
                act_encid=f"ENC{i:04d}",
                title=f"a krait #{i}",
                zone="Great Divide",
                started_at=now - 100 - i,
                ended_at=now - 50 - i,
                duration_s=50,
                total_damage=100000,
                encdps=1000.0,
                reason=reason,
                reported_at=now - i,  # newest first
                uploader_logger_name="Menludiir",
                uploader_discord_id=f"discord-{i}",
                uploader_discord_name=f"Player {i}",
                guild_name="Exordium",
                payload_json='{"stub":true}',
            )
            ids.append(rid)
        conn.commit()
    finally:
        conn.close()
    return ids


def _wipe_tamper_reports() -> None:
    conn = parses_db.store.init_db()
    try:
        conn.execute("DELETE FROM tamper_reports")
        conn.commit()
    finally:
        conn.close()


def _acknowledge_row(report_id: int, actor: str = "admin-prev") -> None:
    """Mark a row acknowledged at the DB layer — used to seed the "ack"
    state before testing the listing filters."""
    conn = parses_db.store.init_db()
    try:
        parses_db.store.acknowledge_tamper_report(
            conn,
            report_id,
            acknowledged_at=1700000999,
            acknowledged_by=actor,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/admin/tamper-reports")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_acknowledge_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/admin/tamper-reports/1/acknowledge")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Listing — default + filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_defaults_to_pending(app):
    ids = _seed_tamper_reports()
    _acknowledge_row(ids[1])  # second-newest is now acknowledged

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/tamper-reports")

    assert r.status_code == 200
    body = r.json()
    # 4 seeded, 1 ack'd → 3 pending
    assert body["pending_count"] == 3
    assert len(body["results"]) == 3
    for row in body["results"]:
        assert row["acknowledged_at"] is None


@pytest.mark.asyncio
async def test_list_status_ack_returns_only_acknowledged(app):
    ids = _seed_tamper_reports()
    _acknowledge_row(ids[0])
    _acknowledge_row(ids[2])

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/tamper-reports?status=ack")

    body = r.json()
    assert len(body["results"]) == 2
    for row in body["results"]:
        assert row["acknowledged_at"] is not None
    # pending_count is independent of filter — still 2 unacknowledged
    assert body["pending_count"] == 2


@pytest.mark.asyncio
async def test_list_status_all_returns_everything(app):
    ids = _seed_tamper_reports()
    _acknowledge_row(ids[0])

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/tamper-reports?status=all")

    body = r.json()
    assert len(body["results"]) == 4
    assert body["pending_count"] == 3


@pytest.mark.asyncio
async def test_list_reason_filter(app):
    _seed_tamper_reports()

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/tamper-reports?status=all&reason=title_enemy_mismatch")

    body = r.json()
    # 2 of the 4 seeded rows used "title_enemy_mismatch"
    assert len(body["results"]) == 2
    for row in body["results"]:
        assert row["reason"] == "title_enemy_mismatch"


@pytest.mark.asyncio
async def test_list_rejects_invalid_status(app):
    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/tamper-reports?status=bogus")

    # FastAPI validates Literal["pending","ack","all"] → 422
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Acknowledge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_flips_pending_row(app):
    ids = _seed_tamper_reports()
    target = ids[0]

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(f"/api/admin/tamper-reports/{target}/acknowledge")

    assert r.status_code == 200
    assert r.json() == {"acknowledged": True}

    # And the DB-side flag was actually set, with the admin's id recorded.
    conn = parses_db.store.init_db()
    try:
        row = conn.execute(
            "SELECT acknowledged_at, acknowledged_by FROM tamper_reports WHERE id = ?",
            (target,),
        ).fetchone()
    finally:
        conn.close()

    assert row[0] is not None  # acknowledged_at populated
    assert row[1] == "admin-1"  # actor


@pytest.mark.asyncio
async def test_acknowledge_idempotent_on_already_ack(app):
    ids = _seed_tamper_reports()
    target = ids[0]
    _acknowledge_row(target, actor="someone-else")

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(f"/api/admin/tamper-reports/{target}/acknowledge")

    assert r.status_code == 200
    # Already-ack rows don't flip — the response says "no row changed",
    # so the UI knows not to optimistic-update.
    assert r.json() == {"acknowledged": False}

    # The original acknowledged_by is preserved — second ack does not overwrite.
    conn = parses_db.store.init_db()
    try:
        row = conn.execute(
            "SELECT acknowledged_by FROM tamper_reports WHERE id = ?",
            (target,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "someone-else"


@pytest.mark.asyncio
async def test_acknowledge_missing_id_returns_false(app):
    _wipe_tamper_reports()

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/tamper-reports/99999/acknowledge")

    assert r.status_code == 200
    assert r.json() == {"acknowledged": False}


# ---------------------------------------------------------------------------
# Isolation guarantee: tamper_reports never touches the encounters table
# ---------------------------------------------------------------------------


def test_tamper_reports_never_touch_encounters_table():
    """Pin the core invariant: a row inserted into ``tamper_reports`` must
    not surface on ``encounters``. If this ever flips True the whole
    "block from leaderboard" promise is broken.

    Pinned at the DB layer rather than via GET /api/parses — the HTTP
    list endpoint pulls in zone classification, mirror grouping, and
    guild resolution, all of which would just confirm the same point
    while introducing test-pollution risk via cached state across
    other tests in the suite. The encounters table is the only place
    the public list reads from, so absence here proves absence there.
    """
    ids = _seed_tamper_reports()
    assert len(ids) > 0  # baseline: rows actually landed

    conn = parses_db.store.init_db()
    try:
        # Use the same act_encids the seed inserted into tamper_reports —
        # if any of them showed up in encounters that's the bug.
        rows = conn.execute("SELECT id FROM encounters WHERE act_encid LIKE 'ENC%'").fetchall()
    finally:
        conn.close()
    assert rows == []


# Touch the time module so the import isn't dead — keeps the import block
# tidy without an "unused" lint flag.
_ = time  # noqa: B018
