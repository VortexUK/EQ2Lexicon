"""Ingest idempotency: the (world, act_encid) ingest_log row is the insert
lock, a purge leaves a tombstone that blocks re-upload until it expires, and
tamper reports dedupe per (world, encid, uploader) with a capped payload."""

from __future__ import annotations

from typing import Any

import pytest

from backend.server.api.parses import IngestRequest
from backend.server.api.parses.ingest import _check_idempotency_sync, _ingest_payload_sync
from backend.server.parses import cleanup
from backend.server.parses import db as parses_db
from tests.fixtures.pg import pg_conn
from tests.server._parses_ingest_fixtures import _minimal_payload


@pytest.fixture
def conn(parses_db_conn: Any) -> Any:
    return parses_db_conn


def test_claim_encid_is_an_exclusive_lock(conn):
    assert parses_db.store.claim_encid(conn, "LOCK0001", "Varsoon", source_dsn="plugin:1", ingested_at=100) is True
    assert parses_db.store.claim_encid(conn, "LOCK0001", "Varsoon", source_dsn="plugin:2", ingested_at=101) is False
    assert parses_db.store.claim_encid(conn, "LOCK0001", "Wuoshi", source_dsn="plugin:2", ingested_at=101) is True


def test_purge_leaves_a_tombstone_that_blocks_reupload(parses_db_path):
    payload = IngestRequest(**_minimal_payload("PURGE001"))
    status, eid, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:1", {}, "Varsoon", True)
    assert status == "inserted"

    with pg_conn(parses_db_path) as conn:
        assert parses_db.store.delete_encounter(conn, eid)
        row = conn.execute("SELECT encounter_id FROM ingest_log WHERE act_encid = 'PURGE001'").fetchone()
        assert row is not None and row["encounter_id"] is None  # tombstone, not cascaded away
        assert _check_idempotency_sync(conn, "PURGE001", "Varsoon") == ("skipped", None, 0, 0, 0)

    status2, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:1", {}, "Varsoon", True)
    assert status2 == "skipped"

    # The sweep expires tombstones older than the window; fresh ones stay.
    assert cleanup.run_tombstone_sweeps(now=1)["tombstones_expired"] == 0
    far_future = 10**10
    assert cleanup.run_tombstone_sweeps(now=far_future)["tombstones_expired"] == 1
    status3, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:1", {}, "Varsoon", True)
    assert status3 == "inserted"


def _report(conn, *, encid="TAMP0001", uploader="u1", reason="r1", payload="{}", reported_at=1000) -> int:
    return parses_db.store.insert_tamper_report(
        conn,
        world="Varsoon",
        act_encid=encid,
        title="t",
        zone=None,
        started_at=1,
        ended_at=2,
        duration_s=1,
        total_damage=0,
        encdps=0.0,
        reason=reason,
        reported_at=reported_at,
        uploader_logger_name="Menludiir",
        uploader_discord_id=uploader,
        uploader_discord_name="x",
        guild_name=None,
        payload_json=payload,
    )


def test_tamper_reports_dedupe_per_uploader_and_cap_payload(conn):
    first = _report(conn, reason="r1")
    again = _report(conn, reason="r2", reported_at=2000)
    other = _report(conn, uploader="u2")
    assert first == again and other != first
    rows = {r["uploader_discord_id"]: r for r in conn.execute("SELECT * FROM tamper_reports").fetchall()}
    assert len(rows) == 2 and rows["u1"]["reason"] == "r2" and rows["u1"]["reported_at"] == 2000

    big = "x" * (parses_db.TAMPER_PAYLOAD_CAP + 1000)
    _report(conn, encid="BIG00001", payload=big)
    stored = conn.execute("SELECT payload_json FROM tamper_reports WHERE act_encid = 'BIG00001'").fetchone()
    assert len(stored["payload_json"]) < len(big) and "truncated" in stored["payload_json"]
