"""Voice-attendance retention: Discord ids of channel members are swept after
VOICE_OBSERVATION_RETENTION_DAYS; raid/online character rows are untouched."""

from __future__ import annotations

import pytest

from backend.server.constants import VOICE_OBSERVATION_RETENTION_DAYS
from backend.server.db.attendance import store as attendance_store
from tests.fixtures.pg import pg_conn

NOW = 1_800_000_000
DAY = 86400


@pytest.fixture(autouse=True)
def users_db(users_schema: str) -> str:
    """Isolated leased schema per test (conftest ``users_schema``), aliased
    so tests can keep naming it ``users_db``."""
    return users_schema


def _seed(schema: str) -> None:
    with pg_conn(schema) as c:
        c.execute(
            "INSERT INTO attendance_sessions (world, guild_name, session_day, seq, started_at, ended_at) "
            "VALUES ('Varsoon', 'Exordium', '2026-01-01', 0, %s, %s)",
            (NOW - 200 * DAY, NOW - 200 * DAY + 3600),
        )
        sid = c.execute("SELECT id FROM attendance_sessions").fetchone()["id"]
        rows = [
            ("111", "voice", NOW - 200 * DAY),  # stale voice → pruned
            ("222", "voice", NOW - 10 * DAY),  # recent voice → kept
            ("Sihtric", "raid", NOW - 200 * DAY),  # character row → never pruned
            ("Alt", "online", NOW - 200 * DAY),
        ]
        for name, kind, seen in rows:
            c.execute(
                "INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, name, kind, seen, seen),
            )


async def test_prune_removes_only_stale_voice_rows(users_db):
    _seed(users_db)
    pruned = await attendance_store.prune_voice_observations(older_than_days=VOICE_OBSERVATION_RETENTION_DAYS, now=NOW)
    assert pruned == 1
    with pg_conn(users_db) as c:
        remaining = sorted(
            (r["character_name"], r["kind"])
            for r in c.execute("SELECT character_name, kind FROM attendance_observations").fetchall()
        )
    assert remaining == [("222", "voice"), ("Alt", "online"), ("Sihtric", "raid")]
    # Second sweep is a no-op.
    assert (
        await attendance_store.prune_voice_observations(older_than_days=VOICE_OBSERVATION_RETENTION_DAYS, now=NOW) == 0
    )


def test_retention_constant_is_the_policy_number():
    assert VOICE_OBSERVATION_RETENTION_DAYS == 90
