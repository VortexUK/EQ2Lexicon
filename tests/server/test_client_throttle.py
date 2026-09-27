"""Per-client-application flood protection (backend/server/core/client_throttle.py)."""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.core.client_throttle import (
    ClientThrottle,
    client_throttle,
    parse_client_limits,
)
from tests.server._parses_ingest_fixtures import _minimal_payload

# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_parse_spec_accepts_semicolon_and_comma_separators():
    rules = parse_client_limits("EQ2AdvancedDesktop=30/hour; Other=10/minute ,Third=5/second")
    assert [(r.prefix, str(r.item)) for r in rules] == [
        ("EQ2AdvancedDesktop", "30 per 1 hour"),
        ("Other", "10 per 1 minute"),
        ("Third", "5 per 1 second"),
    ]


def test_parse_spec_empty_disables():
    assert parse_client_limits("") == []
    assert parse_client_limits(None) == []


def test_parse_spec_skips_malformed_entries_without_raising(caplog):
    with caplog.at_level(logging.WARNING):
        rules = parse_client_limits("Good=1/minute;NoEquals;=missingprefix;Bad=lots per day")
    assert [r.prefix for r in rules] == ["Good"]
    assert sum("malformed" in m or "bad limit" in m for m in caplog.messages) == 3


# ---------------------------------------------------------------------------
# Matching + budget
# ---------------------------------------------------------------------------


def _throttle(spec: str = "EQ2AdvancedDesktop=2/minute") -> ClientThrottle:
    return ClientThrottle(parse_client_limits(spec))


def test_match_is_case_insensitive_prefix_so_version_bumps_stay_covered():
    t = _throttle()
    assert t.match("EQ2AdvancedDesktop/1.25.77") is not None
    assert t.match("eq2advanceddesktop/2.0.0 (beta)") is not None
    assert t.match("EQ2Parser/0.5.5") is None
    assert t.match("Mozilla/5.0 EQ2AdvancedDesktop") is None  # prefix, not substring
    assert t.match(None) is None
    assert t.match("") is None


def test_unlisted_client_is_never_limited():
    t = _throttle()
    for _ in range(50):
        assert t.check("EQ2Parser/0.5.5", "tok:abc").limited is False


def test_listed_client_trips_after_its_budget_with_retry_after():
    t = _throttle("EQ2AdvancedDesktop=2/minute")
    ua = "EQ2AdvancedDesktop/1.25.77"
    assert t.check(ua, "tok:abc").limited is False
    assert t.check(ua, "tok:abc").limited is False
    verdict = t.check(ua, "tok:abc")
    assert verdict.limited is True
    assert verdict.prefix == "EQ2AdvancedDesktop"
    assert verdict.limit == "2 per 1 minute"
    assert 1 <= verdict.retry_after <= 60


def test_budget_is_per_uploader_identity():
    t = _throttle("EQ2AdvancedDesktop=1/minute")
    ua = "EQ2AdvancedDesktop/1.25.77"
    assert t.check(ua, "tok:alice").limited is False
    assert t.check(ua, "tok:alice").limited is True
    # Bob running the same app has his own budget.
    assert t.check(ua, "tok:bob").limited is False


def test_rejection_is_logged_once_per_window(caplog):
    t = _throttle("EQ2AdvancedDesktop=1/minute")
    ua = "EQ2AdvancedDesktop/1.25.77"
    t.check(ua, "tok:abc", remote_ip="203.0.113.9")
    with caplog.at_level(logging.WARNING, logger="backend.server.core.client_throttle"):
        for _ in range(20):
            assert t.check(ua, "tok:abc", remote_ip="203.0.113.9").limited is True
    hits = [m for m in caplog.messages if "over its ingest budget" in m]
    assert len(hits) == 1
    assert "EQ2AdvancedDesktop" in hits[0] and "203.0.113.9" in hits[0]


def test_configure_replaces_rules_and_clears_counters():
    t = _throttle("EQ2AdvancedDesktop=1/minute")
    ua = "EQ2AdvancedDesktop/1.25.77"
    t.check(ua, "tok:abc")
    assert t.check(ua, "tok:abc").limited is True
    t.configure(parse_client_limits("EQ2AdvancedDesktop=5/minute"))
    assert t.check(ua, "tok:abc").limited is False


def test_default_instance_covers_the_2026_09_27_client():
    assert client_throttle.match("EQ2AdvancedDesktop/1.25.77") is not None
    assert client_throttle.match("EQ2Parser/0.5.5") is None
    assert client_throttle.match("EQ2LexiconACTPlugin/0.1.16") is None


# ---------------------------------------------------------------------------
# Route integration: /api/parses/ingest returns 429 before auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_429s_a_flooding_client_before_auth(app):
    previous = client_throttle.limits
    client_throttle.configure(parse_client_limits("FloodApp=2/minute"))
    try:
        headers = {"User-Agent": "FloodApp/9.9", "Authorization": "Bearer eq2c_not_real"}
        payload = _minimal_payload()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/parses/ingest", json=payload, headers=headers)
            second = await client.post("/api/parses/ingest", json=payload, headers=headers)
            third = await client.post("/api/parses/ingest", json=payload, headers=headers)
            other = await client.post(
                "/api/parses/ingest", json=payload, headers={**headers, "User-Agent": "EQ2Parser/0.5.5"}
            )
        # Budget spent on the first two (they fail auth, but they count).
        assert first.status_code == 401 and second.status_code == 401
        assert third.status_code == 429
        assert third.headers.get("retry-after", "0").isdigit() and int(third.headers["retry-after"]) >= 1
        assert "FloodApp" in third.json()["detail"]
        # A different client on the same token is untouched.
        assert other.status_code == 401
    finally:
        client_throttle.configure(previous)


@pytest.mark.asyncio
async def test_ingest_gate_runs_before_body_validation(app):
    """The gate is a dependency, so garbage bodies are throttled too — a
    flooding client never gets to spend a Pydantic parse per request."""
    previous = client_throttle.limits
    client_throttle.configure(parse_client_limits("FloodApp=1/minute"))
    try:
        headers = {"User-Agent": "FloodApp/9.9"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/parses/ingest", json={}, headers=headers)
            second = await client.post("/api/parses/ingest", json={}, headers=headers)
        assert first.status_code == 422  # body validation, as before
        assert second.status_code == 429  # gate fired before validation
    finally:
        client_throttle.configure(previous)
