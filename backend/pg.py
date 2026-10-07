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


@asynccontextmanager
async def aconnection() -> AsyncIterator[Any]:
    # Yields Any on purpose: psycopg's stubs demand LiteralString queries,
    # but the house pattern is named SQL blocks loaded from .sql sidecars.
    """Async checkout: pooled when the lifespan opened pools, else a
    short-lived direct connection (tests / scripts / pre-lifespan bot).
    psycopg commits on clean ``async with`` exit and rolls back on
    exception — explicit commits inside remain harmless."""
    if _async_pool is not None:
        async with _async_pool.connection() as conn:
            yield conn
    else:
        async with await psycopg.AsyncConnection.connect(dsn(), row_factory=dict_row) as conn:  # type: ignore[arg-type]
            yield conn


@contextmanager
def connection() -> Iterator[Any]:
    """Sync twin of :func:`aconnection`."""
    if _sync_pool is not None:
        with _sync_pool.connection() as conn:
            yield conn
    else:
        with psycopg.connect(dsn(), row_factory=dict_row) as conn:  # type: ignore[arg-type]
            yield conn


def getconn() -> Any:
    """Caller-owned sync checkout — ALWAYS pair with :func:`putconn`.
    Pooled when the lifespan opened pools, else a direct connection.
    For scoped work prefer the :func:`connection` contextmanager; this
    exists for the catalogue families' caller-owns-connection pattern
    (PgCatalogue.init_db in backend/db_catalogue.py)."""
    if _sync_pool is not None:
        return _sync_pool.getconn()
    return psycopg.connect(dsn(), row_factory=dict_row)  # type: ignore[arg-type]


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
    """The one statement stores run at checkout — composed so a schema
    name can never inject (test scratch schemas are generated names)."""
    return pgsql.SQL("SET search_path TO {}, public").format(pgsql.Identifier(schema))
