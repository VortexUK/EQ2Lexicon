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
from pathlib import Path
from typing import Any

import psycopg

from backend import pg

_log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

#: One stable lock id for the whole app's migration critical section.
_ADVISORY_LOCK_ID = 0x_E92_1E71  # arbitrary constant, spells-ish "eq2lexi"

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


def apply_migrations(conn: psycopg.Connection[Any], *, directory: Path | None = None) -> list[str]:
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
        for path in migration_files(directory):
            if path.name in done:
                continue
            _log.info("[pg-migrate] applying %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            conn.execute(
                "INSERT INTO public.schema_migrations (filename) VALUES (%s)",
                (path.name,),
            )
            conn.commit()
            applied.append(path.name)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))
        conn.commit()
    return applied


def run(*, directory: Path | None = None) -> list[str]:
    """Open a connection (pool-less safe) and apply. The app lifespan and
    conftest both call this."""
    with pg.connection() as conn:
        try:
            return apply_migrations(conn, directory=directory)
        finally:
            pg.forget_schema(conn)  # the files SET search_path themselves
