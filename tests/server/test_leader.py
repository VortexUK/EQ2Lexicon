"""The leader lease: one holder at a time over a session advisory lock."""

from __future__ import annotations

import psycopg

from backend import pg
from backend.server.core import leader


def test_lease_is_exclusive_and_released(monkeypatch):
    monkeypatch.setenv("LEADER_LEASE", "1")
    leader.release()
    assert leader.try_acquire() is True
    assert leader.is_leader() is True

    # A second session cannot take it while held…
    with psycopg.connect(pg.dsn(), autocommit=True) as other:
        taken = other.execute("SELECT pg_try_advisory_lock(%s)", (leader.LEASE_KEY,)).fetchone()[0]
        assert taken is False
        # …and can once released.
        leader.release()
        assert leader.is_leader() is False
        taken = other.execute("SELECT pg_try_advisory_lock(%s)", (leader.LEASE_KEY,)).fetchone()[0]
        assert taken is True
        assert leader.try_acquire() is False  # now the OTHER session holds it
        other.execute("SELECT pg_advisory_unlock(%s)", (leader.LEASE_KEY,))
    leader.release()


def test_disabled_lease_makes_every_process_leader(monkeypatch):
    monkeypatch.setenv("LEADER_LEASE", "0")
    assert leader.try_acquire() is True
    assert leader.is_leader() is True
