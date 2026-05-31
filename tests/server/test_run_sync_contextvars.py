"""Regression test for the 2026-05-31 production bug: run_sync was not
propagating the caller's contextvars to the worker thread, so
current_world() inside dispatched functions silently fell back to
default_server() instead of the request's actual server.

Fixed by wrapping the executor call in contextvars.copy_context().run(...).
This test pins the contract so a future refactor of run_sync (e.g.
swapping to a fresh executor) doesn't quietly regress it.
"""

from __future__ import annotations

import contextvars

import pytest

from backend.server.core.executor import run_sync


@pytest.mark.asyncio
async def test_run_sync_propagates_contextvar_to_worker_thread():
    """A ContextVar set in the calling async task must be readable from
    inside the function dispatched via run_sync."""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("test_var", default="unset")
    var.set("from_async")

    def _worker() -> str:
        return var.get()

    seen = await run_sync(_worker)
    assert seen == "from_async", "ContextVar set in the async task was not visible to the worker thread"


@pytest.mark.asyncio
async def test_run_sync_isolates_contextvars_between_calls():
    """Two sequential run_sync calls in different contexts must each see
    their own contextvar value (no cross-contamination)."""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("test_var_isolated", default="unset")

    def _worker() -> str:
        return var.get()

    var.set("first")
    first = await run_sync(_worker)

    var.set("second")
    second = await run_sync(_worker)

    assert first == "first"
    assert second == "second"


@pytest.mark.asyncio
async def test_run_sync_works_for_active_server_contextvar():
    """End-to-end with the production-relevant ContextVar:
    web.server_context._active_server. This is the actual variable that
    current_world() reads."""
    from backend.server import server_context
    from backend.server.server_context import Server, current_world

    # Set a known server on the contextvar.
    test_server = Server(
        world="TestWorld",
        subdomain="testsubdomain",
        display_name="Test",
        max_level=50,
        current_xpac=None,
        launch_dt=None,
    )
    token = server_context._active_server.set(test_server)
    try:

        def _worker() -> str:
            return current_world()

        seen = await run_sync(_worker)
        assert seen == "TestWorld", (
            f"current_world() inside run_sync should see the request's active server, got {seen!r}"
        )
    finally:
        server_context._active_server.reset(token)
