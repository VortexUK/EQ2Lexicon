"""Shared Postgres connection plumbing for the migrated store families.

One database, five schemas (users / parses / census / zones / raids —
see db/migrations/). Stores select their schema via ``SET search_path``
at connection checkout, so every query stays unqualified.

Pool-if-open-else-direct: the app lifespan opens one async + one sync
pool (`open_pools`) and every checkout goes through them; when no pool
is open — pytest (fresh event loop per test), scripts, or the bot's
startup racing the web lifespan — the same contextmanagers fall back to
a short-lived direct connection. Tests therefore never share a
loop-bound pool, and nothing outside the lifespan needs pool wiring.

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
#: canonical name (Railway); the others are historical/dashboard-copied.
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


async def open_pools() -> None:
    """Open the shared pools — called once from the app lifespan startup.
    Small on purpose: WEB_CONCURRENCY=1 and web+bot share one process, so
    ~10 server connections total stays well inside Supabase's session-
    pooler limits."""
    global _async_pool, _sync_pool
    _async_pool = AsyncConnectionPool(dsn(), min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row})
    await _async_pool.open()
    _sync_pool = ConnectionPool(dsn(), min_size=1, max_size=5, kwargs={"row_factory": dict_row})


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
