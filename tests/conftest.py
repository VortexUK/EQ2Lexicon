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
    os.environ["DB_USERS_PATH"] = str(_TEST_DB_DIR / "users.db")
    os.environ["DB_PARSES_PATH"] = str(_TEST_DB_DIR / "backend.server.parses.db")
    os.environ["DB_CENSUS_PATH"] = str(_TEST_DB_DIR / "backend.census.db")
    os.environ["DB_ZONES_PATH"] = str(_TEST_DB_DIR / "zones.db")
    os.environ["DB_SPELLS_PATH"] = str(_TEST_DB_DIR / "spells.db")
    os.environ["DB_RECIPES_PATH"] = str(_TEST_DB_DIR / "recipes.db")
    os.environ["DB_RAIDS_PATH"] = str(_TEST_DB_DIR / "raids.db")
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

    # Imports below this line read the env vars above when they evaluate their
    # module-level constants (DB_PATH, SESSION_SECRET, ...).
    from backend.census import store as census_store

    # Force module-level DB_PATH constants to pick up the env vars set above.
    # The constants are evaluated at module import time; if a pytest plugin
    # imported these modules before pytest_configure ran (we saw it for
    # parses_db: the merger started executing SQL against the developer's
    # real data/parses/parses.db with real player names visible in debug
    # output), the cached constants point at the wrong path. Re-evaluate
    # via the shared backend.db_helpers.resolve_db_path which honours the
    # same env-var override convention.
    from backend.db_helpers import resolve_db_path  # noqa: PLC0415
    from backend.eq2db import classes as classes_db
    from backend.eq2db import raids as raids_db
    from backend.eq2db import recipes as recipes_db
    from backend.eq2db import spells as spells_db
    from backend.eq2db import zones as zones_db
    from backend.server import db as users_db
    from backend.server.parses import db as parses_db

    parses_db.DB_PATH = resolve_db_path("DB_PARSES_PATH", "parses", "parses.db")
    users_db.DB_PATH = resolve_db_path("DB_USERS_PATH", "users.db")
    census_store.DB_PATH = resolve_db_path("DB_CENSUS_PATH", "census", "census.db")
    zones_db.DB_PATH = resolve_db_path("DB_ZONES_PATH", "zones", "zones.db")
    spells_db.DB_PATH = resolve_db_path("DB_SPELLS_PATH", "spells", "spells.db")
    spells_db.catalogue.path = spells_db.DB_PATH
    recipes_db.DB_PATH = resolve_db_path("DB_RECIPES_PATH", "recipes", "recipes.db")
    recipes_db.catalogue.path = recipes_db.DB_PATH
    raids_db.DB_PATH = resolve_db_path("DB_RAIDS_PATH", "raids", "raids.db")
    classes_db.DB_PATH = resolve_db_path("DB_CLASSES_PATH", "classes", "classes.db")
    classes_db.catalogue.path = classes_db.DB_PATH

    # Create both schemas immediately. FastAPI's startup hooks (which would
    # normally call init_db) don't fire under ASGITransport, so without this
    # step API-token / parses tests would hit a missing-table OperationalError
    # the first time they read from the DB.
    users_db.init_db()
    parses_db.init_db()


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
from tests.fixtures.logging_state import _logging_state_isolation  # noqa: F401,E402
from tests.fixtures.parses_db import parses_db_conn, parses_db_path  # noqa: F401,E402
