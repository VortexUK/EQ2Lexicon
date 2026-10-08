"""Tiny raw-SQL migration runner — the single applier for local dev,
tests, and the Supabase project (no Supabase CLI in the apply path).

Files live in db/migrations/NNNN_<name>.sql and run in filename order,
each inside its own transaction, recorded in public.schema_migrations.
A pg_advisory_lock serialises concurrent runs (overlapping Railway
containers during a deploy).

FAMILY-FILE CONVENTION (meta-test enforced): a migration that targets
one store family starts with exactly these two lines::

    create schema if not exists <family>;
    set search_path to <family>, public;

followed by UNQUALIFIED DDL. The test fixtures retarget the same file at
scratch schemas (users_s1, ...) by substituting those two lines — one
source of truth for prod, dev, and per-test schemas. A file whose first
line is ``-- global`` is exempt (roles/grants).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pgsql

from backend import pg

_log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

#: One stable lock id for the whole app's migration critical section.
_ADVISORY_LOCK_ID = 0x_E92_1E71  # arbitrary constant, spells-ish "eq2lexi"

#: DDL (DROP INDEX, ALTER TABLE) needs an ACCESS EXCLUSIVE lock. Deploys
#: overlap now, and the outgoing container keeps serving — its rankings
#: rebuild or list prewarm holds a read lock on the parses tables for
#: minutes. A lock request that simply queues behind that (a) waits past
#: the healthcheck window and (b) blocks every later reader behind IT, so
#: the live site stalls too. Instead each file waits at most LOCK_TIMEOUT
#: for its locks and retries until LOCK_RETRY_DEADLINE_S, sitting out
#: between attempts; readers only ever queue for the short timeout.
LOCK_TIMEOUT = os.getenv("MIGRATE_LOCK_TIMEOUT", "2s")
LOCK_RETRY_DEADLINE_S = float(os.getenv("MIGRATE_LOCK_RETRY_DEADLINE_S", "240"))
_LOCK_RETRY_SLEEP_S = 5.0

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename   text PRIMARY KEY,
    applied_at bigint NOT NULL DEFAULT floor(extract(epoch from now()))
)
"""


def migration_files(directory: Path | None = None) -> list[Path]:
    d = directory or MIGRATIONS_DIR
    return sorted(p for p in d.glob("*.sql") if p.is_file())


def retarget(sql_text: str, schema: str) -> str:
    """Rewrite a FAMILY migration's two-line header at ``schema`` — the
    test scratch-schema mechanism. Raises on files that don't follow the
    convention (the meta-test keeps them honest)."""
    lines = sql_text.splitlines()
    if lines and lines[0].strip() == "-- global":
        raise ValueError("global migrations cannot be retargeted")
    if (
        len(lines) < 2
        or not lines[0].strip().lower().startswith("create schema if not exists ")
        or not lines[1].strip().lower().startswith("set search_path to ")
    ):
        raise ValueError("migration does not follow the two-line family header convention")
    head = [
        f"create schema if not exists {schema};",
        f"set search_path to {schema}, public;",
    ]
    return "\n".join(head + lines[2:])


def apply_migrations(
    conn: psycopg.Connection[Any], *, directory: Path | None = None, lock_retry_deadline_s: float | None = None
) -> list[str]:
    """Apply every unapplied migration file in order. Returns the applied
    filenames. The connection should be autocommit=False (default); each
    file commits individually so a failure leaves earlier files applied
    and recorded."""
    applied: list[str] = []
    conn.execute(_LEDGER_DDL)
    conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
    try:
        conn.commit()
        done = {r["filename"] for r in conn.execute("SELECT filename FROM public.schema_migrations").fetchall()}
        conn.execute(pgsql.SQL("SET lock_timeout = {}").format(pgsql.Literal(LOCK_TIMEOUT)))
        conn.commit()
        for path in migration_files(directory):
            if path.name in done:
                continue
            _log.info("[pg-migrate] applying %s", path.name)
            _apply_one(conn, path, deadline_s=lock_retry_deadline_s)
            applied.append(path.name)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("SET lock_timeout = DEFAULT")
        conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))
        conn.commit()
    return applied


def _apply_one(conn: psycopg.Connection[Any], path: Path, *, deadline_s: float | None) -> None:
    """Run one file + its ledger row in a transaction, retrying on a lock
    timeout until ``deadline_s`` has elapsed (see LOCK_TIMEOUT above)."""
    budget = LOCK_RETRY_DEADLINE_S if deadline_s is None else deadline_s
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            conn.execute("INSERT INTO public.schema_migrations (filename) VALUES (%s)", (path.name,))
            conn.commit()
            return
        except psycopg.errors.LockNotAvailable:
            conn.rollback()
            waited = time.monotonic() - started
            if waited >= budget:
                _log.error("[pg-migrate] %s: could not get its locks within %.0fs — giving up", path.name, waited)
                raise
            _log.warning(
                "[pg-migrate] %s: lock busy (attempt %d, %.0fs elapsed) — another session holds the table; retrying",
                path.name,
                attempt,
                waited,
            )
            time.sleep(_LOCK_RETRY_SLEEP_S)


def run(*, directory: Path | None = None) -> list[str]:
    """Open a connection (pool-less safe) and apply. The app lifespan and
    conftest both call this."""
    with pg.connection() as conn:
        try:
            return apply_migrations(conn, directory=directory)
        finally:
            pg.forget_schema(conn)  # the files SET search_path themselves
