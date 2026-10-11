"""Tests for POST /api/parses/tamper-report (auth + HMAC are shared with ingest):
reason header required + sanitised, rows land in tamper_reports NOT encounters,
no idempotency, and unknown reason codes accepted for forward-compat.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.parses import db as parses_db
from tests.server._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _sign,
    _signed_post_kwargs,
)


@pytest.fixture(autouse=True)
def _parses_schema(parses_db_path: str) -> str:
    """Isolated leased parses schema per test — the fixture re-points the
    shared store, so the route's argless ``store.init_db()`` and the helpers
    below hit the same scratch schema."""
    return parses_db_path


def _signed_with_reason(
    payload: dict,
    *,
    reason: str | None = "title_enemy_mismatch",
    token: str = "eq2c_test_token",
) -> dict:
    """Variant of _signed_post_kwargs that adds the tamper-reason header."""
    kwargs = _signed_post_kwargs(payload, token=token)
    if reason is not None:
        kwargs["headers"]["X-Lexicon-Tamper-Reason"] = reason
    return kwargs


def _read_tamper_rows() -> list[dict]:
    """Open the test DB directly and dump every tamper_reports row.
    Used by the assertions below to check the persisted side-effects
    rather than mocking the DB layer (which would obscure the SQL
    interaction with the new columns)."""
    conn = parses_db.store.init_db()
    try:
        return parses_db.store.list_tamper_reports(conn, status="all")
    finally:
        conn.close()


def _read_encounter_count() -> int:
    """Verify the tamper-report endpoint NEVER writes to encounters —
    the whole reason this endpoint exists separately."""
    conn = parses_db.store.init_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM encounters").fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def _wipe_tamper_reports() -> None:
    """Belt-and-braces isolation: the leased scratch schema already starts
    empty per test, but an explicit wipe keeps assertion counts honest even
    if a future fixture change shares state again."""
    conn = parses_db.store.init_db()
    try:
        conn.execute("DELETE FROM tamper_reports")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tamper_report_persists_row(app):
    _wipe_tamper_reports()
    encounters_before = _read_encounter_count()

    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(_minimal_payload(), reason="title_enemy_mismatch"),
            )

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["reason"] == "title_enemy_mismatch"
    assert isinstance(data["id"], int)
    assert data["id"] > 0

    rows = _read_tamper_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["reason"] == "title_enemy_mismatch"
    assert row["title"] == "a krait patriarch"
    assert row["world"] == "Varsoon"
    assert row["uploader_discord_id"] == "discord-123"
    assert row["uploader_logger_name"] == "Menludiir"
    assert row["acknowledged_at"] is None  # default: pending review
    # And — the whole point — NO row landed in encounters.
    assert _read_encounter_count() == encounters_before


@pytest.mark.asyncio
async def test_tamper_report_stores_full_payload_as_evidence(app):
    _wipe_tamper_reports()
    payload = _minimal_payload()
    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(payload, reason="stale_encounter"),
            )

    rows = _read_tamper_rows()
    assert len(rows) == 1
    stored = json.loads(rows[0]["payload_json"])
    # The stored JSON should let an admin re-render the parse exactly as
    # the plugin would have uploaded it — that's the audit trail.
    assert stored["logger_name"] == "Menludiir"
    assert stored["encounter"]["title"] == "a krait patriarch"
    assert len(stored["combatants"]) == len(payload["combatants"])


# ---------------------------------------------------------------------------
# Reason header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tamper_report_requires_reason_header(app):
    _wipe_tamper_reports()
    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/tamper-report",
                # reason=None → no X-Lexicon-Tamper-Reason header at all
                **_signed_with_reason(_minimal_payload(), reason=None),
            )

    assert r.status_code == 400
    assert "X-Lexicon-Tamper-Reason" in r.json()["detail"]
    assert _read_tamper_rows() == []


@pytest.mark.asyncio
async def test_tamper_report_empty_reason_header_rejected(app):
    _wipe_tamper_reports()
    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(_minimal_payload(), reason="   "),
            )

    assert r.status_code == 400
    assert _read_tamper_rows() == []


@pytest.mark.asyncio
async def test_tamper_report_accepts_unknown_reason_for_forward_compat(app):
    """A plugin version newer than this server might add a new reason
    code. The endpoint stores it verbatim so admins still see the row;
    only the UI styling falls back to "unknown". Belt-and-brace against
    requiring a server bump every time the plugin's heuristics evolve."""
    _wipe_tamper_reports()
    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(_minimal_payload(), reason="future_heuristic_v2"),
            )

    assert r.status_code == 201
    rows = _read_tamper_rows()
    assert len(rows) == 1
    assert rows[0]["reason"] == "future_heuristic_v2"


@pytest.mark.asyncio
async def test_tamper_report_strips_crlf_from_reason_header(app):
    """Defence-in-depth header sanitisation. Even though FastAPI/Starlette
    already reject control chars in header values, the route runs its own
    Replace pass to match the plugin-side contract — pin it so a future
    refactor can't quietly drop it."""
    _wipe_tamper_reports()
    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Build the headers manually so we can put something
            # questionable through the sanitiser — \t is allowed by
            # Starlette but our sanitise pass should leave it intact
            # since we only strip CR/LF specifically.
            payload = _minimal_payload()
            body_bytes = json.dumps(payload).encode("utf-8")
            r = await client.post(
                "/api/parses/tamper-report",
                content=body_bytes,
                headers={
                    "Authorization": "Bearer eq2c_test_token",
                    "Content-Type": "application/json",
                    "X-Lexicon-Signature": _sign(body_bytes, "eq2c_test_token"),
                    "X-Lexicon-Tamper-Reason": "  title_enemy_mismatch  ",
                },
            )

    assert r.status_code == 201
    rows = _read_tamper_rows()
    assert rows[0]["reason"] == "title_enemy_mismatch"  # trimmed


# ---------------------------------------------------------------------------
# No idempotency (every attempt becomes its own audit row)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tamper_report_retries_dedupe_into_one_row(app):
    """A retry for the same (world, encid, uploader) refreshes the existing
    report instead of storing another full payload (1,635 rows had grown to
    132 MB of duplicates)."""
    _wipe_tamper_reports()
    payload = _minimal_payload()  # same encid both times

    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(payload, reason="title_enemy_mismatch"),
            )
            r2 = await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(payload, reason="title_enemy_mismatch"),
            )

    assert r1.status_code == 201
    assert r2.status_code == 201
    rows = _read_tamper_rows()
    assert len(rows) == 1
    assert rows[0]["act_encid"] == payload["encounter"]["encid"]


# ---------------------------------------------------------------------------
# logger_name validation (mirrors ingest's character-name shape rule)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tamper_report_rejects_bad_logger_name(app):
    _wipe_tamper_reports()
    payload = _minimal_payload()
    payload["logger_name"] = "Bad Name 123"  # spaces + digits → invalid

    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(payload, reason="title_enemy_mismatch"),
            )

    assert r.status_code == 400
    assert "logger_name" in r.json()["detail"]
    assert _read_tamper_rows() == []


# ---------------------------------------------------------------------------
# logger_server: bounded by the model, never stored raw, never gated
#
# The route does not check the allowlist (a report from another server is
# still evidence), so the stored world can be a name no server serves. It is
# untrusted upload text: the model bounds its length (31, the server-name
# pattern's own) and a value that fails the shape is cleaned before it is
# stored. The admin listing shows those rows as unrouted
# (tests/server/test_admin_tamper_reports.py).
# ---------------------------------------------------------------------------


async def _post_report(app, payload: dict):
    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                "/api/parses/tamper-report",
                **_signed_with_reason(payload, reason="title_enemy_mismatch"),
            )


@pytest.mark.asyncio
async def test_tamper_report_logger_server_of_31_is_stored(app):
    """31 characters is the longest server name: accepted, stored as sent."""
    longest = "V" * 31
    r = await _post_report(app, _minimal_payload(logger_server=longest))
    assert r.status_code == 201, r.text
    assert [row["world"] for row in _read_tamper_rows()] == [longest]


@pytest.mark.asyncio
async def test_tamper_report_logger_server_of_32_is_too_long(app):
    """One past the bound is the model's 422 and nothing is stored: no
    string longer than the bound can reach the world column."""
    r = await _post_report(app, _minimal_payload(logger_server="V" * 32))
    assert r.status_code == 422, r.text
    assert [(str(e["loc"][-1]), e["type"]) for e in r.json()["detail"]] == [("logger_server", "string_too_long")]
    assert _read_tamper_rows() == []


@pytest.mark.asyncio
async def test_tamper_report_logger_name_of_16_is_too_long(app):
    payload = _minimal_payload()
    payload["logger_name"] = "A" * 16
    r = await _post_report(app, payload)
    assert r.status_code == 422, r.text
    assert _read_tamper_rows() == []


@pytest.mark.asyncio
async def test_tamper_report_unserved_server_name_is_stored_as_sent(app):
    """A well-formed name the site does not serve is not refused here."""
    r = await _post_report(app, _minimal_payload(logger_server="Antonia Bayle"))
    assert r.status_code == 201, r.text
    assert [row["world"] for row in _read_tamper_rows()] == ["Antonia Bayle"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "", "   "])
async def test_tamper_report_missing_logger_server_is_stored_as_unknown(app, missing):
    r = await _post_report(app, _minimal_payload(logger_server=missing))
    assert r.status_code == 201, r.text
    assert [row["world"] for row in _read_tamper_rows()] == ["unknown"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hostile", "stored"),
    [
        ("Varsoon\r\naudit: forged", "Varsoonaudit: forged"),  # CR/LF: would start a new log line
        ("Var\x00soon", "Varsoon"),  # NUL: Postgres text cannot hold it
        ("‮noosraV", "noosraV"),  # right-to-left override
        ("\r\n\t", "unknown"),  # nothing printable left
        ("<script>alert(1)</script>", "<script>alert(1)</script>"),  # printable: kept, escaped by the page
        ("../../etc/passwd", "../../etc/passwd"),
    ],
)
async def test_tamper_report_malformed_logger_server_is_cleaned_before_storing(app, hostile, stored):
    r = await _post_report(app, _minimal_payload(logger_server=hostile))
    assert r.status_code == 201, r.text
    rows = _read_tamper_rows()
    assert [row["world"] for row in rows] == [stored]
    assert len(rows[0]["world"]) <= 31
    assert rows[0]["world"].isprintable()
    # The value as sent is still in the evidence payload.
    assert json.loads(rows[0]["payload_json"])["logger_server"] == hostile


@pytest.mark.asyncio
async def test_tamper_report_hostile_logger_server_cannot_forge_a_log_line(app, caplog):
    """A logger_server carrying CR/LF cannot start a line of its own in the
    logs: the forged text never follows a line break in any record written
    while the report is handled, in the message or in a structured field."""
    hostile = "Varsoon\r\naudit: forged"  # 23 characters: inside the model's bound
    with caplog.at_level("DEBUG"):
        r = await _post_report(app, _minimal_payload(logger_server=hostile))
    assert r.status_code == 201, r.text

    received = [rec for rec in caplog.records if getattr(rec, "action", None) == "tamper_report.received"]
    assert len(received) == 1  # the audit event was written, so the loop below has something to check
    for rec in caplog.records:
        texts = [rec.getMessage(), *(v for v in vars(rec).values() if isinstance(v, str))]
        for text in texts:
            assert "\naudit: forged" not in text
            assert "\raudit: forged" not in text
    assert "\naudit: forged" not in caplog.text


def test_world_for_report_unit():
    from backend.server.api.parses.tamper_report import UNKNOWN_WORLD, _world_for_report

    assert _world_for_report("Varsoon") == "Varsoon"
    assert _world_for_report("  varsoon  ") == "varsoon"  # shape-valid: stored in the casing sent
    assert _world_for_report(None) == UNKNOWN_WORLD
    assert _world_for_report("") == UNKNOWN_WORLD
    # Defence in depth: the model already refuses anything past 31, but the
    # helper bounds what it returns whatever it is handed.
    assert _world_for_report("1" + "x" * 500) == "1" + "x" * 30
    assert len(_world_for_report("\x00" * 10 + "9" * 500)) == 31


# ---------------------------------------------------------------------------
# Auth gate (HMAC validation reuse from ingest — pin one negative case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tamper_report_requires_valid_hmac(app):
    """Token auth without a valid X-Lexicon-Signature is the same 401
    posture as ingest. We pin one case here; the rest of the HMAC
    matrix is exercised in test_parses_ingest_hmac.py and applies
    identically because we reuse the same _validate_payload_signature
    function."""
    _wipe_tamper_reports()
    payload = _minimal_payload()
    body_bytes = json.dumps(payload).encode("utf-8")

    with patch(
        "backend.server.api.parses.tamper_report.require_user_session_or_token",
        _fake_require_user,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/tamper-report",
                content=body_bytes,
                headers={
                    "Authorization": "Bearer eq2c_test_token",
                    "Content-Type": "application/json",
                    # Deliberately wrong sig
                    "X-Lexicon-Signature": "0" * 64,
                    "X-Lexicon-Tamper-Reason": "title_enemy_mismatch",
                },
            )

    assert r.status_code == 401
    assert _read_tamper_rows() == []
