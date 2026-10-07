"""Shared pytest fixtures for the EQ2 Lexicon test suite.

``pg.dsn()`` points at the local TEST_DATABASE_URL (never the .env DSN); per-test
isolation comes from leased scratch schemas. Env vars are set in ``pytest_configure``
because plugins may import ``backend.server.app`` during discovery.
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
# parallel pytest-xdist invocations don't race on rmtree + mkdir.
# ---------------------------------------------------------------------------

_PROC_SUFFIX = f"{os.getpid()}"
_TEST_DB_DIR = Path(tempfile.gettempdir()) / f"eq2lexicon-pytest-{_PROC_SUFFIX}"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _tmp_db_dir_isolation() -> Generator[Path]:
    """Clean up the tmp DB dir at session teardown.

    No pre-session wipe: on Windows that hits PermissionError on locked files.
    """
    try:
        yield _TEST_DB_DIR
    finally:
        shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Plugin-ordered env var setup. Runs after plugin discovery, before
    test collection — guarantees backend.server.app sees the right env."""
    # Catalogue tests lease scratch schemas via tests/fixtures/catalogues_db.
    # The classes catalogue is the migration-seeded source of truth
    # (db/migrations/0011_classes.sql), read-only at runtime; tests read it
    # directly.

    # backend.server.app reads SESSION_SECRET at module-import time and raises if it's
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

    # (Every family schema is provisioned by provision_for_session above.
    # FastAPI's startup hooks don't fire under ASGITransport, but the
    # migrations already ran against the test database.)


from unittest.mock import AsyncMock, MagicMock  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Close any leaked aiohttp ClientSession instances before pytest exits.

    ASGITransport never runs the lifespan's aclose_all(); a leaked session's
    destructor logs after stdout closes, which fails the run with exit 1.
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
def _bypass_session_access(request):
    """Route tests inject fake session users that have no ``users`` row, which
    the session access gate would log out. Enforcement is off unless the test
    requests the ``session_access_enforced`` fixture."""
    from backend.server.core import session_access

    if "session_access_enforced" in request.fixturenames:
        yield
        return
    session_access.ENFORCE = False
    try:
        yield
    finally:
        session_access.ENFORCE = True


@pytest.fixture(autouse=True)
def _bypass_uploader_claims(request):
    """Ingest/attendance tests upload as fake users with no claims; the
    logger→claim binding would mark every upload unverified. Bypassed unless
    the test requests the ``uploader_claims_enforced`` fixture."""
    from backend.server.api.parses import ingest

    if "uploader_claims_enforced" in request.fixturenames:
        yield
        return
    ingest.ENFORCE_UPLOADER_CLAIMS = False
    try:
        yield
    finally:
        ingest.ENFORCE_UPLOADER_CLAIMS = True


@pytest.fixture
def uploader_claims_enforced():
    """Opt a test INTO the real logger→approved-claim check."""
    from backend.server.api.parses import ingest

    ingest.ENFORCE_UPLOADER_CLAIMS = True
    yield


@pytest.fixture
def session_access_enforced():
    """Opt a test INTO the session access/epoch gate (with a cold cache)."""
    from backend.server.core import session_access

    session_access.ENFORCE = True
    session_access.clear_cache()
    yield
    session_access.clear_cache()


@pytest.fixture(autouse=True)
def _reset_census_refresh_gates():
    """census_refresh keeps process-wide throttle / in-flight state (now also
    used by the AA and gear-set refreshers); a test that triggered a refresh
    must not throttle the next test's."""
    from backend.server import census_refresh

    census_refresh._reset_for_test()
    yield
    census_refresh._reset_for_test()


@pytest.fixture(autouse=True)
def _reset_census_breaker():
    """The Census client records request failures in a process-wide breaker
    (backend.census.failures); a test that hits a failing/mocked Census must
    not trip census_health.is_down() for the tests after it."""
    from backend.census import failures

    failures._reset_for_test()
    yield
    failures._reset_for_test()


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
from tests.fixtures.catalogues_db import (  # noqa: F401,E402
    aas_schema,
    classes_schema,
    items_schema,
    recipes_schema,
    spells_schema,
)
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
