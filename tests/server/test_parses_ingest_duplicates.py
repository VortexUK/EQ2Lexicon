"""Duplicate-key payloads on /api/parses/ingest.

Before 2026-09-27 a payload with two attack_types rows for one
(attacker, swingtype, type) key reached SQLite as a UNIQUE violation and
came back as a 500 + traceback. One third-party client retried that every
~2 s for nine hours. Now: identical repeats collapse, conflicting repeats
are a 422 naming the keys, and any other IntegrityError is a 422 too.
"""

from __future__ import annotations

import copy
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.parses import IngestRequest
from backend.server.api.parses.ingest import (
    DuplicatePayloadRows,
    _attack_types_from_payload,
    _collapse_duplicate_rows,
    _damage_types_from_payload,
    _ingest_payload_sync,
)
from backend.server.parses import db as pdb
from tests.server._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _signed_post_kwargs,
)


def _payload_with_duplicate_attack(*, conflicting: bool) -> dict:
    payload = _minimal_payload(encid="DUPL0001")
    smite = payload["attack_types"][0]
    assert smite["type"] == "Smite" and smite["swingtype"] == 2
    twin = copy.deepcopy(smite)
    if conflicting:
        twin["damage"] = smite["damage"] + 1
    payload["attack_types"].append(twin)
    return payload


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def test_identical_duplicate_rows_collapse_to_one():
    req = IngestRequest(**_payload_with_duplicate_attack(conflicting=False))
    ats = _attack_types_from_payload(req.attack_types, "DUPL0001")
    dts = _damage_types_from_payload(req.damage_types, "DUPL0001")
    assert len(ats) == 3  # Smite, Smite (twin), Reverence — "All" already stripped
    dts2, ats2 = _collapse_duplicate_rows(dts, ats)
    assert [a.attack_name for a in ats2] == ["Smite", "Reverence"]
    assert dts2 == dts


def test_conflicting_duplicate_attack_rows_raise_with_the_key():
    req = IngestRequest(**_payload_with_duplicate_attack(conflicting=True))
    ats = _attack_types_from_payload(req.attack_types, "DUPL0001")
    with pytest.raises(DuplicatePayloadRows) as exc_info:
        _collapse_duplicate_rows([], ats)
    err = exc_info.value
    assert err.table == "attack_types"
    assert err.keys == [("Menludiir", 2, "Smite")]
    assert "attack_types" in str(err) and "Menludiir/2/Smite" in str(err)


def test_conflicting_duplicate_damage_rows_raise_too():
    payload = _minimal_payload()
    twin = copy.deepcopy(payload["damage_types"][0])
    twin["hits"] += 1
    payload["damage_types"].append(twin)
    req = IngestRequest(**payload)
    dts = _damage_types_from_payload(req.damage_types, "X")
    with pytest.raises(DuplicatePayloadRows) as exc_info:
        _collapse_duplicate_rows(dts, [])
    assert exc_info.value.table == "damage_types"
    assert exc_info.value.keys == [("Menludiir", "divine")]


def test_message_caps_the_listed_keys_at_five():
    err = DuplicatePayloadRows("attack_types", [("A", 1, f"Hit {i}") for i in range(8)])
    assert "(+3 more)" in str(err)


# ---------------------------------------------------------------------------
# DB level: nothing is written on a rejected payload
# ---------------------------------------------------------------------------


@pytest.fixture
def parses_tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "backend.server.parses.db"
    monkeypatch.setattr(pdb.store, "path", db_file)
    pdb.ParsesStore(db_file).init_db().close()
    return db_file


def _encounter_count(db_file) -> int:
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
    finally:
        conn.close()


def test_sync_ingest_rejects_conflicting_duplicates_and_writes_nothing(parses_tmp_db):
    payload = IngestRequest(**_payload_with_duplicate_attack(conflicting=True))
    with pytest.raises(DuplicatePayloadRows):
        _ingest_payload_sync(payload, "Menludiir", "Exordium", "plugin:123", {})
    assert _encounter_count(parses_tmp_db) == 0
    # The encid was never marked ingested either, so a corrected re-upload works.
    fixed = IngestRequest(**_minimal_payload(encid="DUPL0001"))
    status, eid, _n_c, _n_dt, n_at = _ingest_payload_sync(fixed, "Menludiir", "Exordium", "plugin:123", {})
    assert status == "inserted" and eid is not None
    assert n_at == 2


def test_sync_ingest_collapses_identical_duplicates(parses_tmp_db):
    payload = IngestRequest(**_payload_with_duplicate_attack(conflicting=False))
    status, eid, _n_c, _n_dt, n_at = _ingest_payload_sync(payload, "Menludiir", "Exordium", "plugin:123", {})
    assert status == "inserted" and eid is not None
    assert n_at == 2  # Smite once + Reverence


# ---------------------------------------------------------------------------
# Route level: 422, not 500, and the client is named in the log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_returns_422_for_conflicting_duplicates(app, parses_tmp_db, caplog):
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        caplog.at_level("WARNING", logger="backend.server.api.parses.ingest"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            kwargs = _signed_post_kwargs(_payload_with_duplicate_attack(conflicting=True))
            kwargs["headers"]["User-Agent"] = "EQ2AdvancedDesktop/1.25.77"
            r = await client.post("/api/parses/ingest", **kwargs)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "attack_types" in detail and "Menludiir/2/Smite" in detail
    assert "retrying" in detail
    assert _encounter_count(parses_tmp_db) == 0
    rejected = [m for m in caplog.messages if "rejected malformed payload" in m]
    assert len(rejected) == 1
    assert "EQ2AdvancedDesktop/1.25.77" in rejected[0] and "discord-123" in rejected[0]


@pytest.mark.asyncio
async def test_route_maps_any_other_integrity_error_to_422(app):
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "backend.server.api.parses.ingest._ingest_payload_sync",
            side_effect=sqlite3.IntegrityError("UNIQUE constraint failed: something.else"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))
    assert r.status_code == 422
    assert "something.else" in r.json()["detail"]
