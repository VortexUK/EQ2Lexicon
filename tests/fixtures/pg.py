"""Shared Postgres test plumbing for the migrated users family.

Two isolation tiers, mirroring the old SQLite arrangement:

- SESSION schema: ``pytest_configure`` (conftest) calls
  :func:`provision_for_session`, which points ``pg.dsn()`` at the local
  TEST database (never the developer's .env Supabase DSN), rebuilds the
  production-named schemas from the migration files, and leaves them
  shared for the whole run — the analog of the old shared tmpdir
  users.db that route tests ran against.
- SCRATCH schemas: the :data:`leaser` hands out ``users_s<pid>_N``
  schemas built by retargeting the SAME migration file (header
  substitution — one source of truth), reset on release with one
  ``TRUNCATE … RESTART IDENTITY CASCADE`` + a re-run of the idempotent
  ``-- seeds`` section. The :func:`users_schema` fixture leases one and
  points every store at it — the analog of ``init_db(tmp_path)`` +
  ``point_users_db_at``.

:func:`pg_conn` is the seeding/assertion replacement for the old raw
``sqlite3.connect(users_db)`` blocks.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from psycopg import conninfo
from psycopg.rows import dict_row

from backend import pg, pg_migrate

#: Local-only default (the winget PG17 unattended install); override with
#: TEST_DATABASE_URL in the environment or .env.
_DEFAULT_TEST_DSN = "postgresql://postgres:postgres@localhost:5432/eq2lexicon_test"

_INSTALL_HINT = (
    "The test suite needs a local PostgreSQL 17 server:\n"
    "  winget install PostgreSQL.PostgreSQL.17\n"
    "then make sure TEST_DATABASE_URL (default: localhost:5432, database\n"
    "eq2lexicon_test, user/password postgres) is reachable."
)


def resolve_test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL") or _DEFAULT_TEST_DSN


def provision_for_session() -> None:
    """Point the pg seam at the TEST database and rebuild the session
    schemas from the migration files. Called once from pytest_configure
    (xdist workers skip the destructive rebuild — the controller already
    did it). Fails collection with an install hint if PG is unreachable."""
    test_dsn = resolve_test_dsn()
    # DATABASE_URL is the highest-precedence name pg.dsn() reads, so this
    # guarantees no test can ever write to the .env Supabase DSN.
    os.environ["DATABASE_URL"] = test_dsn

    params: dict[str, Any] = conninfo.conninfo_to_dict(test_dsn)
    dbname = str(params.get("dbname") or "eq2lexicon_test")
    try:
        try:
            psycopg.connect(test_dsn, connect_timeout=3).close()
        except psycopg.OperationalError as exc:
            if "does not exist" not in str(exc):
                raise
            # The server is up but the test database is missing — create it
            # via the maintenance DB (CREATE DATABASE can't run in a txn).
            admin = conninfo.make_conninfo(**{**params, "dbname": "postgres"})
            with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
                conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(dbname)))
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"Cannot reach the test PostgreSQL database {dbname!r}: {exc}\n{_INSTALL_HINT}",
            returncode=4,
        )

    if os.environ.get("PYTEST_XDIST_WORKER"):
        return

    with pg.connection() as conn:
        # Drop the ledger so pg_migrate re-applies everything, then the
        # session schemas and any scratch schemas a crashed run left over.
        conn.execute("DROP TABLE IF EXISTS public.schema_migrations")
        conn.execute("DROP SCHEMA IF EXISTS users CASCADE")
        rows = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'users\\_s%'").fetchall()
        for r in rows:
            conn.execute(f'DROP SCHEMA IF EXISTS "{r["nspname"]}" CASCADE')
        conn.commit()
    pg_migrate.run()


@contextmanager
def pg_conn(schema: str = "users") -> Iterator[Any]:
    """Dict-row sync connection with ``search_path`` set — the test
    seeding / assertion replacement for ``sqlite3.connect(users_db)``.
    Commits on clean exit; ``%s`` params, rows are dicts."""
    with pg.connection() as conn:
        conn.execute(pg.search_path_sql(schema))
        yield conn
        conn.commit()


class SchemaLeaser:
    """Lazily-grown pool of scratch users-family schemas.

    Fresh schemas are built by retargeting the same migration file the
    production schema uses, so scratch and session schemas can never
    drift. ``release()`` resets with one TRUNCATE + seeds re-run
    (~10-20ms) instead of a 100-250ms CREATE SCHEMA + full DDL per test."""

    def __init__(self, family: str = "users", migration: str = "0001_users.sql") -> None:
        self.family = family
        self._migration = pg_migrate.MIGRATIONS_DIR / migration
        self._free: list[str] = []
        self._count = 0
        self._tables: list[str] | None = None
        self._seeds: str | None = None

    def _sql_text(self) -> str:
        return self._migration.read_text(encoding="utf-8")

    def _seeds_sql(self) -> str:
        if self._seeds is None:
            text = self._sql_text()
            marker = text.find("-- seeds")
            if marker < 0:
                raise AssertionError(f"{self._migration.name} has no '-- seeds' marker")
            self._seeds = text[marker:]
        return self._seeds

    def _create(self) -> str:
        self._count += 1
        # pid suffix: parallel xdist workers share one test database.
        name = f"{self.family}_s{os.getpid()}_{self._count}"
        with pg.connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            conn.execute(pg_migrate.retarget(self._sql_text(), name))
            conn.commit()
        return name

    def acquire(self) -> str:
        return self._free.pop() if self._free else self._create()

    def release(self, name: str) -> None:
        try:
            self._reset(name)
        except Exception:
            return  # never pool a schema we failed to clean
        self._free.append(name)

    def _reset(self, name: str) -> None:
        with pg.connection() as conn:
            conn.execute(pg.search_path_sql(name))
            if self._tables is None:
                rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", (name,)).fetchall()
                self._tables = sorted(r["tablename"] for r in rows)
            tables = ", ".join(f'"{t}"' for t in self._tables)
            conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
            conn.execute(self._seeds_sql())
            conn.commit()


#: The session-wide leaser every test shares.
leaser = SchemaLeaser()


@pytest.fixture
def users_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A leased scratch users-family schema with every store pointed at
    it — the per-test-isolated replacement for ``init_db(tmp_path /
    'users.db')`` + ``point_users_db_at(monkeypatch, path)``."""
    from tests.fixtures.users_db import point_users_db_at

    name = leaser.acquire()
    point_users_db_at(monkeypatch, name)
    try:
        yield name
    finally:
        leaser.release(name)


def connect_test_db() -> Any:
    """A bare dict-row connection to the test database (no search_path) —
    for the rare test that inspects cross-schema state."""
    return psycopg.connect(resolve_test_dsn(), row_factory=dict_row)  # type: ignore[arg-type]
