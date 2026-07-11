"""Tests for the Phase-2 is_player schema migration + helpers in parses/db.py.

The migration is idempotent (re-running init_db on a fresh or migrated DB
must not error). The helpers round-trip the classifier's output through
the column.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.server.parses import db as parses_db


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Fresh throwaway parses DB with the full schema applied."""
    c = parses_db.ParsesStore(tmp_path / "parses.db").init_db()
    try:
        yield c
    finally:
        c.close()


def _insert_encounter(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        INSERT INTO encounters (
            act_encid, title, zone, started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, source_dsn, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("abc123", "Test", "Zone", 1000, 1100, 100, 50000, 500.0, 1, 0, "test", 1100),
    )
    return int(cur.lastrowid or 0)


def _insert_combatant(conn: sqlite3.Connection, encounter_id: int, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO combatants (encounter_id, name, ally) VALUES (?, ?, ?)",
        (encounter_id, name, 1),
    )
    return int(cur.lastrowid or 0)


def test_is_player_column_exists(tmp_path: Path):
    conn = parses_db.ParsesStore(tmp_path / "parses.db").init_db()
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(combatants)")}
        assert "is_player" in cols
        # DEFAULT NULL allows the lazy-backfill sentinel.
        # PRAGMA table_info returns 'NULL' (string) for DEFAULT NULL added via
        # ALTER TABLE, and Python None for columns with no default clause —
        # either indicates the column is nullable with no non-null default.
        assert cols["is_player"][4] in (None, "NULL")
    finally:
        conn.close()


def test_is_player_index_exists(tmp_path: Path):
    conn = parses_db.ParsesStore(tmp_path / "parses.db").init_db()
    try:
        names = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='combatants'")
        }
        assert "idx_combatants_encounter_is_player" in names
    finally:
        conn.close()


def test_migration_is_idempotent():
    # Re-applying every migration on a fresh DB must not error (the
    # OperationalError swallow inside init_db handles duplicate-ALTER).
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(parses_db._CREATE_ENCOUNTERS)
        conn.execute(parses_db._CREATE_COMBATANTS)
        # First application — some may already be present in CREATE TABLE
        # (e.g. is_player is added by ALTER, not CREATE).
        for stmt in parses_db._MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        # Second application — every ALTER should now be a no-op.
        for stmt in parses_db._MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # idempotency: duplicate ALTER must be a no-op
        cols = {r[1] for r in conn.execute("PRAGMA table_info(combatants)")}
        assert "is_player" in cols
    finally:
        conn.close()


def test_update_combatant_is_player_round_trip(conn):
    enc_id = _insert_encounter(conn)
    a = _insert_combatant(conn, enc_id, "Alpha")
    b = _insert_combatant(conn, enc_id, "Bravo")
    c = _insert_combatant(conn, enc_id, "Charlie")
    parses_db.store.update_combatant_is_player(conn, {a: True, b: False, c: True})
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, is_player FROM combatants ORDER BY id")}
    assert rows[a] == 1
    assert rows[b] == 0
    assert rows[c] == 1


def test_update_combatant_is_player_overwrites_existing(conn):
    enc_id = _insert_encounter(conn)
    a = _insert_combatant(conn, enc_id, "Alpha")
    parses_db.store.update_combatant_is_player(conn, {a: True})
    parses_db.store.update_combatant_is_player(conn, {a: False})
    row = conn.execute("SELECT is_player FROM combatants WHERE id = ?", (a,)).fetchone()
    assert row[0] == 0


def test_update_combatant_is_player_empty_dict_is_noop(conn):
    parses_db.store.update_combatant_is_player(conn, {})  # must not raise


def test_invalidate_is_player_cache_with_conn_sets_every_row_to_null(conn):
    enc_id = _insert_encounter(conn)
    a = _insert_combatant(conn, enc_id, "Alpha")
    b = _insert_combatant(conn, enc_id, "Bravo")
    parses_db.store.update_combatant_is_player(conn, {a: True, b: False})
    parses_db.store.invalidate_is_player_cache_with_conn(conn)
    rows = conn.execute("SELECT is_player FROM combatants").fetchall()
    assert all(r[0] is None for r in rows), rows
