"""Tests for DELETE /api/parses/{id}, DELETE /api/parses/batch, soft-delete and
boss/trash/purge logic — and that no filter-based bulk DELETE /api/parses exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(None)),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_batch_404_when_no_rows(app):
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi([])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_batch_rejects_bad_ids(app):
    with patch("backend.server.api.parses.delete._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            empty = await client.delete("/api/parses/batch?ids=")
            bad = await client.delete("/api/parses/batch?ids=abc")
    assert empty.status_code == 400
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/parses (the filter-based bulk route) is GONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_bulk_by_filter_route_is_gone(app):
    """`DELETE /api/parses?guild=` does not exist (405, since GET does) regardless
    of auth: a filter-based delete can wipe far more than the visible rows."""
    with patch("backend.server.api.parses.delete._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            anon = await client.delete("/api/parses?guild=Exordium")
            with patch("backend.server.api.parses.delete._is_admin", return_value=True):
                admin = await client.delete("/api/parses?guild=Exordium&purge=1")
    assert anon.status_code == 405
    assert admin.status_code == 405


# ---------------------------------------------------------------------------
# DELETE /api/parses/batch — cap, per-guild memo, audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_batch_caps_at_max_ids(app):
    """Ids past PARSE_BATCH_MAX_IDS are dropped before the auth lookup — the
    page chunks at the same number, so a well-behaved client never hits it."""
    from backend.server.constants import PARSE_BATCH_MAX_IDS

    ids = list(range(1, PARSE_BATCH_MAX_IDS + 6))
    rows = [
        {"id": i, "guild_name": None, "source_dsn": "plugin:OTHER", "title": "a rat", "hidden_at": None} for i in ids
    ]
    conn = _fake_conn_multi(rows)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=conn),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", MagicMock(return_value=True)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=" + ",".join(map(str, ids)))
    assert r.status_code == 200
    # First execute() is the auth SELECT: (ids via = ANY, world) — exactly the cap.
    ids_param, world_param = conn.execute.call_args_list[0].args[1]
    assert len(ids_param) == PARSE_BATCH_MAX_IDS
    assert ids_param == ids[:PARSE_BATCH_MAX_IDS]
    assert world_param == "Varsoon"


@pytest.mark.asyncio
async def test_delete_batch_officer_check_once_per_guild(app):
    """A 200-id chunk from one guild must cost one roster lookup, not 200."""
    rows = [
        {"id": i, "guild_name": g, "source_dsn": f"plugin:OTHER{i}", "title": "a rat", "hidden_at": None}
        for i, g in enumerate(["Exordium"] * 3 + ["Remnant"] * 3, start=1)
    ]
    calls: list[str] = []

    async def fake_officer_chars(discord_id, guild):
        calls.append(guild)
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", MagicMock(return_value=True)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2,3,4,5,6")
    assert r.status_code == 200
    assert r.json() == {"deleted": 3}  # only the Exordium rows
    assert sorted(calls) == ["Exordium", "Remnant"]


@pytest.mark.asyncio
async def test_delete_batch_audit_names_the_guilds(app):
    rows = [
        {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER", "title": "a rat", "hidden_at": None},
        {"id": 2, "guild_name": "Remnant", "source_dsn": "plugin:OTHER", "title": "a rat", "hidden_at": None},
    ]
    audit = MagicMock()
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", MagicMock(return_value=True)),
        patch("backend.server.api.parses.delete.audit_log", audit),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200
    audit.assert_called_once()
    assert audit.call_args.args[0] == "parse_batch_deleted"
    assert audit.call_args.kwargs["guilds"] == "Exordium,Remnant"
    assert audit.call_args.kwargs["count"] == 2


# ---------------------------------------------------------------------------
# Soft-delete visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_excludes_hidden_rows(app, parses_db_path):
    # Real leased parses schema: one visible boss kill, one soft-deleted.
    import time as _t

    from backend.server.parses import db as pdb
    from backend.server.parses.models import Encounter

    conn = pdb.store.init_db()
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
        eid = pdb.store.insert_encounter(conn, enc, source_dsn="eq2act", ingested_at=int(_t.time()))
        if encid == "BBB":
            pdb.store.soft_delete_encounter(conn, eid, hidden_at=int(_t.time()))
    conn.close()

    with patch("backend.server.api.parses.list._require_user", _fake_user):
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
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._encounter_detail_sync", MagicMock(return_value=enc)),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", hard),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", hard),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", hard),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", hard),
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
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", soft),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2&purge=1")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert hard.call_count == 2
    soft.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/parses/{id}/unhide + hidden_by stamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete_stamps_the_actor(app):
    """A boss-kill soft-delete records WHO hid it (the admin sanitize view
    shows it)."""
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:99999",
        "title": "Wuoshi",  # uppercase = boss → soft-delete path
        "hidden_at": None,
    }
    soft_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", soft_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    args = soft_mock.call_args.args
    assert args[1] == 1  # encounter id
    assert args[3] == "123456789"  # hidden_by = the acting user


@pytest.mark.asyncio
async def test_unhide_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/parses/1/unhide")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unhide_404_when_missing(app):
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/1/unhide")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unhide_admin_restores_parse(app):
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:OTHER_USER",
        "title": "Wuoshi",
        "hidden_at": 1700001111,
    }
    unhide_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.unhide_encounter", unhide_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/1/unhide")
    assert r.status_code == 200
    assert r.json() == {"unhidden": True}
    unhide_mock.assert_called_once()


@pytest.mark.asyncio
async def test_unhide_random_user_403(app):
    """Same authorisation rule as hiding: not admin, not the uploader, not
    an officer of the guild → 403."""
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:OTHER_USER",
        "title": "Wuoshi",
        "hidden_at": 1700001111,
    }

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/1/unhide")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/parses/batch/unhide — bulk restore after a mistaken delete
# ---------------------------------------------------------------------------


def _hidden(enc_id: int, uploader: str = "OTHER_USER", hidden_by: str = "MODERATOR") -> dict:
    return {
        "id": enc_id,
        "guild_name": "Exordium",
        "source_dsn": f"plugin:{uploader}",
        "title": f"Boss {enc_id}",
        "hidden_at": 1700001111,
        "hidden_by": hidden_by,
    }


@pytest.mark.asyncio
async def test_batch_unhide_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/parses/batch/unhide?ids=1,2")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_batch_unhide_admin_restores_every_id_and_audits(app):
    rows = [_hidden(1), _hidden(2), _hidden(3)]
    unhide_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.unhide_encounter", unhide_mock),
        patch("backend.server.api.parses.delete.invalidate_parses_list_cache") as invalidate,
        patch("backend.server.api.parses.delete.audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/batch/unhide?ids=1,2,3,3")
    assert r.status_code == 200
    assert r.json() == {"unhidden": 3}
    assert [c.args[1] for c in unhide_mock.call_args_list] == [1, 2, 3]
    invalidate.assert_called_once()
    assert audit.call_args.args[0] == "parse_batch_unhidden"
    assert audit.call_args.kwargs["count"] == 3
    assert audit.call_args.kwargs["guilds"] == "Exordium"


@pytest.mark.asyncio
async def test_batch_unhide_skips_ids_the_caller_may_not_touch(app):
    """The user who hid an encounter restores it; an id someone else hid is
    skipped (an uploader cannot undo a moderator's hide), never a
    whole-request 403."""
    rows = [_hidden(1, uploader="123456789", hidden_by="123456789"), _hidden(2, uploader="123456789")]
    unhide_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.unhide_encounter", unhide_mock),
        patch("backend.server.api.parses.delete.audit_log"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/batch/unhide?ids=1,2")
    assert r.status_code == 200
    assert r.json() == {"unhidden": 1}
    assert [c.args[1] for c in unhide_mock.call_args_list] == [1]


@pytest.mark.asyncio
async def test_batch_unhide_403_when_nothing_permitted_and_404_when_nothing_found(app):
    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", fake_officer_chars),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi([_hidden(1)])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/batch/unhide?ids=1")
    assert r.status_code == 403

    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi([])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/batch/unhide?ids=1")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_batch_unhide_rejects_bad_ids(app):
    with patch("backend.server.api.parses.delete._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.post("/api/parses/batch/unhide?ids=1,x")
            empty = await client.post("/api/parses/batch/unhide?ids=,")
    assert bad.status_code == 400
    assert empty.status_code == 400


# ---------------------------------------------------------------------------
# guild_settings.officers_can_delete_parses — a leader can switch officers off
# ---------------------------------------------------------------------------


async def _officer_of_exordium(discord_id, guild):
    return {"menludiir"} if guild == "Exordium" else set()


def _flag_off():
    return patch(
        "backend.server.api.parses.delete.guild_settings_db.officers_can_delete_parses",
        new=AsyncMock(return_value={"Exordium": False}),
    )


@pytest.mark.asyncio
async def test_officer_blocked_from_single_delete_and_unhide_when_guild_disables_it(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER", "title": "Tarinax", "hidden_at": None}
    delete_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", _officer_of_exordium),
        _flag_off(),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.soft_delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            deleted = await client.delete("/api/parses/1")
            unhidden = await client.post("/api/parses/1/unhide")
    assert deleted.status_code == 403
    assert unhidden.status_code == 403
    delete_mock.assert_not_called()


@pytest.mark.asyncio
async def test_batch_skips_officer_ids_when_guild_disables_it_but_keeps_own_uploads(app):
    rows = [
        {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:123456789", "title": "a rat", "hidden_at": None},
        {"id": 2, "guild_name": "Exordium", "source_dsn": "plugin:OTHER1", "title": "a rat", "hidden_at": None},
        {"id": 3, "guild_name": "Exordium", "source_dsn": "plugin:OTHER2", "title": "a rat", "hidden_at": None},
    ]
    delete_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", _officer_of_exordium),
        _flag_off(),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2,3")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}
    assert [c.args[1] for c in delete_mock.call_args_list] == [1]


@pytest.mark.asyncio
async def test_admin_unaffected_when_guild_disables_officer_deletes(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER", "title": "a rat", "hidden_at": None}
    delete_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=True),
        _flag_off(),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_officer_allowed_when_guild_setting_is_default(app):
    """The default (no guild_settings row) keeps today's behaviour — read
    from the real users schema store rather than a mock."""
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER", "title": "a rat", "hidden_at": None}
    delete_mock = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.delete._require_user", _fake_user),
        patch("backend.server.api.parses.delete._is_admin", return_value=False),
        patch("backend.server.api.guild._officer_chars", _officer_of_exordium),
        patch("backend.server.api.parses.delete.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("backend.server.api.parses.delete.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_unhide_requires_admin_hider_or_officer():
    """The uploader alone cannot undo a moderator's hide; the hider can."""
    from unittest.mock import AsyncMock, patch

    from backend.server.api.parses import delete as mod

    uploader = make_fake_user(id="up-1")
    hider = make_fake_user(id="off-1")
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:up-1", "hidden_at": 5, "hidden_by": "off-1"}
    with patch.object(mod, "_is_admin", return_value=False):
        assert await mod._can_unhide_encounter(hider, enc, guild_ok={"Exordium": False}) is True
        assert await mod._can_unhide_encounter(uploader, enc, guild_ok={"Exordium": False}) is False
        with patch("backend.server.api.guild._officer_chars", new=AsyncMock(return_value={"sihtric"})):
            with patch.object(
                mod.guild_settings_db, "officers_can_delete_parses", new=AsyncMock(return_value={"Exordium": True})
            ):
                assert await mod._can_unhide_encounter(uploader, enc) is True
    with patch.object(mod, "_is_admin", return_value=True):
        assert await mod._can_unhide_encounter(uploader, enc) is True
