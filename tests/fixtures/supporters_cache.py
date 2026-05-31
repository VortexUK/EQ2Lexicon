"""Autouse fixture for supporters-cache isolation.

Fixes TEST-040: each test in tests/web/test_supporters.py calls
supporters_mod.invalidate() inline, but if a test fails before that
line, the cache leaks. This fixture invalidates BEFORE every test
regardless of subsequent failures.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _supporters_cache_isolation() -> Generator[None]:
    """Invalidate the supporters cache before AND after every test."""
    from backend.server.api import supporters as supporters_mod

    supporters_mod.invalidate()
    try:
        yield
    finally:
        supporters_mod.invalidate()
