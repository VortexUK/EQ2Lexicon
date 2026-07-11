"""Tests for per-server world scoping on the parses ingest and list paths.

Covers:
  * ingest with logger_server='Wuoshi' stores encounter under world='Wuoshi'
  * list endpoint (Varsoon context) excludes Wuoshi encounters
  * _ingest_payload_sync deduplication is world-scoped
  * delete endpoints are blocked from deleting cross-server encounters
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.parses import IngestRequest
from backend.server.api.parses.ingest import _ingest_payload_sync
from backend.server.parses import db as parses_db
from backend.server.parses.models import Encounter
from tests.fixtures.users import make_fake_admin

_fake_admin_user = make_fake_admin(id="admin1")

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _minimal_payload(encid: str = "ABCD1234") -> dict:
    return {
        "logger_name": "Menludiir",
        "encounter": {
            "encid": encid,
            "title": "a krait patriarch",
            "zone": "Great Divide",
            "starttime": "2026-05-24 13:51:56",
            "endtime": "2026-05-24 13:52:42",
            "duration": 46,
            "damage": 502718,
            "encdps": 10928.65,
            "kills": 4,
            "deaths": 0,
        },
        "combatants": [
            {
                "name": "Menludiir",
                "ally": "T",
                "starttime": "2026-05-24 13:51:56",
                "endtime": "2026-05-24 13:52:43",
                "duration": 47,
                "damage": 502718,
                "damageperc": "100%",
                "kills": 4,
                "healed": 11637,
                "healedperc": "100%",
                "critheals": 1,
                "heals": 40,
                "curedispels": 0,
                "powerdrain": 0,
                "powerreplenish": 0,
                "dps": 10696.13,
                "encdps": 10928.65,
                "enchps": 252.98,
                "hits": 132,
                "crithits": 123,
                "blocked": 0,
                "misses": 0,
                "swings": 132,
                "healstaken": 11637,
                "damagetaken": 27557,
                "deaths": 0,
                "tohit": 100.0,
                "critdamperc": "93%",
                "crithealperc": "3%",
            },
        ],
    }


# ---------------------------------------------------------------------------
# _ingest_payload_sync — world attribution
# ---------------------------------------------------------------------------


class TestIngestPayloadSyncWorldAttribution:
    def test_logger_server_wuoshi_stores_under_wuoshi(self, tmp_path, monkeypatch):
        """When ingest is called with world='Wuoshi', the encounter row has
        world='Wuoshi'."""
        db_file = tmp_path / "backend.server.parses.db"
        monkeypatch.setattr(parses_db.store, "path", db_file)
        parses_db.ParsesStore(db_file).init_db().close()

        payload = IngestRequest(**_minimal_payload())
        status, eid, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Wuoshi")
        assert status == "inserted"

        conn = parses_db.ParsesStore(db_file).init_db()
        try:
            row = conn.execute("SELECT world FROM encounters WHERE id = ?", (eid,)).fetchone()
            assert row[0] == "Wuoshi"
        finally:
            conn.close()

    def test_ingest_log_world_matches_encounter(self, tmp_path, monkeypatch):
        """ingest_log.world must match the encounter's world."""
        db_file = tmp_path / "backend.server.parses.db"
        monkeypatch.setattr(parses_db.store, "path", db_file)
        parses_db.ParsesStore(db_file).init_db().close()

        payload = IngestRequest(**_minimal_payload())
        _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Kaladim")

        conn = parses_db.ParsesStore(db_file).init_db()
        try:
            row = conn.execute(
                "SELECT world FROM ingest_log WHERE act_encid = ?", (payload.encounter.encid,)
            ).fetchone()
            assert row[0] == "Kaladim"
        finally:
            conn.close()

    def test_same_encid_different_world_both_inserted(self, tmp_path, monkeypatch):
        """Two ingest calls with the same act_encid but different worlds must
        both succeed (no UNIQUE collision)."""
        db_file = tmp_path / "backend.server.parses.db"
        monkeypatch.setattr(parses_db.store, "path", db_file)
        parses_db.ParsesStore(db_file).init_db().close()

        payload = IngestRequest(**_minimal_payload())
        status_v, eid_v, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Varsoon")
        status_w, eid_w, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Wuoshi")
        assert status_v == "inserted"
        assert status_w == "inserted"
        assert eid_v != eid_w

        conn = parses_db.ParsesStore(db_file).init_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM encounters WHERE act_encid = ?", (payload.encounter.encid,)
            ).fetchone()[0]
            assert count == 2
        finally:
            conn.close()

    def test_idempotency_is_world_scoped(self, tmp_path, monkeypatch):
        """Re-uploading the same (world, act_encid) returns 'skipped'; uploading
        the same act_encid under a DIFFERENT world is NOT skipped."""
        db_file = tmp_path / "backend.server.parses.db"
        monkeypatch.setattr(parses_db.store, "path", db_file)
        parses_db.ParsesStore(db_file).init_db().close()

        payload = IngestRequest(**_minimal_payload())
        _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Varsoon")

        # Same world → skipped.
        status2, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Varsoon")
        assert status2 == "skipped"

        # Different world → inserted.
        status3, *_ = _ingest_payload_sync(payload, "Menludiir", None, "plugin:123", {}, world="Wuoshi")
        assert status3 == "inserted"


# ---------------------------------------------------------------------------
# _list_encounters_sync — world scoping
# ---------------------------------------------------------------------------


class TestListEncountersSyncWorldScoping:
    def test_varsoon_list_excludes_wuoshi_encounters(self, tmp_path, monkeypatch):
        """_list_encounters_sync(world='Varsoon') must not return encounters
        stored under 'Wuoshi'."""
        db_file = tmp_path / "backend.server.parses.db"
        monkeypatch.setattr(parses_db.store, "path", db_file)
        parses_db.ParsesStore(db_file).init_db().close()

        payload_v = IngestRequest(**_minimal_payload("VARSOON1"))
        payload_w = IngestRequest(**_minimal_payload("WUOSHI01"))

        _ingest_payload_sync(payload_v, "Menludiir", "Exordium", "plugin:123", {}, world="Varsoon")
        _ingest_payload_sync(payload_w, "Menludiir", "Exordium", "plugin:456", {}, world="Wuoshi")

        from backend.server.api.parses.list import _list_encounters_sync

        v_rows = _list_encounters_sync(100, None, None, world="Varsoon")
        w_rows = _list_encounters_sync(100, None, None, world="Wuoshi")

        v_encids = {r["act_encid"] for r in v_rows}
        w_encids = {r["act_encid"] for r in w_rows}
        assert "VARSOON1" in v_encids
        assert "WUOSHI01" not in v_encids
        assert "WUOSHI01" in w_encids
        assert "VARSOON1" not in w_encids


# ---------------------------------------------------------------------------
# Cross-server delete isolation
# ---------------------------------------------------------------------------


class TestDeleteCrossServerIsolation:
    """Admin (or any caller) on Varsoon must not be able to delete an encounter
    that belongs to Wuoshi — the delete must return 404 and leave the row
    untouched."""

    def _fake_admin(self, request=None):
        return _fake_admin_user

    @pytest.mark.asyncio
    async def test_cross_server_delete_by_id_blocked(self, tmp_path, monkeypatch, app):
        """Seeded: id A=Varsoon, id B=Wuoshi.
        DELETE /api/parses/{B} under Varsoon context → 404, B still in DB.
        DELETE /api/parses/{A} under Varsoon context → 200, A gone."""
        db_file = tmp_path / "backend.server.parses.db"
        monkeypatch.setattr(parses_db.store, "path", db_file)
        parses_db.ParsesStore(db_file).init_db().close()

        # Seed two encounters in different worlds.
        payload_a = IngestRequest(**_minimal_payload("AAAAAA"))
        payload_b = IngestRequest(**_minimal_payload("BBBBBB"))
        _, id_a, *_ = _ingest_payload_sync(payload_a, "Menludiir", "Exordium", "plugin:admin1", {}, world="Varsoon")
        _, id_b, *_ = _ingest_payload_sync(payload_b, "Menludiir", "Exordium", "plugin:admin1", {}, world="Wuoshi")

        with (
            patch("backend.server.api.parses.delete._require_user", self._fake_admin),
            patch("backend.server.api.parses.delete._is_admin", return_value=True),
            patch("backend.server.api.parses.delete.current_world", return_value="Varsoon"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Cross-server: should be 404 and B must survive.
                r_cross = await client.delete(f"/api/parses/{id_b}")
                # Same-server: should succeed and A must be gone.
                r_same = await client.delete(f"/api/parses/{id_a}")

        assert r_cross.status_code == 404, "cross-server delete must return 404"
        assert r_same.status_code == 200, "same-server delete must succeed"
        assert r_same.json() == {"deleted": 1}

        # Verify B still exists in the DB; A is gone.
        conn = parses_db.ParsesStore(db_file).init_db()
        try:
            b_row = conn.execute("SELECT id FROM encounters WHERE id = ?", (id_b,)).fetchone()
            a_row = conn.execute("SELECT id FROM encounters WHERE id = ?", (id_a,)).fetchone()
        finally:
            conn.close()

        assert b_row is not None, "Wuoshi encounter B must still exist after cross-server delete attempt"
        assert a_row is None, "Varsoon encounter A must be gone after same-server delete"


# ---------------------------------------------------------------------------
# End-to-end: HTTP POST → encounters.world attribution
#
# The _ingest_payload_sync unit tests above prove that the helper persists
# whatever `world` string is passed into it.  The test below proves that
# the HTTP handler wires `parse_world = sanitized_server` (from the
# allowlist gate) through to the actual DB row — i.e. the gate→persist
# path is covered, not just each end individually.
# ---------------------------------------------------------------------------


def _sign_payload(body_bytes: bytes, token: str) -> str:
    return hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def _http_payload(logger_server: str = "Wuoshi", encid: str = "E2E00001") -> dict:
    """Minimal valid payload for end-to-end ingest, with logger_server stamped."""
    return {
        "logger_server": logger_server,
        "logger_name": "Menludiir",
        "encounter": {
            "encid": encid,
            "title": "a krait patriarch",
            "zone": "Great Divide",
            "starttime": "2026-05-24 13:51:56",
            "endtime": "2026-05-24 13:52:42",
            "duration": 46,
            "damage": 502718,
            "encdps": 10928.65,
            "kills": 4,
            "deaths": 0,
        },
        "combatants": [
            {
                "name": "Menludiir",
                "ally": "T",
                "starttime": "2026-05-24 13:51:56",
                "endtime": "2026-05-24 13:52:43",
                "duration": 47,
                "damage": 502718,
                "damageperc": "100%",
                "kills": 4,
                "healed": 11637,
                "healedperc": "100%",
                "critheals": 1,
                "heals": 40,
                "curedispels": 0,
                "powerdrain": 0,
                "powerreplenish": 0,
                "dps": 10696.13,
                "encdps": 10928.65,
                "enchps": 252.98,
                "hits": 132,
                "crithits": 123,
                "blocked": 0,
                "misses": 0,
                "swings": 132,
                "healstaken": 11637,
                "damagetaken": 27557,
                "deaths": 0,
                "tohit": 100.0,
                "critdamperc": "93%",
                "crithealperc": "3%",
                "crittypes": "0.8%L - 0.0%F - 0.0%M",
                "threatstr": "+(0)20000/-(0)0",
                "threatdelta": 20000,
            },
        ],
        "damage_types": [],
        "attack_types": [],
    }


@pytest.mark.asyncio
async def test_http_ingest_attributes_encounter_world_from_logger_server(tmp_path, monkeypatch, app):
    """End-to-end: an HTTP POST to /api/parses/ingest with
    logger_server='Wuoshi' must result in encounters.world == 'Wuoshi'.

    This test intentionally does NOT mock _ingest_payload_sync so that
    the full handler → helper → DB path is exercised.  It pins the
    parse_world = sanitized_server wiring in ingest_parse that was
    introduced in the merge of feature/per-server-urls (replacing the
    old _resolve_parse_world + current_world() fallback)."""
    db_file = tmp_path / "backend.server.parses.db"
    monkeypatch.setattr(parses_db.store, "path", db_file)
    parses_db.ParsesStore(db_file).init_db().close()

    token = "eq2c_e2e_test_token"

    async def _fake_user(request):
        return {"id": "discord-e2e", "username": "testplayer", "auth_source": "token"}

    payload = _http_payload(logger_server="Wuoshi")
    body_bytes = json.dumps(payload).encode("utf-8")

    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_user),
        patch(
            "backend.server.api.parses.ingest._resolve_uploader_guild_async",
            new=AsyncMock(return_value="Exordium"),
        ),
        patch(
            "backend.server.api.parses.ingest._cached_snapshots",
            new=MagicMock(return_value={}),
        ),
        patch(
            "backend.server.api.parses.ingest._resolve_and_update_snapshots",
            new=AsyncMock(),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                content=body_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Lexicon-Signature": _sign_payload(body_bytes, token),
                },
            )

    assert r.status_code == 201, r.text
    encounter_id = r.json()["encounter_id"]
    assert encounter_id is not None

    conn = parses_db.ParsesStore(db_file).init_db()
    try:
        row = conn.execute("SELECT world FROM encounters WHERE id = ?", (encounter_id,)).fetchone()
    finally:
        conn.close()

    assert row is not None, "encounter row not found in DB"
    assert row[0] == "Wuoshi", (
        f"expected encounters.world='Wuoshi' but got {row[0]!r} — "
        "the parse_world=sanitized_server wiring in ingest_parse is broken"
    )
