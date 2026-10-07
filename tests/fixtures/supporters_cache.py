"""Autouse fixture: invalidate the supporters cache around every test, so a
failing test can't leak cached state into the next one.
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
