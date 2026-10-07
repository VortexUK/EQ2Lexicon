-- SQL for backend/eq2db/aas.py (Postgres `aas` schema; unqualified — the
-- catalogue's connections set search_path). Schema DDL lives in
-- db/migrations/0012_aas.sql; this file is DML only.
--
-- aa_trees condenses data/AAs/trees/{id}.json (157 files, ~3 MB) into two
-- tables. tree_type and max_points are PRECOMPUTED at build time (see
-- scripts/build_aas_db.py) so the runtime never re-runs the structural
-- detect_tree_type heuristic or the Σ(maxtier × points_per_tier) sweep.

-- ── Read queries ─────────────────────────────────────────────────────────────

-- :name select_tree_index
SELECT id, name, tree_type FROM aa_trees ORDER BY id;

-- :name select_tree
SELECT * FROM aa_trees WHERE id = %s;

-- :name select_nodes_for_tree
SELECT * FROM aa_nodes WHERE tree_id = %s ORDER BY ycoord, xcoord, node_id;

-- :name select_node_costs
SELECT node_id, points_per_tier FROM aa_nodes WHERE tree_id = %s;

-- :name select_max_points
SELECT max_points FROM aa_trees WHERE id = %s;

-- :name sum_max_points_for_types
SELECT COALESCE(SUM(max_points), 0) AS total FROM aa_trees WHERE tree_type = ANY(%s);

-- ── Build (scripts/build_aas_db.py) ──────────────────────────────────────────

-- :name upsert_tree
INSERT INTO aa_trees (id, name, tree_type, max_points, is_warder_tree, maximum_points,
                      minimum_points_required, ofx_classification, ofy_classification, version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    name=excluded.name, tree_type=excluded.tree_type, max_points=excluded.max_points,
    is_warder_tree=excluded.is_warder_tree, maximum_points=excluded.maximum_points,
    minimum_points_required=excluded.minimum_points_required,
    ofx_classification=excluded.ofx_classification,
    ofy_classification=excluded.ofy_classification, version=excluded.version;

-- Nodes are fully replaced per tree on rebuild (delete then insert) so removed
-- nodes never linger.
-- :name delete_nodes_for_tree
DELETE FROM aa_nodes WHERE tree_id = %s;

-- :name insert_node
INSERT INTO aa_nodes (tree_id, node_id, name, description, classification, node_group,
                      title, title_level, xcoord, ycoord, icon_id, icon_backdrop,
                      maxtier, points_per_tier, min_level, spellcrc, points_to_unlock,
                      points_global_to_unlock, classification_points_required,
                      first_parent_id, first_parent_required_tier)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);

-- ── AA limits (per-expansion caps — from data/AAs/aa_limits.json) ────────────

-- :name select_limit
SELECT aa_cap, unlocked_trees, notes, visible_rows FROM aa_limits WHERE xpac = %s;

-- :name select_limit_xpacs
SELECT xpac FROM aa_limits;

-- :name upsert_limit
INSERT INTO aa_limits (xpac, aa_cap, unlocked_trees, visible_rows, notes)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (xpac) DO UPDATE SET
    aa_cap=excluded.aa_cap, unlocked_trees=excluded.unlocked_trees,
    visible_rows=excluded.visible_rows, notes=excluded.notes;
