-- DDL for the shared `_meta` (key/value provenance) table. Every reference
-- DB built/maintained under backend/eq2db/ carries one — the schema is
-- identical, so each module's init_db() calls _meta.create_table(conn)
-- instead of redefining `_CREATE_META = """CREATE TABLE _meta ..."""`.

-- :name create_table
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- :name select_value
SELECT value FROM _meta WHERE key = ?

-- :name upsert
INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)
