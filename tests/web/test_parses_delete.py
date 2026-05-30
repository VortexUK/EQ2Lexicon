"""Tests for DELETE /api/parses/{id}, DELETE /api/parses/batch,
DELETE /api/parses (bulk), soft-delete, boss/trash/purge logic.

Extracted from test_parses.py:593-1217 per TEST-004 / Phase 2b.3.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_require_user, make_fake_user

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


def _fake_conn_for_fetch(row: dict | None) -> MagicMock:
    """Build a MagicMock connection whose `execute()` returns the given row via
    both fetchone and fetchall (the delete auth path uses an `IN (...)` query
    → fetchall). Used to fake the per-id encounter lookup inside delete_parse."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = row
    cur.fetchall.return_value = [row] if row else []
    conn.execute.return_value = cur
    return conn


def _fake_conn_multi(rows: list[dict]) -> MagicMock:
    """Like _fake_conn_for_fetch but for the batch path — fetchall returns the
    full row list."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    conn.execute.return_value = cur
    return conn


# ---------------------------------------------------------------------------
# DELETE /api/parses/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_parse_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/parses/1")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_parse_404_when_missing(app):
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_parse_admin_can_delete(app):
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:99999",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
    delete_mock = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_parse_uploader_can_delete(app):
    # _fake_user returns id="123456789"; source_dsn matches → uploader-allowed
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:123456789",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
    delete_mock = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_parse_officer_can_delete(app):
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:OTHER_USER",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
    delete_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_parse_random_user_403(app):
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:OTHER_USER",
        "title": "a krait patriarch",
        "hidden_at": None,
    }

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/parses/batch (whole multi-uploader encounter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_batch_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_batch_officer_deletes_all_uploads(app):
    # Two uploads of one fight, both by *other* people; caller is an officer of
    # the guild → may delete the whole encounter.
    rows = [
        {
            "id": 1,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER1",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
        {
            "id": 2,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER2",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
    ]
    delete_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}
    assert sorted(c.args[1] for c in delete_mock.call_args_list) == [1, 2]


@pytest.mark.asyncio
async def test_delete_batch_admin_deletes_all_uploads(app):
    rows = [
        {"id": 5, "guild_name": None, "source_dsn": "plugin:OTHER1", "title": "a krait patriarch", "hidden_at": None},
        {
            "id": 6,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER2",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
    ]
    delete_mock = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=5,6")
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}


@pytest.mark.asyncio
async def test_delete_batch_skips_unauthorised_ids(app):
    # Caller (_fake_user id=123456789) uploaded id 1 only; not officer/admin.
    # The batch deletes the one they own and skips the other.
    rows = [
        {
            "id": 1,
            "guild_name": "Exordium",
            "source_dsn": "plugin:123456789",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
        {
            "id": 2,
            "guild_name": "Exordium",
            "source_dsn": "plugin:SOMEONE_ELSE",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
    ]
    delete_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}
    assert [c.args[1] for c in delete_mock.call_args_list] == [1]


@pytest.mark.asyncio
async def test_delete_batch_none_allowed_403(app):
    rows = [
        {
            "id": 1,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER",
            "title": "a krait patriarch",
            "hidden_at": None,
        }
    ]

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_batch_404_when_no_rows(app):
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi([])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_batch_rejects_bad_ids(app):
    with patch("web.routes.parses.delete._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            empty = await client.delete("/api/parses/batch?ids=")
            bad = await client.delete("/api/parses/batch?ids=abc")
    assert empty.status_code == 400
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/parses (bulk by filter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_bulk_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_bulk_admin_passes_filters(app):
    captured = {}

    def fake_find(conn, *, guild_name, zone=None, date=None, uploaded_by=None, world=None):
        captured.update(guild_name=guild_name, zone=zone, date=date, uploaded_by=uploaded_by, world=world)
        return [
            {"id": 1, "title": "a krait patriarch", "guild_name": guild_name, "source_dsn": "plugin:X"},
            {"id": 2, "title": "a krait patriarch", "guild_name": guild_name, "source_dsn": "plugin:Y"},
        ]

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=MagicMock()),
        patch("web.routes.parses.delete.parses_db.find_encounters_by_filter", fake_find),
        patch("web.routes.parses.delete.parses_db.delete_encounter", MagicMock(return_value=True)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&zone=Great+Divide&date=2026-05-24&uploader=Menludiir")
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}
    assert captured["guild_name"] == "Exordium"
    assert captured["zone"] == "Great Divide"
    assert captured["date"] == "2026-05-24"
    assert captured["uploaded_by"] == "Menludiir"
    # world must always be passed (per-server isolation)
    assert captured["world"] is not None


@pytest.mark.asyncio
async def test_delete_bulk_officer_allowed(app):
    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=MagicMock()),
        patch(
            "web.routes.parses.delete.parses_db.find_encounters_by_filter",
            MagicMock(
                return_value=[
                    {"id": 1, "title": "a krait patriarch", "guild_name": "Exordium", "source_dsn": "plugin:X"},
                ]
            ),
        ),
        patch("web.routes.parses.delete.parses_db.delete_encounter", MagicMock(return_value=True)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}


@pytest.mark.asyncio
async def test_delete_bulk_random_user_403(app):
    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Soft-delete visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_excludes_hidden_rows(app, tmp_path, monkeypatch):
    # Real temp DB: one visible boss kill, one soft-deleted.
    import time as _t

    from parses import db as pdb
    from parses.models import Encounter

    db_file = tmp_path / "parses.db"
    monkeypatch.setattr(pdb, "DB_PATH", db_file)
    conn = pdb.init_db(db_file)
    for encid, title in [("AAA", "Tarinax"), ("BBB", "Venekor")]:
        enc = Encounter(
            encid=encid,
            title=title,
            zone="Zone",
            started_at=None,
            ended_at=None,
            duration_s=60,
            total_damage=1,
            encdps=1.0,
            kills=1,
            deaths=0,
            success_level=1,
        )
        eid = pdb.insert_encounter(conn, enc, source_dsn="eq2act", ingested_at=int(_t.time()))
        if encid == "BBB":
            pdb.soft_delete_encounter(conn, eid, hidden_at=int(_t.time()))
    conn.close()

    with patch("web.routes.parses.list._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    titles = {f["title"] for f in r.json()["results"]}
    assert "Tarinax" in titles
    assert "Venekor" not in titles  # soft-deleted → hidden from list


@pytest.mark.asyncio
async def test_detail_reports_hidden_flag(app):
    enc = {
        "id": 1,
        "act_encid": "X",
        "title": "Tarinax",
        "zone": "Z",
        "started_at": 1,
        "ended_at": 2,
        "duration_s": 1,
        "total_damage": 0,
        "encdps": 0.0,
        "kills": 0,
        "deaths": 0,
        "success_level": 1,
        "hidden_at": 1700000000,
        "combatants": [],
    }
    with (
        patch("web.routes.parses.list._require_user", _fake_user),
        patch("web.routes.parses.list._encounter_detail_sync", MagicMock(return_value=enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses/1")
    assert r.status_code == 200
    assert r.json()["hidden"] is True


# ---------------------------------------------------------------------------
# Boss soft-delete vs trash hard-delete, admin purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_boss_soft_deletes(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:123456789", "title": "Tarinax", "hidden_at": None}
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    soft.assert_called_once()
    hard.assert_not_called()


@pytest.mark.asyncio
async def test_delete_trash_hard_deletes(app):
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:123456789",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    hard.assert_called_once()
    soft.assert_not_called()


@pytest.mark.asyncio
async def test_admin_purge_hard_deletes_boss(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER", "title": "Tarinax", "hidden_at": None}
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1?purge=1")
    assert r.status_code == 200
    hard.assert_called_once()
    soft.assert_not_called()


@pytest.mark.asyncio
async def test_purge_forbidden_for_non_admin(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:123456789", "title": "Tarinax", "hidden_at": None}
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1?purge=1")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_batch_boss_soft_deletes_each(app):
    rows = [
        {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER1", "title": "Tarinax", "hidden_at": None},
        {"id": 2, "guild_name": "Exordium", "source_dsn": "plugin:OTHER2", "title": "Tarinax", "hidden_at": None},
    ]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert soft.call_count == 2
    hard.assert_not_called()


@pytest.mark.asyncio
async def test_delete_batch_purge_hard_deletes_each(app):
    rows = [
        {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER1", "title": "Tarinax", "hidden_at": None},
        {"id": 2, "guild_name": "Exordium", "source_dsn": "plugin:OTHER2", "title": "Tarinax", "hidden_at": None},
    ]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2&purge=1")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert hard.call_count == 2
    soft.assert_not_called()


# ---------------------------------------------------------------------------
# Bulk-by-filter soft-delete vs hard-delete (Task 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_delete_soft_deletes_bosses(app):
    matches = [
        {"id": 1, "title": "Tarinax", "guild_name": "Exordium", "source_dsn": "plugin:OTHER"},
        {"id": 2, "title": "a krait patriarch", "guild_name": "Exordium", "source_dsn": "plugin:OTHER"},
    ]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=MagicMock()),
        patch("web.routes.parses.delete.parses_db.find_encounters_by_filter", MagicMock(return_value=matches)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    soft.assert_called_once()  # Tarinax (boss)
    hard.assert_called_once()  # trash


@pytest.mark.asyncio
async def test_bulk_delete_purge_hard_deletes_boss(app):
    matches = [{"id": 1, "title": "Tarinax", "guild_name": "Exordium", "source_dsn": "plugin:OTHER"}]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=True),
        patch("web.routes.parses.delete.parses_db.init_db", return_value=MagicMock()),
        patch("web.routes.parses.delete.parses_db.find_encounters_by_filter", MagicMock(return_value=matches)),
        patch("web.routes.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&purge=1")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    hard.assert_called_once()  # purge forces hard delete even for a boss
    soft.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_delete_purge_forbidden_for_non_admin(app):
    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"}  # officer, but NOT admin

    with (
        patch("web.routes.parses.delete._require_user", _fake_user),
        patch("web.routes.parses.delete._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&purge=1")
    assert r.status_code == 403
