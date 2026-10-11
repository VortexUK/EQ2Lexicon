"""Tests for the admin tamper-report list + acknowledge endpoints (admin-gated,
status/reason filters, idempotent acknowledge); reports never reach the public parses list.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import server_context as sc
from backend.server.parses import db as parses_db
from tests.fixtures.users import make_fake_admin

_fake_admin_user = make_fake_admin(id="admin-1")


def _fake_admin(request=None):  # noqa: ARG001
    return _fake_admin_user


@pytest.fixture(autouse=True)
def _parses_schema(parses_db_path: str) -> str:
    """Isolated leased parses schema per test — the fixture re-points the
    shared store, so the admin route's argless ``store.init_db()`` and the
    helpers below hit the same scratch schema."""
    return parses_db_path


def _seed_tamper_reports(now: int = 1700000000) -> list[int]:
    """Drop a fixed set of reports into the test DB and return their ids,
    newest-first so tests can refer to them by index."""
    _wipe_tamper_reports()
    # The shared store's schema is re-pointed by the autouse fixture above,
    # and init_db() reads it at call time — so this argless call and the
    # admin route's argless call hit the same leased scratch schema.
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
# Unrouted reports
#
# POST /api/parses/tamper-report does not gate logger_server, so a report can
# be stored under a world no server serves (missing -> "unknown", an unserved
# name, a served name in another casing). The panel used to list by
# current_world() alone, so those rows were stored and visible nowhere. The
# listing now carries them on every server's panel, marked unrouted.
# ---------------------------------------------------------------------------


@pytest.fixture
def two_servers(monkeypatch):
    """Pin the in-memory server registry to Varsoon (default) + Wuoshi."""
    varsoon = sc.Server("Varsoon", "varsoon", "Varsoon", 50, None, None, is_default=True)
    wuoshi = sc.Server("Wuoshi", "wuoshi", "Wuoshi", 70, None, None)
    monkeypatch.setattr(sc, "_by_world", {"Varsoon": varsoon, "Wuoshi": wuoshi})
    monkeypatch.setattr(sc, "_by_subdomain", {"varsoon": varsoon, "wuoshi": wuoshi})


def _insert_report(world: str, n: int, *, reason: str = "stale_encounter", now: int = 1700000000) -> int:
    """One report stored under ``world``. A larger ``n`` is a NEWER report."""
    conn = parses_db.store.init_db()
    try:
        rid = parses_db.store.insert_tamper_report(
            conn,
            world=world,
            act_encid=f"UNR{n:04d}",
            title=f"report {n}",
            zone="Great Divide",
            started_at=now - 100,
            ended_at=now - 50,
            duration_s=50,
            total_damage=100000,
            encdps=1000.0,
            reason=reason,
            reported_at=now + n,
            uploader_logger_name="Menludiir",
            uploader_discord_id=f"discord-u{n}",
            uploader_discord_name=f"Player {n}",
            guild_name=None,
            payload_json='{"stub":true}',
        )
        conn.commit()
        return rid
    finally:
        conn.close()


async def _list(app, query: str = "", *, server: str | None = None) -> dict:
    headers = {"X-Server": server} if server else {}
    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(f"/api/admin/tamper-reports{query}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _seed_mixed() -> dict[str, int]:
    """One report per stored world; the unrouted ones are the NEWEST."""
    _wipe_tamper_reports()
    return {
        "Varsoon": _insert_report("Varsoon", 1),
        "Wuoshi": _insert_report("Wuoshi", 2),
        "Antonia Bayle": _insert_report("Antonia Bayle", 3),  # well-formed, not served
        "unknown": _insert_report("unknown", 4),  # logger_server was missing
        "varsoon": _insert_report("varsoon", 5),  # a served name in another casing
    }


@pytest.mark.asyncio
async def test_list_carries_unrouted_reports_marked_with_their_stored_world(app, two_servers):
    ids = _seed_mixed()
    body = await _list(app)

    by_id = {row["id"]: row for row in body["results"]}
    assert set(by_id) == {ids["Varsoon"], ids["Antonia Bayle"], ids["unknown"], ids["varsoon"]}
    assert by_id[ids["Varsoon"]]["unrouted"] is False
    for stored in ("Antonia Bayle", "unknown", "varsoon"):
        assert by_id[ids[stored]]["unrouted"] is True
        assert by_id[ids[stored]]["world"] == stored  # the stored value, shown as it is
    # The other served world's report belongs to that server's panel only.
    assert ids["Wuoshi"] not in by_id

    # pending_count stays the active server's own; the unrouted are counted apart.
    assert body["pending_count"] == 1
    assert body["unrouted_pending_count"] == 3


@pytest.mark.asyncio
async def test_unrouted_reports_show_on_every_servers_panel(app, two_servers):
    ids = _seed_mixed()
    body = await _list(app, server="wuoshi")

    by_id = {row["id"]: row for row in body["results"]}
    assert set(by_id) == {ids["Wuoshi"], ids["Antonia Bayle"], ids["unknown"], ids["varsoon"]}
    assert by_id[ids["Wuoshi"]]["unrouted"] is False
    assert ids["Varsoon"] not in by_id
    assert body["pending_count"] == 1
    assert body["unrouted_pending_count"] == 3


@pytest.mark.asyncio
async def test_every_report_is_visible_on_at_least_one_panel(app, two_servers):
    """The defect itself: no stored world may leave a report unreachable."""
    ids = _seed_mixed()
    seen: set[int] = set()
    for server in ("varsoon", "wuoshi"):
        seen |= {row["id"] for row in (await _list(app, "?status=all", server=server))["results"]}
    assert seen == set(ids.values())


@pytest.mark.asyncio
async def test_servers_own_reports_sort_before_unrouted_and_survive_the_limit(app, two_servers):
    """Unrouted rows are untrusted input anyone with a token can create: they
    must not be able to push the server's own reports out of the page."""
    ids = _seed_mixed()  # every unrouted report is newer than Varsoon's
    body = await _list(app)
    assert body["results"][0]["id"] == ids["Varsoon"]
    assert [row["unrouted"] for row in body["results"]] == [False, True, True, True]
    # Each group is newest-first.
    assert [row["id"] for row in body["results"][1:]] == [ids["varsoon"], ids["unknown"], ids["Antonia Bayle"]]

    limited = await _list(app, "?limit=1")
    assert [row["id"] for row in limited["results"]] == [ids["Varsoon"]]


@pytest.mark.asyncio
async def test_status_and_reason_filters_apply_to_unrouted_reports(app, two_servers):
    _wipe_tamper_reports()
    routed = _insert_report("Varsoon", 1, reason="stale_encounter")
    unrouted_rename = _insert_report("Antonia Bayle", 2, reason="title_enemy_mismatch")
    unrouted_stale = _insert_report("unknown", 3, reason="stale_encounter")
    _acknowledge_row(unrouted_stale)

    pending = await _list(app)
    assert {row["id"] for row in pending["results"]} == {routed, unrouted_rename}
    assert pending["unrouted_pending_count"] == 1

    acked = await _list(app, "?status=ack")
    assert [(row["id"], row["unrouted"]) for row in acked["results"]] == [(unrouted_stale, True)]
    assert acked["unrouted_pending_count"] == 1  # independent of the filter in view

    by_reason = await _list(app, "?status=all&reason=title_enemy_mismatch")
    assert [row["id"] for row in by_reason["results"]] == [unrouted_rename]


@pytest.mark.asyncio
async def test_unrouted_report_can_be_acknowledged_from_the_panel(app, two_servers):
    """Visible is not enough: the admin must be able to clear it. The by-id
    and batch routes are not world-scoped, so they work on unrouted rows."""
    ids = _seed_mixed()
    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            one = await client.post(f"/api/admin/tamper-reports/{ids['unknown']}/acknowledge")
            batch = await client.post(
                "/api/admin/tamper-reports/acknowledge-batch",
                json={"ids": [ids["Antonia Bayle"], ids["varsoon"]]},
            )
    assert one.json() == {"acknowledged": True}
    assert batch.json() == {"acknowledged": 2}

    body = await _list(app)
    assert [row["id"] for row in body["results"]] == [ids["Varsoon"]]
    assert body["unrouted_pending_count"] == 0


@pytest.mark.asyncio
async def test_acknowledge_all_stays_scoped_to_the_active_server(app, two_servers):
    """Acknowledge-all flips the active server's reports only (what
    pending_count counts). Unrouted rows stay pending until acknowledged by
    id or batch, or until the retention sweep expires them."""
    ids = _seed_mixed()
    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/tamper-reports/acknowledge-all")
    assert r.json() == {"acknowledged": 1}

    body = await _list(app)
    assert {row["id"] for row in body["results"]} == {ids["Antonia Bayle"], ids["unknown"], ids["varsoon"]}
    assert body["pending_count"] == 0
    assert body["unrouted_pending_count"] == 3


@pytest.mark.asyncio
async def test_unrouted_world_is_cleaned_on_the_way_out(app, two_servers):
    """Rows stored before the route cleaned logger_server hold it as sent (up
    to 64 characters, control characters included). What the panel receives
    is printable and bounded whatever the row holds."""
    _wipe_tamper_reports()
    legacy = "evil\r\nforged line\t‮<b>x</b>" + "y" * 30
    assert len(legacy) <= 64
    rid = _insert_report(legacy, 1)
    blank = _insert_report("\r\n", 2)

    body = await _list(app)
    by_id = {row["id"]: row for row in body["results"]}
    shown = by_id[rid]["world"]
    assert by_id[rid]["unrouted"] is True
    assert shown.isprintable()
    assert shown.startswith("evilforged line<b>x</b>")  # markup is text: the page escapes it
    assert len(shown) <= 64
    assert by_id[blank]["world"] == "unknown"  # never an empty cell

    # The store itself bounds nothing on read; the cap is the route's.
    over = _insert_report("z" * 300, 3)
    shown_over = {row["id"]: row for row in (await _list(app))["results"]}[over]["world"]
    assert shown_over == "z" * 64


def test_panel_listing_leaves_the_payload_out(two_servers):
    """The panel never shows payload_json and one row's can be 512 KB."""
    _seed_mixed()
    conn = parses_db.store.init_db()
    try:
        rows = parses_db.store.list_tamper_reports_for_panel(
            conn, world="Varsoon", served_worlds=["Varsoon", "Wuoshi"], status="all"
        )
    finally:
        conn.close()
    assert rows and all("payload_json" not in row for row in rows)
    assert {row["world"] for row in rows if not row["unrouted"]} == {"Varsoon"}


def test_panel_listing_never_marks_the_active_world_unrouted():
    """Even if the caller's served list leaves the active world out."""
    _wipe_tamper_reports()
    _insert_report("Varsoon", 1)
    conn = parses_db.store.init_db()
    try:
        rows = parses_db.store.list_tamper_reports_for_panel(conn, world="Varsoon", served_worlds=["Wuoshi"])
        with pytest.raises(ValueError, match="unknown status"):
            parses_db.store.list_tamper_reports_for_panel(
                conn, world="Varsoon", served_worlds=["Varsoon"], status="bogus"
            )
    finally:
        conn.close()
    assert [(row["world"], row["unrouted"]) for row in rows] == [("Varsoon", False)]


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
            "SELECT acknowledged_at, acknowledged_by FROM tamper_reports WHERE id = %s",
            (target,),
        ).fetchone()
    finally:
        conn.close()

    assert row["acknowledged_at"] is not None  # acknowledged_at populated
    assert row["acknowledged_by"] == "admin-1"  # actor


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
            "SELECT acknowledged_by FROM tamper_reports WHERE id = %s",
            (target,),
        ).fetchone()
    finally:
        conn.close()
    assert row["acknowledged_by"] == "someone-else"


@pytest.mark.asyncio
async def test_acknowledge_missing_id_returns_false(app):
    _wipe_tamper_reports()

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/tamper-reports/99999/acknowledge")

    assert r.status_code == 200
    assert r.json() == {"acknowledged": False}


# ---------------------------------------------------------------------------
# Acknowledge-all + purge (spam-flood cleanup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_all_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/admin/tamper-reports/acknowledge-all")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_acknowledge_all_flips_every_pending(app):
    ids = _seed_tamper_reports()  # 4 pending
    _acknowledge_row(ids[1])  # one already acked — must not be re-counted

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/tamper-reports/acknowledge-all")

    assert r.status_code == 200
    assert r.json() == {"acknowledged": 3}  # only the 3 still-pending flipped

    conn = parses_db.store.init_db()
    try:
        pending = conn.execute("SELECT COUNT(*) AS n FROM tamper_reports WHERE acknowledged_at IS NULL").fetchone()["n"]
        actor = conn.execute("SELECT acknowledged_by FROM tamper_reports WHERE id = %s", (ids[0],)).fetchone()[
            "acknowledged_by"
        ]
    finally:
        conn.close()
    assert pending == 0  # working set is clear
    assert actor == "admin-1"  # actor recorded on the flipped rows


@pytest.mark.asyncio
async def test_purge_acknowledged_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/admin/tamper-reports/purge-acknowledged")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_purge_deletes_only_acknowledged(app):
    ids = _seed_tamper_reports()  # 4
    _acknowledge_row(ids[0])
    _acknowledge_row(ids[2])  # 2 acked, 2 pending

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/tamper-reports/purge-acknowledged")

    assert r.status_code == 200
    assert r.json() == {"deleted": 2}

    conn = parses_db.store.init_db()
    try:
        remaining = {row["id"] for row in conn.execute("SELECT id FROM tamper_reports").fetchall()}
    finally:
        conn.close()
    assert remaining == {ids[1], ids[3]}  # only the pending rows survive


# ---------------------------------------------------------------------------
# Isolation guarantee: tamper_reports never touches the encounters table
# ---------------------------------------------------------------------------


def test_tamper_reports_never_touch_encounters_table():
    """A row inserted into ``tamper_reports`` never surfaces on ``encounters`` —
    the only table the public parses list reads from.
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


# ---------------------------------------------------------------------------
# Bulk acknowledge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_batch_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/admin/tamper-reports/acknowledge-batch", json={"ids": [1]})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_acknowledge_batch_flips_only_pending_rows(app):
    ids = _seed_tamper_reports()
    _acknowledge_row(ids[1])  # already reviewed by a previous admin

    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/admin/tamper-reports/acknowledge-batch",
                json={"ids": [ids[0], ids[1], ids[2], 999999]},  # 1 ack'd + 1 unknown mixed in
            )

    assert r.status_code == 200
    assert r.json()["acknowledged"] == 2  # only the two pending rows flipped

    # The flipped rows carry THIS admin's stamp; the pre-ack'd row keeps its
    # original reviewer.
    conn = parses_db.store.init_db()
    try:
        rows = {r["id"]: r for r in parses_db.store.list_tamper_reports(conn, status="all")}
    finally:
        conn.close()
    assert rows[ids[0]]["acknowledged_by"] == "admin-1"
    assert rows[ids[2]]["acknowledged_by"] == "admin-1"
    assert rows[ids[1]]["acknowledged_by"] == "admin-prev"
    assert rows[ids[3]]["acknowledged_at"] is None  # untouched


@pytest.mark.asyncio
async def test_acknowledge_batch_empty_ids_is_400(app):
    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/tamper-reports/acknowledge-batch", json={"ids": []})
    assert r.status_code == 400
