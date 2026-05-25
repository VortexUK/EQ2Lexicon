"""Shared pytest fixtures for the EQ2 Lexicon test suite.

Test isolation note
-------------------
Both ``web.db.DB_PATH`` and ``parses.db.DB_PATH`` are evaluated at module
import time. To stop the test suite from touching the developer's real
``data/users.db`` / ``data/parses/parses.db`` (the production files), we
redirect both via env vars **before** any ``web.*`` import below.

The tmp dir is wiped at the start of every pytest session, so tests start
from an empty DB every run. Per-test isolation is then up to individual
fixtures / mocks — most tests already mock the DB-touching calls outright.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.gettempdir()) / "eq2censusbot-pytest"
if _TEST_DB_DIR.exists():
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
_TEST_DB_DIR.mkdir(parents=True)
os.environ["USERS_DB_PATH"] = str(_TEST_DB_DIR / "users.db")
os.environ["PARSES_DB_PATH"] = str(_TEST_DB_DIR / "parses.db")

# web.app reads SESSION_SECRET at module-import time and raises if it's
# unset or shorter than 32 chars. CI and fresh contributor checkouts have
# no .env, so provide a throwaway value here (setdefault leaves a real
# local SESSION_SECRET untouched). Must be >= 32 chars to pass the check.
os.environ.setdefault("SESSION_SECRET", "pytest-session-secret-not-real-0123456789")

# Imports below this line read the env vars above when they evaluate their
# module-level constants (DB_PATH, SESSION_SECRET, ...).

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from parses import db as parses_db  # noqa: E402
from web import db as users_db  # noqa: E402
from web.app import create_app  # noqa: E402

# Create both schemas immediately. FastAPI's startup hooks (which would
# normally call init_db) don't fire under ASGITransport, so without this
# step API-token / parses tests would hit a missing-table OperationalError
# the first time they read from the DB.
users_db.init_db()
parses_db.init_db()

_TEST_SECRET = "test-secret-for-pytest"


@pytest.fixture
def app():
    """FastAPI application instance with a fixed session secret."""
    return create_app(session_secret=_TEST_SECRET)


@pytest.fixture
def mock_census():
    """AsyncMock CensusClient that can be customised per-test."""
    client = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_guild_cache():
    """MagicMock that mimics TTLCache.get_stale / .set behaviour (cache miss by default)."""
    cache = MagicMock()
    cache.get_stale.return_value = (None, False)
    cache.set = MagicMock()
    return cache


@pytest.fixture
def mock_character_cache():
    """MagicMock that mimics TTLCache.get_stale / .set behaviour (cache miss by default)."""
    cache = MagicMock()
    cache.get_stale.return_value = (None, False)
    cache.set = MagicMock()
    return cache
