from __future__ import annotations

from web import db


def test_servers_seeded_and_lookups(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    rows = db.list_servers_sync(p)
    worlds = {r["world"] for r in rows}
    assert {"Varsoon", "Wuoshi"} <= worlds
    v = db.get_server_by_subdomain_sync("varsoon", p)
    assert v is not None and v["world"] == "Varsoon"
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w is not None and w["subdomain"] == "wuoshi"
    assert db.get_server_by_subdomain_sync("nope", p) is None


def test_upsert_server_updates_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    db.upsert_server_settings_sync(
        "Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt="2026-07-01T18:00:00Z", path=p
    )
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w["max_level"] == 70
    assert w["current_xpac"] == "Sentinel's Fate"
    assert w["launch_dt"] == "2026-07-01T18:00:00Z"


def test_second_init_db_preserves_upserted_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    db.upsert_server_settings_sync("Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt=None, path=p)
    # A second init_db (e.g. container restart) must not reset the admin edit.
    db.init_db(p)
    w = db.get_server_by_world_sync("Wuoshi", p)
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
