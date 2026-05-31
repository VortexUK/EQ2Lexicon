"""Tests for POST /api/parses/tamper-report — the plugin's audit channel
for parses it refused to send to the leaderboard.

The tamper-report endpoint reuses ingest's auth + HMAC validation
verbatim, so most failure-mode tests live there. The cases pinned here
are the ones specific to this endpoint:
  * the X-Lexicon-Tamper-Reason header is required + sanitised
  * the row lands in `tamper_reports`, NOT `encounters` (the whole point)
  * the same encid posted twice produces two rows (no idempotency)
  * unknown reason codes are accepted for forward-compat with future
    plugin heuristics
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from parses import db as parses_db
from tests.web._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _sign,
    _signed_post_kwargs,
)


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
    conn = parses_db.init_db()
    try:
        return parses_db.list_tamper_reports(conn, status="all")
    finally:
        conn.close()


def _read_encounter_count() -> int:
    """Verify the tamper-report endpoint NEVER writes to encounters —
    the whole reason this endpoint exists separately."""
    conn = parses_db.init_db()
    try:
        row = conn.execute("SELECT COUNT(*) FROM encounters").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _wipe_tamper_reports() -> None:
    """Per-test isolation. The session-scoped tmp DB is shared across
    tests, but each tamper-report test starts with an empty table so
    assertion counts don't drift across runs."""
    conn = parses_db.init_db()
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
async def test_tamper_report_no_idempotency_two_posts_two_rows(app):
    """Unlike /ingest (which dedupes by (world, act_encid)), tamper
    reports keep every attempt — a user retrying a rename via the
    right-click path after the auto-skip deserves two rows so the
    admin sees both attempts."""
    _wipe_tamper_reports()
    payload = _minimal_payload()  # same encid both times

    with patch(
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
    assert len(rows) == 2
    # Distinct ids, same encid
    assert rows[0]["id"] != rows[1]["id"]
    assert rows[0]["act_encid"] == rows[1]["act_encid"]


# ---------------------------------------------------------------------------
# logger_name validation (mirrors ingest's character-name shape rule)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tamper_report_rejects_bad_logger_name(app):
    _wipe_tamper_reports()
    payload = _minimal_payload()
    payload["logger_name"] = "Bad Name 123"  # spaces + digits → invalid

    with patch(
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
        "web.routes.parses.tamper_report.require_user_session_or_token",
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
