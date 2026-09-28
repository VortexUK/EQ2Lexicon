"""Log hygiene under a flood: coalesced 429 warnings and one-line unhandled
errors (backend/server/core/log_coalesce.py, unhandled_errors.py,
app.py:_rate_limit_handler)."""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.core.log_coalesce import Coalescer, coalescer
from backend.server.core.unhandled_errors import app_frame, leaf_exception

# ---------------------------------------------------------------------------
# Coalescer
# ---------------------------------------------------------------------------


def test_coalescer_lets_one_through_per_window_and_counts_the_rest():
    t = [100.0]
    c = Coalescer(clock=lambda: t[0])
    assert c.allow("k", 60) == (True, 0)
    assert c.allow("k", 60) == (False, 0)
    assert c.allow("k", 60) == (False, 0)
    t[0] = 159.9
    assert c.allow("k", 60) == (False, 0)
    t[0] = 160.0
    assert c.allow("k", 60) == (True, 3)  # reports what it swallowed
    assert c.allow("other", 60) == (True, 0)  # keys are independent


def test_leaf_exception_unwraps_nested_groups():
    inner = ValueError("boom")
    grouped = ExceptionGroup("outer", [ExceptionGroup("inner", [inner])])
    assert leaf_exception(grouped) is inner
    assert leaf_exception(inner) is inner


def test_app_frame_prefers_our_code():
    def _raise():
        raise RuntimeError("x")

    try:
        _raise()
    except RuntimeError as exc:
        frame = app_frame(exc)
    assert "test_unhandled_errors.py" in frame and "in _raise" in frame


# ---------------------------------------------------------------------------
# Unhandled exception → one line + JSON 500, traceback coalesced
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_coalescer():
    coalescer.reset()
    yield
    coalescer.reset()


@pytest.fixture
def exploding_app(app):
    from fastapi.routing import APIRoute

    async def _explode():
        raise RuntimeError("kaboom")

    # Insert ahead of the SPA catch-all route, which would otherwise 404 it.
    app.router.routes.insert(0, APIRoute("/api/_test/explode", _explode, methods=["GET"]))
    return app


@pytest.mark.asyncio
async def test_unhandled_exception_is_one_line_with_json_500(exploding_app, caplog):
    with caplog.at_level(logging.ERROR, logger="backend.server.core.unhandled_errors"):
        async with AsyncClient(transport=ASGITransport(app=exploding_app), base_url="http://test") as client:
            first = await client.get("/api/_test/explode")
            second = await client.get("/api/_test/explode")
            third = await client.get("/api/_test/explode")
    for r in (first, second, third):
        assert r.status_code == 500
        assert r.json()["detail"] == "Internal Server Error"
        assert r.json()["request_id"] == r.headers["x-request-id"]
    records = [r for r in caplog.records if "[unhandled]" in r.getMessage()]
    assert len(records) == 3
    msg = records[0].getMessage()
    assert "GET /api/_test/explode -> RuntimeError: kaboom at " in msg
    assert "test_unhandled_errors.py" in msg and "in _explode" in msg
    # Full traceback only on the first occurrence in the window.
    assert records[0].exc_info is not None
    assert records[1].exc_info is None and records[2].exc_info is None
    # No framework traceback storm reached the log.
    assert not any("Traceback" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 429 logging coalesced per client per minute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_logs_once_per_minute_per_client(app, caplog):
    # DELETE /api/guild/{g}/attendance/{id} is 10/minute and the limit is
    # checked before auth, so unauthenticated calls burn the budget: 10x 401
    # then 429s.
    with caplog.at_level(logging.WARNING):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            statuses = [(await client.delete("/api/guild/X/attendance/1")).status_code for _ in range(15)]
    assert statuses[:10] == [401] * 10
    assert statuses[10:] == [429] * 5
    ours = [r for r in caplog.records if "[ratelimit]" in r.getMessage()]
    assert len(ours) == 1
    assert "DELETE /api/guild/X/attendance/1 exceeded 10 per 1 minute for ip:" in ours[0].getMessage()
    # (slowapi's own per-request warning is pinned to ERROR by configure_logging —
    # covered in test_logging_config.py; the test app doesn't run that setup.)
