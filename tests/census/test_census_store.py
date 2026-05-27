from __future__ import annotations

from census import census_store as cs


def test_init_db_creates_tables(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"characters", "guilds"} <= tables
        char_cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)")}
        assert {
            "name_lower",
            "world",
            "name",
            "level",
            "guild_name",
            "data_json",
            "last_resolved_at",
            "updated_at",
        } <= char_cols
    finally:
        conn.close()
