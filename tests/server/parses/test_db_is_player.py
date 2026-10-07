"""Tests for the Phase-2 is_player column + helpers in parses/db.py.

On Postgres the column and its index are part of the migrations-owned DDL
(db/migrations/0002_parses.sql) rather than an ALTER-style migration; the
idempotency guarantee survives as "re-running the migration runner is a
no-op". The helpers round-trip the classifier's output through the column.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend import pg_migrate
from backend.server.parses import db as parses_db
from tests.fixtures.pg import pg_conn


@pytest.fixture
def conn(parses_db_conn: Any) -> Any:
    """Historical name for the throwaway schema-scoped parses connection."""
    return parses_db_conn


def _insert_encounter(conn: Any) -> int:
    row = conn.execute(
        """
        INSERT INTO encounters (
            act_encid, title, zone, started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, source_dsn, ingested_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        ("abc123", "Test", "Zone", 1000, 1100, 100, 50000, 500.0, 1, 0, "test", 1100),
    ).fetchone()
    return int(row["id"]) if row else 0


def _insert_combatant(conn: Any, encounter_id: int, name: str) -> int:
    row = conn.execute(
        "INSERT INTO combatants (encounter_id, name, ally) VALUES (%s, %s, %s) RETURNING id",
        (encounter_id, name, 1),
    ).fetchone()
    return int(row["id"]) if row else 0


def test_is_player_column_exists(parses_db_path):
    with pg_conn(parses_db_path) as conn:
        rows = conn.execute(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = 'combatants'",
            (parses_db_path,),
        ).fetchall()
    cols = {r["column_name"]: r for r in rows}
    assert "is_player" in cols
    # Nullable with no non-null default — the lazy-backfill sentinel.
    assert cols["is_player"]["is_nullable"] == "YES"
    assert cols["is_player"]["column_default"] is None


def test_is_player_index_exists(parses_db_path):
    with pg_conn(parses_db_path) as conn:
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = 'combatants'",
            (parses_db_path,),
        ).fetchall()
    assert "idx_combatants_encounter_is_player" in {r["indexname"] for r in rows}


def test_migration_is_idempotent():
    """The SQLite "re-apply every ALTER, duplicates are no-ops" check becomes:
    the migration runner on an already-migrated database applies nothing."""
    assert pg_migrate.run() == []


def test_update_combatant_is_player_round_trip(conn):
    enc_id = _insert_encounter(conn)
    a = _insert_combatant(conn, enc_id, "Alpha")
    b = _insert_combatant(conn, enc_id, "Bravo")
    c = _insert_combatant(conn, enc_id, "Charlie")
    parses_db.store.update_combatant_is_player(conn, {a: True, b: False, c: True})
    rows = {r["id"]: r["is_player"] for r in conn.execute("SELECT id, is_player FROM combatants ORDER BY id")}
    assert rows[a] == 1
    assert rows[b] == 0
    assert rows[c] == 1


def test_update_combatant_is_player_overwrites_existing(conn):
    enc_id = _insert_encounter(conn)
    a = _insert_combatant(conn, enc_id, "Alpha")
    parses_db.store.update_combatant_is_player(conn, {a: True})
    parses_db.store.update_combatant_is_player(conn, {a: False})
    row = conn.execute("SELECT is_player FROM combatants WHERE id = %s", (a,)).fetchone()
    assert row["is_player"] == 0


def test_update_combatant_is_player_empty_dict_is_noop(conn):
    parses_db.store.update_combatant_is_player(conn, {})  # must not raise


def test_invalidate_is_player_cache_with_conn_sets_every_row_to_null(conn):
    enc_id = _insert_encounter(conn)
    a = _insert_combatant(conn, enc_id, "Alpha")
    b = _insert_combatant(conn, enc_id, "Bravo")
    parses_db.store.update_combatant_is_player(conn, {a: True, b: False})
    parses_db.store.invalidate_is_player_cache_with_conn(conn)
    rows = conn.execute("SELECT is_player FROM combatants").fetchall()
    assert all(r["is_player"] is None for r in rows), rows


def _insert_encounter_in_zone(conn: Any, encid: str, zone: str) -> int:
    row = conn.execute(
        """
        INSERT INTO encounters (
            act_encid, title, zone, started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, source_dsn, ingested_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (encid, "Test", zone, 1000, 1100, 100, 50000, 500.0, 1, 0, "test", 1100),
    ).fetchone()
    return int(row["id"]) if row else 0


def test_invalidate_is_player_cache_scoped_to_one_zone(conn):
    """A curator edit to one zone must not rewrite every combatant row in
    the table — only that zone's classified ally rows are reset."""
    enc_a = _insert_encounter_in_zone(conn, "zoneA001", "Sanctum of Fear")
    enc_b = _insert_encounter_in_zone(conn, "zoneB001", "Veeshan's Peak")
    a1 = _insert_combatant(conn, enc_a, "Alpha")
    a2 = _insert_combatant(conn, enc_a, "AlreadyNull")
    b1 = _insert_combatant(conn, enc_b, "Bravo")
    parses_db.store.update_combatant_is_player(conn, {a1: True, b1: True})

    reset = parses_db.store.invalidate_is_player_cache_with_conn(conn, "sanctum of fear")  # case-insensitive

    assert reset == 1  # a2 was already NULL, b1 is another zone
    rows = {r["id"]: r["is_player"] for r in conn.execute("SELECT id, is_player FROM combatants").fetchall()}
    assert rows[a1] is None and rows[a2] is None and rows[b1] == 1
