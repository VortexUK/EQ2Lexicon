-- SQL for backend/eq2db/classes.py. Schema + queries.
--
-- The committed classes.db (data/classes/classes.db) ships pre-populated;
-- init_db here only re-asserts the schema on a fresh / wiped file. No seeding.

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_classes
CREATE TABLE IF NOT EXISTS classes (
    name           TEXT PRIMARY KEY,
    archetype      TEXT    NOT NULL,
    subclass       TEXT,
    role           TEXT    NOT NULL,
    colour         TEXT    NOT NULL,
    display_order  INTEGER NOT NULL,
    icon_id        INTEGER NOT NULL
);

-- :name indexes_classes
CREATE INDEX IF NOT EXISTS idx_classes_archetype ON classes (archetype);
CREATE INDEX IF NOT EXISTS idx_classes_role ON classes (role);

-- ---------------------------------------------------------------------------
-- Read helpers
-- ---------------------------------------------------------------------------

-- :name list_all
SELECT * FROM classes ORDER BY display_order;

-- :name find_by_name
SELECT * FROM classes WHERE name = ?;
