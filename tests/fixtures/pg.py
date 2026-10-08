"""Shared Postgres test plumbing.

- SESSION schemas: :func:`provision_for_session` (called from conftest's
  ``pytest_configure``) points ``pg.dsn()`` at the local TEST database and
  rebuilds the production-named schemas from db/migrations/ for the whole run.
- SCRATCH schemas: :data:`leaser` hands out ``users_s<pid>_N`` schemas built
  from the same migration file, truncated + re-seeded on release. The
  :func:`users_schema` fixture leases one and points every store at it.

:func:`pg_conn` is the seeding/assertion connection for tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import conninfo
from psycopg import sql as pgsql
from psycopg.rows import dict_row

from backend import pg, pg_migrate

#: Local-only default (the winget PG17 unattended install); override with
#: TEST_DATABASE_URL in the environment or .env.
_DEFAULT_TEST_DSN = "postgresql://postgres:postgres@localhost:5432/eq2lexicon_test"

#: Advisory lock serialising pytest SESSIONS on one test database (distinct
#: from pg_migrate's lock). provision_for_session drops + rebuilds the
#: session and scratch schemas, which would clobber another in-flight run,
#: so queue them instead of racing. Held by a dedicated connection for the
#: LIFETIME of the process (released when the process exits).
_SESSION_LOCK_ID = 0x_E92_7E57  # spells-ish "eq2TEST"
_session_lock_conn: Any = None

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
    # Every test lifespan is "the leader": the advisory-lock lease would
    # otherwise make parallel test processes stand by for each other.
    os.environ.setdefault("LEADER_LEASE", "0")

    params: dict[str, Any] = conninfo.conninfo_to_dict(test_dsn)
    dbname = str(params.get("dbname") or "eq2lexicon_test")
    # This function DROPS every family schema on the target. Refuse anything
    # that doesn't look like a local scratch database unless explicitly
    # overridden — a mis-set TEST_DATABASE_URL must never reach production.
    host = str(params.get("host") or "localhost")
    if not os.environ.get("ALLOW_REMOTE_TEST_DB") and (
        host not in ("localhost", "127.0.0.1", "::1") or not dbname.endswith("_test")
    ):
        pytest.exit(
            f"Refusing to provision tests against {host!r}/{dbname!r}: the test run drops every "
            "family schema there. Use a local database whose name ends in '_test', or set "
            "ALLOW_REMOTE_TEST_DB=1 if you really mean it.",
            returncode=4,
        )
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
                conn.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(dbname)))
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"Cannot reach the test PostgreSQL database {dbname!r}: {exc}\n{_INSTALL_HINT}",
            returncode=4,
        )

    if os.environ.get("PYTEST_XDIST_WORKER"):
        return

    # Queue behind any other pytest session on this database (see
    # _SESSION_LOCK_ID). xdist workers never reach here, so the
    # controller's lock covers its whole worker tree.
    global _session_lock_conn
    _session_lock_conn = psycopg.connect(test_dsn, autocommit=True)
    got = _session_lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (_SESSION_LOCK_ID,)).fetchone()
    if not (got and got[0]):
        print("[pg-fixtures] another pytest session holds the test database — waiting for it to finish...")
        _session_lock_conn.execute("SELECT pg_advisory_lock(%s)", (_SESSION_LOCK_ID,))

    # Family schemas are derived from the migration files' two-line headers,
    # so adding 0003_census.sql etc. needs no fixture change.
    families: list[str] = []
    for path in pg_migrate.migration_files():
        fam = migration_family(path)
        if fam and fam not in families:
            families.append(fam)
    with pg.connection() as conn:
        # Drop the ledger so pg_migrate re-applies everything, then the
        # session schemas and any scratch schemas a crashed run left over.
        conn.execute("DROP TABLE IF EXISTS public.schema_migrations")
        for fam in families:
            conn.execute(pgsql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(pgsql.Identifier(fam)))
            rows = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (fam + r"\_s%",)).fetchall()
            for r in rows:
                conn.execute(pgsql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(pgsql.Identifier(r["nspname"])))
        conn.commit()
    pg_migrate.run()


@contextmanager
def pg_conn(schema: str = "users") -> Iterator[Any]:
    """Dict-row sync connection with ``search_path`` set — the test
    seeding / assertion connection.
    Commits on clean exit; ``%s`` params, rows are dicts."""
    with pg.connection() as conn:
        conn.execute(pg.search_path_sql(schema))
        yield conn
        conn.commit()


def migration_family(path: Path) -> str | None:
    """The family a migration file targets (from its two-line header), or
    None for ``-- global`` files."""
    first = path.read_text(encoding="utf-8").splitlines()[0].strip().lower()
    if first.startswith("create schema if not exists "):
        return first.removeprefix("create schema if not exists ").rstrip(";").strip()
    return None


class SchemaLeaser:
    """Lazily-grown pool of scratch schemas for one family.

    Fresh schemas are built by retargeting EVERY migration file of the family
    (in order) — the same files production runs — so scratch and session
    schemas can never drift. ``release()`` resets with one TRUNCATE + seeds
    re-run (~10-20ms) instead of a 100-250ms CREATE SCHEMA + full DDL per
    test."""

    def __init__(self, family: str = "users") -> None:
        self.family = family
        self._migrations = [p for p in pg_migrate.migration_files() if migration_family(p) == family]
        if not self._migrations:
            raise AssertionError(f"no migration file targets family {family!r}")
        self._free: list[str] = []
        self._count = 0
        self._tables: list[str] | None = None
        self._seeds: str | None = None

    def _seeds_sql(self) -> str:
        if self._seeds is None:
            parts: list[str] = []
            for path in self._migrations:
                text = path.read_text(encoding="utf-8")
                marker = text.find("-- seeds")
                if marker >= 0:
                    parts.append(text[marker:])
            if not parts:
                raise AssertionError(f"no {self.family} migration has a '-- seeds' marker")
            self._seeds = "\n".join(parts)
        return self._seeds

    def _create(self) -> str:
        self._count += 1
        # pid suffix: parallel xdist workers share one test database.
        name = f"{self.family}_s{os.getpid()}_{self._count}"
        with pg.connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            for path in self._migrations:
                conn.execute(pg_migrate.retarget(path.read_text(encoding="utf-8"), name))
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
    it — per-test isolated."""
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
