"""Pool checkout preamble: pooled app connections must carry the
idle-in-transaction guardrail (a camped transaction holds its locks and
starves maintenance DDL — the cutover's TRUNCATE sat behind two such
sessions). Direct (non-pool) connections stay unrestricted on purpose:
scripts legitimately pause mid-transaction while computing."""

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
    # a lock (the metrics size query sat 110 s on cutover DDL).
    with psycopg.connect(pg.dsn()) as conn:
        pg._configure_sync(conn)
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "30s"
        assert conn.execute("SHOW lock_timeout").fetchone()[0] == "10s"
