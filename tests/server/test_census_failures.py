"""The Census client no longer conflates "Census failed" with "not found"
silently: failures feed a breaker that trips census_health.is_down(), error
envelopes count as failures, and the logging is coalesced."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.census import failures
from backend.census.client import CensusClient
from backend.server import census_health


@pytest.fixture(autouse=True)
def _clean():
    census_health._reset_for_test()
    yield
    census_health._reset_for_test()


def test_breaker_trips_after_a_burst_and_resets_on_success():
    import time

    base = time.monotonic()  # is_down() reads the real clock
    for t in (base - 2.0, base - 1.0):
        failures.note_failure(now=t)
    assert not failures.tripped()
    failures.note_failure(now=base)
    assert failures.tripped()
    assert census_health.is_down()
    assert census_health.get_state()["breaker"] is True
    failures.note_success()
    assert not failures.tripped()
    assert not census_health.is_down()


def test_breaker_forgets_old_failures():
    for t in (0.0, 1.0, 2.0):
        failures.note_failure(now=t)
    assert failures.tripped(now=10.0)
    assert not failures.tripped(now=failures.TRIP_WINDOW_S + 3.0)


def test_should_log_coalesces_per_key():
    assert failures.should_log("k", every_s=60, now=0.0)
    assert not failures.should_log("k", every_s=60, now=30.0)
    assert failures.should_log("other", every_s=60, now=30.0)
    assert failures.should_log("k", every_s=60, now=61.0)


def _fake_session(status: int, body) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.url = "https://census.example/x"
    resp.json = AsyncMock(return_value=body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=cm)
    session.closed = False
    return session


@pytest.mark.asyncio
async def test_non_200_is_a_recorded_warning_not_debug(caplog):
    client = CensusClient("svc")
    client._session = _fake_session(503, None)
    with caplog.at_level(logging.WARNING, logger="backend.census.client"):
        for _ in range(3):
            assert await client._census_get("character/", {"a": "b"}) is None
    assert failures.tripped()
    assert census_health.is_down()
    warnings = [r for r in caplog.records if "HTTP 503" in r.getMessage()]
    assert len(warnings) == 1  # coalesced: one line per endpoint per minute


@pytest.mark.asyncio
async def test_error_envelope_is_a_failure_not_an_empty_result():
    client = CensusClient("svc")
    client._session = _fake_session(200, {"errorCode": "SERVER_ERROR"})
    assert await client._census_get("character/", {}) is None
    assert len(failures._failures) == 1


@pytest.mark.asyncio
async def test_healthy_body_closes_the_breaker():
    for t in (0.0, 1.0, 2.0):
        failures.note_failure(now=t)
    client = CensusClient("svc")
    client._session = _fake_session(200, {"returned": 0, "character_list": []})
    body = await client._census_get("character/", {})
    assert body == {"returned": 0, "character_list": []}
    assert not failures.tripped()
