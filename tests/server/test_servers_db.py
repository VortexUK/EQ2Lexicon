from __future__ import annotations

import sqlite3

from backend.server import db
from backend.server.db.servers import ServersStore


def test_servers_seeded_and_lookups(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    rows = ServersStore(p).list_servers_sync()
    worlds = {r["world"] for r in rows}
    assert {"Varsoon", "Wuoshi"} <= worlds
    v = ServersStore(p).get_server_by_subdomain_sync("varsoon")
    assert v is not None and v["world"] == "Varsoon"
    w = ServersStore(p).get_server_by_world_sync("Wuoshi")
    assert w is not None and w["subdomain"] == "wuoshi"
    assert ServersStore(p).get_server_by_subdomain_sync("nope") is None


def test_upsert_server_updates_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    ServersStore(p).upsert_server_settings_sync(
        "Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt="2026-07-01T18:00:00Z"
    )
    w = ServersStore(p).get_server_by_world_sync("Wuoshi")
    assert w["max_level"] == 70
    assert w["current_xpac"] == "Sentinel's Fate"
    assert w["launch_dt"] == "2026-07-01T18:00:00Z"


def test_second_init_db_preserves_upserted_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    ServersStore(p).upsert_server_settings_sync("Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt=None)
    # A second init_db (e.g. container restart) must not reset the admin edit.
    db.init_db(p)
    w = ServersStore(p).get_server_by_world_sync("Wuoshi")
    assert w["max_level"] == 70
    assert w["current_xpac"] == "Sentinel's Fate"


def test_init_db_migrates_legacy_db_without_world_columns(tmp_path):
    """Regression: init_db must not crash on a pre-per-server users.db whose
    character_claims / item_watch lack the `world` column.

    The bug: the idx_claims_world index lived in _SCHEMA (run by executescript
    BEFORE the ALTER that adds `world`), so on an existing DB it raised
    'no such column: world' and crashed app startup -> whole site down.
    """
    import sqlite3

    p = tmp_path / "users.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE users (
            discord_id TEXT PRIMARY KEY, discord_name TEXT NOT NULL,
            discord_username TEXT, avatar TEXT,
            first_seen INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            last_seen INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            access_status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE character_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL, character_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            reviewed_at INTEGER, reviewed_by TEXT, note TEXT
        );
        CREATE TABLE item_watch (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_name TEXT NOT NULL, character_name TEXT NOT NULL,
            item_id INTEGER NOT NULL, item_name TEXT NOT NULL,
            added_by TEXT NOT NULL, added_by_name TEXT NOT NULL,
            added_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            first_seen_at INTEGER, last_seen_at INTEGER, last_checked_at INTEGER,
            UNIQUE(guild_name, character_name, item_id)
        );
        INSERT INTO users (discord_id, discord_name) VALUES ('u1', 'U1');
        INSERT INTO character_claims (discord_id, character_name, status)
            VALUES ('u1', 'Sihtric', 'approved');
        INSERT INTO item_watch (guild_name, character_name, item_id, item_name, added_by, added_by_name)
            VALUES ('Exordium', 'Sihtric', 42, 'Sword', 'u1', 'U1');
        """
    )
    conn.commit()
    conn.close()

    # Must NOT raise (this is the exact crash that took the site down).
    db.init_db(p)

    conn = sqlite3.connect(p)
    try:
        claims_cols = {r[1] for r in conn.execute("PRAGMA table_info(character_claims)")}
        assert "world" in claims_cols and "is_primary" in claims_cols
        watch_cols = {r[1] for r in conn.execute("PRAGMA table_info(item_watch)")}
        assert "world" in watch_cols
        # Legacy rows backfilled to Varsoon.
        assert (
            conn.execute("SELECT world FROM character_claims WHERE character_name='Sihtric'").fetchone()[0] == "Varsoon"
        )
        assert conn.execute("SELECT world FROM item_watch WHERE item_id=42").fetchone()[0] == "Varsoon"
        # The world index now exists.
        idx = {r[1] for r in conn.execute("PRAGMA index_list(character_claims)")}
        assert "idx_claims_world" in idx
    finally:
        conn.close()

    # Idempotent: a second boot on the migrated DB is a clean no-op.
    db.init_db(p)


# ---------------------------------------------------------------------------
# is_default migration tests
# ---------------------------------------------------------------------------


def test_init_db_adds_is_default_to_legacy_servers_table(monkeypatch, tmp_path):
    """Regression: init_db must not crash against a prod servers table that has NO
    is_default column, must add the column, and must ensure exactly one row has
    is_default=1 afterwards.  A second init_db call must be idempotent.
    """
    import os

    # Simulate the production DB shape: servers table WITHOUT is_default.
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE servers (
            world        TEXT PRIMARY KEY,
            subdomain    TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            max_level    INTEGER NOT NULL,
            current_xpac TEXT,
            launch_dt    TEXT,
            updated_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        INSERT INTO servers (world, subdomain, display_name, max_level)
            VALUES ('Varsoon', 'varsoon', 'Varsoon', 50);
        INSERT INTO servers (world, subdomain, display_name, max_level)
            VALUES ('Wuoshi', 'wuoshi', 'Wuoshi', 50);
        """
    )
    conn.commit()
    conn.close()

    # Ensure EQ2_WORLD resolves to Varsoon (the default env).
    monkeypatch.setenv("EQ2_WORLD", "Varsoon")

    # Must NOT raise.
    db.init_db(p)

    # Column now exists and exactly one row has is_default=1.
    conn = sqlite3.connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(servers)")}
        assert "is_default" in cols, "is_default column must be added by migration"

        defaults = conn.execute("SELECT world FROM servers WHERE is_default = 1").fetchall()
        assert len(defaults) == 1, f"Expected exactly 1 default, got {defaults}"
        assert defaults[0][0] == "Varsoon", "EQ2_WORLD server should be the default"
    finally:
        conn.close()

    # Second init_db must be idempotent — still exactly one default.
    db.init_db(p)

    conn = sqlite3.connect(p)
    try:
        defaults = conn.execute("SELECT world FROM servers WHERE is_default = 1").fetchall()
        assert len(defaults) == 1, f"After second init_db, expected 1 default, got {defaults}"
    finally:
        conn.close()


def test_set_default_server_clears_others(tmp_path):
    """set_default_server_sync atomically swaps the default between servers."""
    p = tmp_path / "users.db"
    db.init_db(p)

    # After init the default should be Varsoon (EQ2_WORLD default).
    rows = ServersStore(p).list_servers_sync()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1

    # Set Wuoshi as default — Varsoon must flip to 0.
    result = ServersStore(p).set_default_server_sync("Wuoshi")
    assert result is True

    rows = ServersStore(p).list_servers_sync()
    wuoshi = next(r for r in rows if r["world"] == "Wuoshi")
    varsoon = next(r for r in rows if r["world"] == "Varsoon")
    assert wuoshi["is_default"] is True
    assert varsoon["is_default"] is False

    # Flip back to Varsoon.
    result = ServersStore(p).set_default_server_sync("Varsoon")
    assert result is True

    rows = ServersStore(p).list_servers_sync()
    wuoshi = next(r for r in rows if r["world"] == "Wuoshi")
    varsoon = next(r for r in rows if r["world"] == "Varsoon")
    assert varsoon["is_default"] is True
    assert wuoshi["is_default"] is False

    # Unknown world: returns False and does NOT leave zero defaults.
    result = ServersStore(p).set_default_server_sync("Nope")
    assert result is False
    rows = ServersStore(p).list_servers_sync()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1, "Must always have exactly one default even after failed set"
