"""Autouse fixture to snapshot + restore logger levels around each test.

Logging-config tests mutate the root and third-party loggers without restoring them.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

import pytest

_LOGGER_NAMES = (
    "",  # root
    "discord",
    "uvicorn.access",
    "aiohttp.access",
    "eq2.audit",
)


@pytest.fixture(autouse=True)
def _logging_state_isolation() -> Generator[None]:
    """Snapshot every relevant logger's level + handler list, restore on teardown."""
    snapshots: dict[str, tuple[int, list[logging.Handler]]] = {
        name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers)) for name in _LOGGER_NAMES
    }
    try:
        yield
    finally:
        for name, (level, handlers) in snapshots.items():
            lg = logging.getLogger(name)
            lg.setLevel(level)
            lg.handlers[:] = handlers
