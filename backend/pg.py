"""Shared Postgres connection plumbing: one database, one schema per family,
selected via ``SET search_path`` at checkout.

Pool-if-open-else-direct: the app lifespan opens one async + one sync pool
(`open_pools`); with no pool open (pytest, scripts, the bot's startup racing
the lifespan) the same contextmanagers use a short-lived direct connection.

Production DSN is the Supabase Supavisor SESSION pooler (:5432,
sslmode=require). Transaction mode (:6543) would break psycopg's
prepared statements and the per-checkout search_path — don't use it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

#: Accepted DSN env names, in precedence order. DATABASE_URL is the
#: canonical name (Railway); the others match dashboard-copied names.
_DSN_VARS = ("DATABASE_URL", "SUPABASE_DB_URL", "POSTGRES_CONNECTION_STRING")


def ensure_selector_event_loop_policy() -> None:
    """psycopg's async side cannot run on Windows' default ProactorEventLoop.
    Call before asyncio.run() on entry points that touch Postgres (main.py,
    conftest, scripts). No-op elsewhere; production Linux is unaffected."""
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def dsn() -> str:
    for var in _DSN_VARS:
        value = os.getenv(var)
        if value:
            return value
    raise RuntimeError(f"No Postgres DSN configured — set one of {', '.join(_DSN_VARS)}.")


_async_pool: AsyncConnectionPool | None = None
_sync_pool: ConnectionPool | None = None

# App connections must never camp an open transaction: a session left "idle
# in transaction" holds its locks indefinitely and starves maintenance DDL.
# The app's real between-statement idling is milliseconds, so two minutes is
# pure headroom. Pool-only on purpose — scripts/tests use direct connections and may
# legitimately pause mid-transaction while computing. A plain SET survives
# the Supavisor SESSION pooler (startup `options` packets do not).
_IDLE_TXN_TIMEOUT_SQL = "SET idle_in_transaction_session_timeout = '120s'"

# Per-statement ceilings for APP connections (the Supabase role default is
# 2 minutes, lock_timeout 0): no request-path statement legitimately runs
# this long, and a statement stuck behind a lock while holding a pool slot
# must fail fast rather than freeze the pool. Long maintenance work
# (migrations, scripts, the bulk loaders) uses direct connections and is unaffected; a
# sweep that genuinely needs more can `SET LOCAL statement_timeout`.
_STATEMENT_TIMEOUT_SQL = "SET statement_timeout = '30s'"
_LOCK_TIMEOUT_SQL = "SET lock_timeout = '10s'"
_CONFIGURE_SQL = (_IDLE_TXN_TIMEOUT_SQL, _STATEMENT_TIMEOUT_SQL, _LOCK_TIMEOUT_SQL)

# Checkout wait before a PoolTimeout. The library default (30 s) on a
# saturated pool stalls every loop-thread caller for 30 s; failing in 5 s
# surfaces as a 500/503 instead.
POOL_CHECKOUT_TIMEOUT_S = 5.0


def _configure_sync(conn: psycopg.Connection) -> None:
    for sql in _CONFIGURE_SQL:
        conn.execute(sql)
    conn.commit()


async def _configure_async(conn: psycopg.AsyncConnection) -> None:
    for sql in _CONFIGURE_SQL:
        await conn.execute(sql)
    await conn.commit()


async def open_pools() -> None:
    """Open the shared pools — called once from the app lifespan startup.
    Small on purpose: WEB_CONCURRENCY=1 and web+bot share one process, so
    ~10 server connections total stays well inside Supabase's session-
    pooler limits."""
    import asyncio
    import sys

    if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
        # Fail fast and loud: psycopg's async side can't create connections
        # on the ProactorEventLoop, and the symptom otherwise is every
        # async checkout silently timing out after 30s (a blank site).
        # Plain `uvicorn backend.server.app:app` lands here on Windows
        # (uvicorn 0.52 picks Proactor without --reload).
        raise RuntimeError(
            "psycopg async cannot run on Windows' ProactorEventLoop. Start the app "
            "via `python main.py`, or uvicorn WITH --reload (scripts/dev_backend.ps1), "
            "or call pg.ensure_selector_event_loop_policy() before the loop is created."
        )
    global _async_pool, _sync_pool
    _async_pool = AsyncConnectionPool(
        dsn(),
        min_size=1,
        max_size=5,
        open=False,
        timeout=POOL_CHECKOUT_TIMEOUT_S,
        kwargs={"row_factory": dict_row},
        configure=_configure_async,
    )
    await _async_pool.open()
    _sync_pool = ConnectionPool(
        dsn(),
        min_size=1,
        max_size=5,
        timeout=POOL_CHECKOUT_TIMEOUT_S,
        kwargs={"row_factory": dict_row},
        configure=_configure_sync,
    )


async def close_pools() -> None:
    """Lifespan shutdown. Safe to call when pools never opened."""
    global _async_pool, _sync_pool
    if _async_pool is not None:
        await _async_pool.close()
        _async_pool = None
    if _sync_pool is not None:
        _sync_pool.close()
        _sync_pool = None


# ── Schema selection (sticky, session-level) ─────────────────────────────────
# Every pooled call used to pay BEGIN + SET search_path + query + COMMIT: four
# round trips to Supabase for one SELECT. Two things cut that:
#
#   * The schema is remembered per physical connection (``_SCHEMA_ATTR``) and
#     the SET is skipped when it already matches — the users schema dominates
#     traffic, so most checkouts send nothing. The SET runs with autocommit on,
#     i.e. session-level, so a later rollback can never revert it (an
#     uncommitted SET inside the implicit transaction was the latent hazard).
#     A transaction-scoped switch (erasure spans two schemas in one
#     transaction) uses :func:`local_search_path_sql` and leaves the memo alone.
#   * ``autocommit=True`` for single-query reads drops the BEGIN/COMMIT pair.
#     The flag is reset before the connection returns to the pool.
#
# Transaction status is IDLE at checkout (the pool rolls back on return, a
# direct connection is fresh), which is what makes the autocommit toggle a
# local state change rather than a round trip.

_SCHEMA_ATTR = "_eq2_schema"


def _apply_schema_sync(conn: Any, schema: str | None) -> None:
    if schema is None or getattr(conn, _SCHEMA_ATTR, None) == schema:
        return
    was_autocommit = conn.autocommit
    if not was_autocommit:
        conn.autocommit = True
    try:
        conn.execute(search_path_sql(schema))
    finally:
        if not was_autocommit:
            conn.autocommit = False
    setattr(conn, _SCHEMA_ATTR, schema)


async def _apply_schema_async(conn: Any, schema: str | None) -> None:
    if schema is None or getattr(conn, _SCHEMA_ATTR, None) == schema:
        return
    was_autocommit = conn.autocommit
    if not was_autocommit:
        await conn.set_autocommit(True)
    try:
        await conn.execute(search_path_sql(schema))
    finally:
        if not was_autocommit:
            await conn.set_autocommit(False)
    setattr(conn, _SCHEMA_ATTR, schema)


@asynccontextmanager
async def aconnection(schema: str | None = None, *, autocommit: bool = False) -> AsyncIterator[Any]:
    # Yields Any on purpose: psycopg's stubs demand LiteralString queries,
    # but the house pattern is named SQL blocks loaded from .sql sidecars.
    """Async checkout: pooled when the lifespan opened pools, else a
    short-lived direct connection (tests / scripts / pre-lifespan bot).
    ``schema`` selects the family search_path (sticky per connection, see
    above); ``autocommit=True`` is for single-statement reads. Without it
    psycopg commits on clean ``async with`` exit and rolls back on
    exception — explicit commits inside remain harmless."""
    if _async_pool is not None:
        async with _async_pool.connection() as conn:
            await _apply_schema_async(conn, schema)
            if not autocommit:
                yield conn
                return
            await conn.set_autocommit(True)
            try:
                yield conn
            finally:
                try:
                    await conn.set_autocommit(False)
                except Exception:  # broken connection — the pool discards it
                    pass
    else:
        async with await psycopg.AsyncConnection.connect(dsn(), row_factory=dict_row) as conn:  # type: ignore[arg-type]
            await _apply_schema_async(conn, schema)
            if autocommit:
                await conn.set_autocommit(True)
            yield conn


@contextmanager
def connection(schema: str | None = None, *, autocommit: bool = False) -> Iterator[Any]:
    """Sync twin of :func:`aconnection`."""
    if _sync_pool is not None:
        with _sync_pool.connection() as conn:
            _apply_schema_sync(conn, schema)
            if not autocommit:
                yield conn
                return
            conn.autocommit = True
            try:
                yield conn
            finally:
                try:
                    conn.autocommit = False
                except Exception:  # broken connection — the pool discards it
                    pass
    else:
        with psycopg.connect(dsn(), row_factory=dict_row) as conn:  # type: ignore[arg-type]
            _apply_schema_sync(conn, schema)
            if autocommit:
                conn.autocommit = True
            yield conn


def getconn(schema: str | None = None) -> Any:
    """Caller-owned sync checkout — ALWAYS pair with :func:`putconn`.
    Pooled when the lifespan opened pools, else a direct connection.
    For scoped work prefer the :func:`connection` contextmanager; this
    exists for the catalogue families' caller-owns-connection pattern
    (PgCatalogue.init_db in backend/db_catalogue.py)."""
    conn = _sync_pool.getconn() if _sync_pool is not None else psycopg.connect(dsn(), row_factory=dict_row)  # type: ignore[arg-type]
    try:
        _apply_schema_sync(conn, schema)
    except Exception:
        putconn(conn)
        raise
    return conn


def putconn(conn: Any) -> None:
    """Return a :func:`getconn` connection. Pool-origin connections go back
    to the pool (psycopg_pool resets them); direct ones just close. The
    pool-closed-since race degrades to close()."""
    if _sync_pool is not None:
        try:
            _sync_pool.putconn(conn)
        except ValueError:
            conn.close()
        return
    conn.close()


def search_path_sql(schema: str) -> pgsql.Composed:
    """Session-level schema selection — what the checkout helpers run (once
    per connection per schema). Composed so a schema name can never inject
    (test scratch schemas are generated names). Prefer passing ``schema``
    to :func:`connection` / :func:`aconnection` / :func:`getconn` over
    executing this directly: a direct SET inside a transaction is not
    reflected in the per-connection memo."""
    return pgsql.SQL("SET search_path TO {}, public").format(pgsql.Identifier(schema))


def forget_schema(conn: Any) -> None:
    """Drop the connection's remembered schema. Call after running SQL that
    sets search_path directly on a possibly-pooled connection (migrations,
    the test fixtures' retargeted DDL) so the next scoped checkout re-sends
    its SET instead of trusting a memo the raw SQL made false."""
    setattr(conn, _SCHEMA_ATTR, None)


def local_search_path_sql(schema: str) -> pgsql.Composed:
    """Transaction-scoped schema switch (``SET LOCAL``) for the rare
    multi-schema transaction. Reverts at COMMIT/ROLLBACK, so the
    connection's remembered schema stays truthful."""
    return pgsql.SQL("SET LOCAL search_path TO {}, public").format(pgsql.Identifier(schema))
