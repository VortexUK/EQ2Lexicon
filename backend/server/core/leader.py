"""Single-leader lease over a Postgres session advisory lock.

Exactly one process may run the singleton duties — the Discord bot, the
retention sweeps, the pollers, the refresh-queue worker. A second instance,
or the overlapping container during a deploy, stays on standby and retries
every ``LEASE_RETRY_S`` until the holder's connection goes away: the lock is
session-level on a dedicated direct connection, so a crash releases it too.
Web traffic is unaffected — only the duties listed above are gated.

``LEADER_LEASE=0`` disables the lease (every process is leader); the test
provisioner sets it so parallel test lifespans never contend.
"""

from __future__ import annotations

import asyncio
import logging
import os

import psycopg

from backend import pg

_log = logging.getLogger(__name__)

#: Distinct from pg_migrate's lock id.
LEASE_KEY = 0x45513254  # "EQ2T"
LEASE_RETRY_S = 15.0

_conn: psycopg.Connection | None = None
_held = False


def _enabled() -> bool:
    return os.getenv("LEADER_LEASE", "1") not in ("0", "false", "no")


def is_leader() -> bool:
    return _held or not _enabled()


def try_acquire() -> bool:
    """One non-blocking attempt. Keeps the connection open while held."""
    global _conn, _held
    if not _enabled():
        return True
    if _held:
        return True
    try:
        if _conn is None or _conn.closed:
            _conn = psycopg.connect(pg.dsn(), autocommit=True, application_name="eq2lexicon-leader")
        row = _conn.execute("SELECT pg_try_advisory_lock(%s)", (LEASE_KEY,)).fetchone()
        _held = bool(row and row[0])
    except psycopg.Error as exc:
        _log.warning("[leader] lease attempt failed: %s", exc)
        _held = False
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None
    return _held


def release() -> None:
    global _conn, _held
    if _conn is not None:
        try:
            if _held:
                _conn.execute("SELECT pg_advisory_unlock(%s)", (LEASE_KEY,))
        except psycopg.Error:
            pass
        finally:
            try:
                _conn.close()
            finally:
                _conn = None
    _held = False


async def wait_until_leader(stop: asyncio.Event | None = None, *, retry_s: float = LEASE_RETRY_S) -> bool:
    """Block until this process holds the lease (True) or ``stop`` is set (False)."""
    announced = False
    while True:
        if await asyncio.to_thread(try_acquire):
            if announced:
                _log.info("[leader] lease acquired")
            return True
        if not announced:
            _log.info("[leader] another instance holds the lease — standing by (retry every %.0fs)", retry_s)
            announced = True
        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=retry_s)
                return False
            except TimeoutError:
                continue
        await asyncio.sleep(retry_s)
