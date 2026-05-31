"""Tests for the lifespan context manager — pins task cancellation on shutdown.

Without this, the three background tasks (prewarm, cache-sweep, census-health-
poll) hang the dev server on Ctrl-C / --reload (see memory note
'backend-reload-hangs-untracked-bg-tasks.md').
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.server.app import create_app


def test_lifespan_starts_and_stops_cleanly() -> None:
    """Entering and exiting the TestClient drives the lifespan startup +
    shutdown. If a background task hangs on shutdown, this test times out."""
    app = create_app(session_secret="x" * 32)
    with TestClient(app) as client:
        # One request just to confirm startup completed and routes are wired.
        res = client.get("/api/health")
        assert res.status_code == 200
    # Exiting the `with` block triggers shutdown — if a task hangs, pytest
    # times out per pytest.ini configuration.
