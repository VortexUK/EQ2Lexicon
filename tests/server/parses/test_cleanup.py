"""Tests for the parses retention sweep (backend/server/parses/cleanup.py).

Encounters are seeded directly via SQL (no combatants): two same-fight uploads
with empty top-N ally sets trivially merge under _group_into_fights, so the
mirror-grouping path is exercised without building full combatant rows.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.server.parses import cleanup as parse_cleanup
from backend.server.parses import db as parses_db

# Fixed clock so the test never touches wall-time. cutoff at 3 days = NOW-259200.
NOW = 2_000_000_000
DAY = 86_400


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Temp parses DB wired into both the seeding conn and the sweep (via
    DB_PATH). Returns the seed connection."""
    db_path = tmp_path / "parses.db"
    monkeypatch.setattr(parses_db, "DB_PATH", db_path)
    conn = parses_db.init_db(db_path)
    yield conn
    conn.close()


def _insert(
    conn: sqlite3.Connection,
    *,
    title: str,
    started_at: int,
    uploaded_by: str,
    world: str = "Varsoon",
    duration_s: int = 60,
    success_level: int = 1,
    hidden_at: int | None = None,
    guild_name: str | None = "Exordium",
    zone: str = "Castle Mistmoore",
) -> int:
    cur = conn.execute(
        "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, "
        "duration_s, success_level, source_dsn, uploaded_by, guild_name, ingested_at, hidden_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'eq2act', ?, ?, ?, ?)",
        (
            world,
            f"enc-{world}-{started_at}-{uploaded_by}",
            title,
            zone,
            started_at,
            started_at + duration_s,
            duration_s,
            success_level,
            uploaded_by,
            guild_name,
            started_at,
            hidden_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def _ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT id FROM encounters").fetchall()}


def test_trash_deleted_after_cutoff_recent_survives(seeded_db):
    old_trash = _insert(seeded_db, title="a krait patriarch", started_at=NOW - 10 * DAY, uploaded_by="A")
    recent_trash = _insert(seeded_db, title="a hill giant", started_at=NOW - 1 * DAY, uploaded_by="A")

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    assert result["trash_deleted"] == 1
    surviving = _ids(seeded_db)
    assert old_trash not in surviving
    assert recent_trash in surviving


def test_boss_single_upload_survives(seeded_db):
    boss = _insert(seeded_db, title="Captain Krasniv", started_at=NOW - 10 * DAY, uploaded_by="A")

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    assert result["dup_uploads_deleted"] == 0
    assert boss in _ids(seeded_db)


def test_boss_duplicate_uploads_collapse_to_longest_primary(seeded_db):
    base = NOW - 10 * DAY
    # Same fight, two raiders, starts within the 60s mirror window, both wins.
    short = _insert(seeded_db, title="Captain Krasniv", started_at=base, uploaded_by="RaiderA", duration_s=60)
    longer = _insert(seeded_db, title="Captain Krasniv", started_at=base + 30, uploaded_by="RaiderB", duration_s=90)

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    assert result["dup_uploads_deleted"] == 1
    surviving = _ids(seeded_db)
    assert longer in surviving  # primary (longest) kept — rankings link holds
    assert short not in surviving  # duplicate cleared


def test_hidden_non_primary_upload_not_deleted(seeded_db):
    base = NOW - 10 * DAY
    longer = _insert(seeded_db, title="Captain Krasniv", started_at=base, uploaded_by="RaiderA", duration_s=90)
    hidden_short = _insert(
        seeded_db,
        title="Captain Krasniv",
        started_at=base + 30,
        uploaded_by="RaiderB",
        duration_s=60,
        hidden_at=NOW - 5 * DAY,
    )

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    assert result["dup_uploads_deleted"] == 0  # the only non-primary is soft-deleted
    surviving = _ids(seeded_db)
    assert longer in surviving
    assert hidden_short in surviving  # manual soft-delete preserved


def test_winning_primary_preserved_when_longest_is_a_loss(seeded_db):
    base = NOW - 10 * DAY
    # Longest upload is a LOSS; a shorter upload is the WIN rankings would link.
    long_loss = _insert(
        seeded_db,
        title="Captain Krasniv",
        started_at=base,
        uploaded_by="RaiderA",
        duration_s=120,
        success_level=2,
    )
    short_win = _insert(
        seeded_db,
        title="Captain Krasniv",
        started_at=base + 30,
        uploaded_by="RaiderB",
        duration_s=60,
        success_level=1,
    )

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    # Both kept: canonical (longest) AND the winning primary the leaderboard links.
    assert result["dup_uploads_deleted"] == 0
    surviving = _ids(seeded_db)
    assert long_loss in surviving
    assert short_win in surviving


def test_worlds_processed_independently(seeded_db):
    base = NOW - 10 * DAY
    v_short = _insert(
        seeded_db, title="Captain Krasniv", started_at=base, uploaded_by="VA", world="Varsoon", duration_s=60
    )
    v_long = _insert(
        seeded_db, title="Captain Krasniv", started_at=base + 30, uploaded_by="VB", world="Varsoon", duration_s=90
    )
    # Same boss/guild on a different world must NOT merge with Varsoon's uploads.
    k_only = _insert(
        seeded_db, title="Captain Krasniv", started_at=base, uploaded_by="KA", world="Kaladim", duration_s=70
    )

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    assert result["dup_uploads_deleted"] == 1  # only Varsoon's shorter dup
    surviving = _ids(seeded_db)
    assert v_long in surviving
    assert v_short not in surviving
    assert k_only in surviving  # untouched — single upload on its own world


def test_nothing_deleted_inside_retention_window(seeded_db):
    # Everything is recent (inside 3 days) → no-op.
    _insert(seeded_db, title="a krait patriarch", started_at=NOW - 1 * DAY, uploaded_by="A")
    _insert(seeded_db, title="Captain Krasniv", started_at=NOW - 1 * DAY, uploaded_by="RaiderA", duration_s=60)
    _insert(seeded_db, title="Captain Krasniv", started_at=NOW - 1 * DAY + 30, uploaded_by="RaiderB", duration_s=90)

    result = parse_cleanup.run_parse_cleanup(now=NOW, retention_days=3)

    assert result == {"trash_deleted": 0, "dup_uploads_deleted": 0}
    assert len(_ids(seeded_db)) == 3
