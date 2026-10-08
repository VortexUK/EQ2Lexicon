"""Pool checkout preamble: pooled app connections must carry the
idle-in-transaction guardrail (a camped transaction holds its locks and
starves maintenance DDL). Direct (non-pool) connections stay unrestricted on
purpose: scripts legitimately pause mid-transaction while computing."""

import psycopg
import pytest

from backend import pg


def test_configure_sync_sets_idle_txn_timeout():
    with psycopg.connect(pg.dsn()) as conn:
        pg._configure_sync(conn)
        val = conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0]
        assert val == "2min"


@pytest.mark.asyncio
async def test_configure_async_sets_idle_txn_timeout():
    async with await psycopg.AsyncConnection.connect(pg.dsn()) as conn:
        await pg._configure_async(conn)
        cur = await conn.execute("SHOW idle_in_transaction_session_timeout")
        row = await cur.fetchone()
        assert row[0] == "2min"


def test_direct_connections_keep_server_default():
    # The guardrail is pool-only: a plain connect must NOT inherit it.
    with psycopg.connect(pg.dsn()) as conn:
        val = conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0]
        assert val != "2min"


def test_configure_sets_statement_and_lock_timeouts():
    # Request-path statements fail fast instead of camping a pool slot behind
    # a lock.
    with psycopg.connect(pg.dsn()) as conn:
        pg._configure_sync(conn)
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "30s"
        assert conn.execute("SHOW lock_timeout").fetchone()[0] == "10s"


# ── Schema selection: sticky per connection, session-level, autocommit reads ──
# One pooled read used to cost BEGIN + SET search_path + query + COMMIT. The
# checkout helpers now remember the schema per physical connection and run
# the SET session-level (never reverted by a rollback), and `autocommit=True`
# turns a single-statement read into one round trip.


@pytest.fixture
def sync_pool(monkeypatch):
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    # max_size=1 so every checkout below is provably the same physical connection.
    pool = ConnectionPool(
        pg.dsn(), min_size=1, max_size=1, kwargs={"row_factory": dict_row}, configure=pg._configure_sync
    )
    monkeypatch.setattr(pg, "_sync_pool", pool)
    yield pool
    pool.close()


def _search_path(conn) -> str:
    return conn.execute("SHOW search_path").fetchone()["search_path"]


def test_schema_is_sticky_and_survives_rollback(sync_pool):
    with pg.connection("users") as conn:
        assert _search_path(conn) == "users, public"
        first = conn
    with pg.connection("users") as conn:
        assert conn is first
        assert getattr(conn, pg._SCHEMA_ATTR) == "users"
        conn.execute("SELECT 1")  # opens the implicit transaction …
        conn.rollback()  # … whose rollback must NOT revert the schema
        assert _search_path(conn) == "users, public"
    with pg.connection("parses") as conn:
        assert conn is first
        assert _search_path(conn) == "parses, public"
        assert getattr(conn, pg._SCHEMA_ATTR) == "parses"


def test_autocommit_read_is_transactionless_and_resets(sync_pool):
    from psycopg.pq import TransactionStatus

    with pg.connection("users", autocommit=True) as conn:
        assert conn.autocommit is True
        conn.execute("SELECT 1").fetchone()
        assert conn.info.transaction_status == TransactionStatus.IDLE
    with pg.connection("users") as conn:
        assert conn.autocommit is False  # the flag never leaks into the next checkout


def test_getconn_applies_schema(sync_pool):
    conn = pg.getconn("raids")
    try:
        assert _search_path(conn) == "raids, public"
    finally:
        pg.putconn(conn)


def test_local_search_path_reverts_at_commit(sync_pool):
    # The multi-schema erasure transaction switches with SET LOCAL so the
    # connection's remembered schema is still true afterwards.
    with pg.connection("users") as conn:
        conn.execute(pg.local_search_path_sql("parses"))
        assert _search_path(conn) == "parses, public"
        conn.commit()
        assert _search_path(conn) == "users, public"
        assert getattr(conn, pg._SCHEMA_ATTR) == "users"


@pytest.mark.asyncio
async def test_async_schema_sticky_and_autocommit_resets():
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        pg.dsn(), min_size=1, max_size=1, open=False, kwargs={"row_factory": dict_row}, configure=pg._configure_async
    )
    await pool.open()
    previous = pg._async_pool
    pg._async_pool = pool
    try:
        async with pg.aconnection("users", autocommit=True) as conn:
            assert conn.autocommit is True
            cur = await conn.execute("SHOW search_path")
            assert (await cur.fetchone())["search_path"] == "users, public"
            first = conn
        async with pg.aconnection("users") as conn:
            assert conn is first
            assert conn.autocommit is False
            assert getattr(conn, pg._SCHEMA_ATTR) == "users"
        async with pg.aconnection("zones") as conn:
            cur = await conn.execute("SHOW search_path")
            assert (await cur.fetchone())["search_path"] == "zones, public"
    finally:
        pg._async_pool = previous
        await pool.close()
