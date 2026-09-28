"""Voice-attendance retention: Discord ids of channel members are swept after
VOICE_OBSERVATION_RETENTION_DAYS; raid/online character rows are untouched."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.server.constants import VOICE_OBSERVATION_RETENTION_DAYS
from backend.server.db import init_db
from backend.server.db.attendance import store as attendance_store
from tests.fixtures.users_db import point_users_db_at

NOW = 1_800_000_000
DAY = 86400


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)


def _seed(db: Path) -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO attendance_sessions (world, guild_name, session_day, seq, started_at, ended_at) "
            "VALUES ('Varsoon', 'Exordium', '2026-01-01', 0, ?, ?)",
            (NOW - 200 * DAY, NOW - 200 * DAY + 3600),
        )
        sid = c.execute("SELECT id FROM attendance_sessions").fetchone()[0]
        rows = [
            ("111", "voice", NOW - 200 * DAY),  # stale voice → pruned
            ("222", "voice", NOW - 10 * DAY),  # recent voice → kept
            ("Sihtric", "raid", NOW - 200 * DAY),  # character row → never pruned
            ("Alt", "online", NOW - 200 * DAY),
        ]
        for name, kind, seen in rows:
            c.execute(
                "INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, name, kind, seen, seen),
            )


async def test_prune_removes_only_stale_voice_rows(users_db):
    _seed(users_db)
    pruned = await attendance_store.prune_voice_observations(older_than_days=VOICE_OBSERVATION_RETENTION_DAYS, now=NOW)
    assert pruned == 1
    with sqlite3.connect(users_db) as c:
        remaining = sorted(c.execute("SELECT character_name, kind FROM attendance_observations").fetchall())
    assert remaining == [("222", "voice"), ("Alt", "online"), ("Sihtric", "raid")]
    # Second sweep is a no-op.
    assert (
        await attendance_store.prune_voice_observations(older_than_days=VOICE_OBSERVATION_RETENTION_DAYS, now=NOW) == 0
    )


def test_retention_constant_is_the_policy_number():
    assert VOICE_OBSERVATION_RETENTION_DAYS == 90
