"""Memory [[test-migrations-against-old-db-shape]]: init_db must succeed
on a pre-migration-shape DB, not just a fresh one. The repo has crashed
in prod before because a column-dependent index in _SCHEMA referenced a
column not yet added on an existing DB.

This test seeds a v1-shape DB by hand, then runs init_db and asserts
every modern column + index is present afterwards.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.server import db as users_db
from backend.server.db._assertions import assert_schema_complete


@pytest.fixture
def v1_db(tmp_path: Path) -> Path:
    """A user-table-only DB that mirrors the original schema BEFORE any
    of the column-add migrations (users.access_status,
    character_claims.world, character_claims.is_primary, etc.)."""
    db = tmp_path / "users.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE users (
                discord_id TEXT PRIMARY KEY,
                discord_name TEXT NOT NULL,
                avatar TEXT,
                first_seen INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_seen INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE character_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                character_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            INSERT INTO users (discord_id, discord_name) VALUES ('123', 'OldUser');
            INSERT INTO character_claims (discord_id, character_name) VALUES ('123', 'Vortex');
        """)
    return db


def test_init_db_migrates_v1_to_current(v1_db: Path) -> None:
    """The v1-shape DB should upgrade cleanly to the current schema."""
    users_db.init_db(v1_db)

    with sqlite3.connect(v1_db) as conn:
        # Modern columns exist on users.
        users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "access_status" in users_cols
        assert "discord_username" in users_cols

        # Modern columns exist on character_claims.
        claims_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
        assert "world" in claims_cols
        assert "is_primary" in claims_cols

        # The column-dependent indexes exist.
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(character_claims)")}
        assert "idx_claims_world" in indexes
        assert "idx_claims_primary" in indexes

        # BE-073: access_status index exists on users.
        user_indexes = {row[1] for row in conn.execute("PRAGMA index_list(users)")}
        assert "idx_users_access" in user_indexes

        # Existing data survived.
        assert conn.execute("SELECT discord_name FROM users WHERE discord_id='123'").fetchone()[0] == "OldUser"


def test_init_db_on_fresh_db(tmp_path: Path) -> None:
    """The fresh-DB path also works (no pre-existing tables)."""
    db = tmp_path / "users.db"
    users_db.init_db(db)
    with sqlite3.connect(db) as conn:
        # Both old and modern columns present.
        users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "access_status" in users_cols
        assert "discord_username" in users_cols

        claims_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
        assert "world" in claims_cols
        assert "is_primary" in claims_cols

        # Indexes exist on fresh DB.
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(character_claims)")}
        assert "idx_claims_world" in indexes
        assert "idx_claims_primary" in indexes

        user_indexes = {row[1] for row in conn.execute("PRAGMA index_list(users)")}
        assert "idx_users_access" in user_indexes


def test_init_db_idempotent(v1_db: Path) -> None:
    """Running init_db twice in a row is safe — no double-ALTER errors."""
    users_db.init_db(v1_db)
    users_db.init_db(v1_db)  # must not raise


# ---------------------------------------------------------------------------
# BE-070: schema-vs-migrations drift assertion
# ---------------------------------------------------------------------------


def test_assert_schema_complete_passes_on_current_db(tmp_path: Path) -> None:
    """assert_schema_complete must not raise after init_db produces a fully
    migrated DB — every column in SCHEMA should be present."""
    db = tmp_path / "users.db"
    users_db.init_db(db)
    with sqlite3.connect(db) as conn:
        assert_schema_complete(conn)  # must not raise


def test_assert_schema_complete_fires_on_missing_column(tmp_path: Path) -> None:
    """If SCHEMA declares a column that's absent from the live DB, the
    assertion should fire immediately — rather than silently failing at
    runtime when the column is first accessed.

    We simulate the drift by patching SCHEMA to include a fake column that
    the migration never adds, then calling assert_schema_complete against a
    freshly initialised (un-drifted) DB."""
    db = tmp_path / "users.db"
    users_db.init_db(db)

    # Inject a fake column into SCHEMA's users table definition.
    import backend.server.db.schema as _schema_mod

    drifted_schema = _schema_mod.SCHEMA.replace(
        "CREATE TABLE IF NOT EXISTS users (",
        "CREATE TABLE IF NOT EXISTS users (\n    _fake_drift_column TEXT,",
    )
    with (
        patch.object(_schema_mod, "SCHEMA", drifted_schema),
        patch("backend.server.db._assertions.SCHEMA", drifted_schema),
    ):
        with sqlite3.connect(db) as conn:
            with pytest.raises(AssertionError, match="_fake_drift_column"):
                assert_schema_complete(conn)
