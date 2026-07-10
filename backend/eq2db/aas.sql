-- SQL for backend/eq2db/aas.py. Schema + DML both live here. The shared
-- `_meta` table is created from backend/eq2db/_meta.sql via _meta.create_table().
--
-- aas.db condenses data/AAs/trees/{id}.json (157 files, ~3 MB) into two
-- tables. tree_type and max_points are PRECOMPUTED at build time (see
-- scripts/build_aas_db.py) so the runtime never re-runs the structural
-- detect_tree_type heuristic or the Σ(maxtier × points_per_tier) sweep.

-- :name schema_aa_trees
CREATE TABLE IF NOT EXISTS aa_trees (
    id                       INTEGER PRIMARY KEY,          -- census tree id (JSON filename stem)
    name                     TEXT    NOT NULL,             -- e.g. "Cleric"
    tree_type                TEXT    NOT NULL,             -- class | subclass | shadows | heroic | ...
    max_points               INTEGER NOT NULL DEFAULT 0,   -- Σ maxtier × points_per_tier over its nodes
    is_warder_tree           INTEGER NOT NULL DEFAULT 0,
    maximum_points           INTEGER NOT NULL DEFAULT 0,
    minimum_points_required  INTEGER NOT NULL DEFAULT 0,
    ofx_classification       TEXT,
    ofy_classification       TEXT,
    version                  INTEGER
);

-- :name schema_aa_nodes
CREATE TABLE IF NOT EXISTS aa_nodes (
    tree_id                         INTEGER NOT NULL REFERENCES aa_trees(id) ON DELETE CASCADE,
    node_id                         INTEGER NOT NULL,
    name                            TEXT    NOT NULL DEFAULT '',
    description                     TEXT    NOT NULL DEFAULT '',
    classification                  TEXT    NOT NULL DEFAULT '',
    node_group                      TEXT    NOT NULL DEFAULT '',
    title                           TEXT    NOT NULL DEFAULT '',
    title_level                     INTEGER NOT NULL DEFAULT 0,
    xcoord                          INTEGER NOT NULL,
    ycoord                          INTEGER NOT NULL,
    icon_id                         INTEGER NOT NULL DEFAULT 0,
    icon_backdrop                   INTEGER NOT NULL DEFAULT -1,
    maxtier                         INTEGER NOT NULL DEFAULT 1,
    points_per_tier                 INTEGER NOT NULL DEFAULT 1,
    min_level                       INTEGER NOT NULL DEFAULT 1,
    spellcrc                        INTEGER NOT NULL DEFAULT 0,
    points_to_unlock                INTEGER NOT NULL DEFAULT 0,
    points_global_to_unlock         INTEGER NOT NULL DEFAULT 0,
    classification_points_required  INTEGER NOT NULL DEFAULT 0,
    first_parent_id                 INTEGER,
    first_parent_required_tier      INTEGER,
    PRIMARY KEY (tree_id, node_id)
);

-- :name indexes_aas
CREATE INDEX IF NOT EXISTS idx_aa_nodes_tree ON aa_nodes(tree_id);

-- ── Read queries ─────────────────────────────────────────────────────────────

-- :name select_tree_index
SELECT id, name, tree_type FROM aa_trees ORDER BY id;

-- :name select_tree
SELECT * FROM aa_trees WHERE id = ?;

-- :name select_nodes_for_tree
SELECT * FROM aa_nodes WHERE tree_id = ? ORDER BY ycoord, xcoord, node_id;

-- :name select_node_costs
SELECT node_id, points_per_tier FROM aa_nodes WHERE tree_id = ?;

-- :name select_max_points
SELECT max_points FROM aa_trees WHERE id = ?;

-- {placeholders} sized at call time.
-- :name sum_max_points_for_types
SELECT COALESCE(SUM(max_points), 0) FROM aa_trees WHERE tree_type IN ({placeholders});

-- ── Build (scripts/build_aas_db.py) ──────────────────────────────────────────

-- :name upsert_tree
INSERT INTO aa_trees (id, name, tree_type, max_points, is_warder_tree, maximum_points,
                      minimum_points_required, ofx_classification, ofy_classification, version)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name, tree_type=excluded.tree_type, max_points=excluded.max_points,
    is_warder_tree=excluded.is_warder_tree, maximum_points=excluded.maximum_points,
    minimum_points_required=excluded.minimum_points_required,
    ofx_classification=excluded.ofx_classification,
    ofy_classification=excluded.ofy_classification, version=excluded.version;

-- Nodes are fully replaced per tree on rebuild (delete then insert) so removed
-- nodes never linger.
-- :name delete_nodes_for_tree
DELETE FROM aa_nodes WHERE tree_id = ?;

-- :name insert_node
INSERT INTO aa_nodes (tree_id, node_id, name, description, classification, node_group,
                      title, title_level, xcoord, ycoord, icon_id, icon_backdrop,
                      maxtier, points_per_tier, min_level, spellcrc, points_to_unlock,
                      points_global_to_unlock, classification_points_required,
                      first_parent_id, first_parent_required_tier)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
