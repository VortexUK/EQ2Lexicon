"""Shared pytest fixtures for the EQ2 Lexicon test suite.

Test isolation note
-------------------
SQLite families: ``parses.db.DB_PATH`` (and the catalogue paths) are
evaluated at module import time. To stop the test suite from touching the
developer's real data files, we redirect them via env vars **before** any
``web.*`` import below. The tmp dir is wiped at the start of every pytest
session, so tests start from an empty DB every run.

Postgres (users family): tests/fixtures/pg.py points ``pg.dsn()`` at the
local TEST database (TEST_DATABASE_URL) and rebuilds the session schemas
from db/migrations/ — never the developer's .env Supabase DSN. Per-test
isolation comes from the ``users_schema`` fixture (leased scratch schemas).

BE-096: env vars are set inside ``pytest_configure`` (a plugin-ordered hook
that runs after plugin discovery, before test collection) to avoid a race
with plugins that import ``web.app`` during discovery.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module-level: path resolution and temp-dir creation.
# Must happen before pytest_configure so _TEST_DB_DIR is available
# as a module-level constant (imported by some test modules directly).
#
# Per-process suffix (os.getpid()) makes the path unique per worker so
# parallel pytest-xdist invocations don't race on rmtree + mkdir (TEST-039).
# ---------------------------------------------------------------------------

_PROC_SUFFIX = f"{os.getpid()}"
_TEST_DB_DIR = Path(tempfile.gettempdir()) / f"eq2lexicon-pytest-{_PROC_SUFFIX}"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _tmp_db_dir_isolation() -> Generator[Path]:
    """Clean up the tmp DB dir at session teardown.

    Per-process suffix means parallel pytest-xdist workers don't race
    on rmtree (TEST-039) — each worker creates a fresh unique directory.
    The directory contents are initialised by pytest_configure before any
    test runs; we skip the pre-session wipe to avoid touching open DB
    file handles on Windows (PermissionError on locked SQLite files).
    """
    try:
        yield _TEST_DB_DIR
    finally:
        shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Plugin-ordered env var setup. Runs after plugin discovery, before
    test collection — guarantees web.app sees the right DB_*_PATH values.

    BE-096: moved from module-level os.environ calls to avoid a race with
    pytest plugins (e.g. pytest-asyncio) that may import web.app during
    plugin discovery."""
    # (items/spells/recipes moved to Postgres — their tests lease scratch
    # schemas via tests/fixtures/catalogues_db; no env re-point needed.)
    # DB_CLASSES_PATH intentionally NOT overridden — classes.db is the
    # committed source-of-truth (data/classes/classes.db) and is read-only
    # at runtime. Tests read it directly; nothing writes to it. Pointing
    # it at an empty tmpdir would make backend.eq2db.classes' import-time
    # row load fail with "classes.db is empty or unreadable".

    # web.app reads SESSION_SECRET at module-import time and raises if it's
    # unset or shorter than 32 chars. CI and fresh contributor checkouts have
    # no .env, so provide a throwaway value here (setdefault leaves a real
    # local SESSION_SECRET untouched). Must be >= 32 chars to pass the check.
    os.environ.setdefault("SESSION_SECRET", "pytest-session-secret-not-real-0123456789")

    # Force non-Secure session cookies for the test suite. HTTPS_ONLY defaults
    # to "true" (Secure flag), but the test client always talks http://, so a
    # Secure cookie would never be sent back — the OAuth-callback test would
    # lose its CSRF state and 400. Forced (not setdefault) so a contributor
    # with HTTPS_ONLY=true in their env still gets a working test run.
    os.environ["HTTPS_ONLY"] = "false"

    # --- Postgres (users family) -------------------------------------
    # psycopg's async side can't run on Windows' ProactorEventLoop; the
    # policy must be set before pytest-asyncio creates any loop.
    from backend import pg as _pg

    _pg.ensure_selector_event_loop_policy()
    # Point pg.dsn() at the local TEST database (never the .env Supabase
    # DSN) and rebuild the session schemas from the migration files.
    from tests.fixtures.pg import provision_for_session

    provision_for_session()

    # (The users + parses schemas — and the Phase-2 items/spells/recipes
    # catalogue schemas — are provisioned by provision_for_session above.
    # FastAPI's startup hooks don't fire under ASGITransport, but the
    # migrations already ran against the test database. classes.db and
    # aas.db stay committed SQLite and are read in place.)


from unittest.mock import AsyncMock, MagicMock  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Close any leaked aiohttp ClientSession instances before pytest exits.

    Why this matters: httpx.ASGITransport does NOT fire FastAPI's lifespan
    startup/shutdown — so census_lifecycle.aclose_all() (registered in the
    app's lifespan) never runs in tests. Any route test that triggers a
    Census call creates a singleton CensusClient bound to the test loop;
    that ClientSession then leaks to GC at process exit.

    On Linux/CI the destructor's logger.error('Unclosed client session')
    fires AFTER pytest has closed stdout, raising ValueError: I/O operation
    on closed file. The unraisable-exception plugin promotes that to an
    exit-1 failure even though every test passed.

    Running aclose_all() here closes the underlying sessions cleanly so
    no destructor warning fires at GC.
    """
    import asyncio

    from backend.server.core import census_lifecycle

    if not census_lifecycle._clients:
        return
    try:
        asyncio.run(census_lifecycle.aclose_all())
    except RuntimeError:
        # If there's no event loop AND we somehow can't make one (rare),
        # silently drop. The destructor warning is the worse alternative
        # but it's not a correctness bug.
        pass


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The shared slowapi limiter keeps in-memory counters for the whole
    pytest process (every test client shares one IP), so heavily-hit routes
    like /api/parses (30/minute) start returning 429 once enough tests have
    run in the same minute. Reset between tests so each starts fresh."""
    from backend.server.core.client_throttle import client_throttle
    from backend.server.limiter import limiter

    limiter.reset()
    client_throttle.reset()
    yield


@pytest.fixture
def app():
    """FastAPI application instance with a fixed session secret."""
    from backend.server.app import create_app

    return create_app(session_secret="pytest-session-secret-not-real-0123456789")


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


# Re-export per-domain fixtures so they can be requested from any test
# directory (the fixtures' module location is implementation detail).
from tests.fixtures.catalogues_db import items_schema, recipes_schema, spells_schema  # noqa: F401,E402
from tests.fixtures.census_db import census_schema  # noqa: F401,E402
from tests.fixtures.logging_state import _logging_state_isolation  # noqa: F401,E402
from tests.fixtures.parses_db import parses_db_conn, parses_db_path  # noqa: F401,E402
from tests.fixtures.pg import users_schema  # noqa: F401,E402
from tests.fixtures.zones_raids_db import raids_schema, zones_schema  # noqa: F401,E402


@pytest.fixture(autouse=True)
def _reset_parses_list_cache():
    """The /parses SWR cache is module-global — without a per-test reset a
    cached page from one test's DB would serve into the next test."""
    from backend.server.api.item import _ITEM_CACHE
    from backend.server.api.parses.list import _reset_list_cache_for_test

    _reset_list_cache_for_test()
    _ITEM_CACHE.clear()
    yield
    _reset_list_cache_for_test()
    _ITEM_CACHE.clear()
