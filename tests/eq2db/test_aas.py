"""Tests for backend/eq2db/aas.py — the AA-tree catalogue.

Round-trip tests use a tmp DB through the real build path (upsert_tree);
the committed-data tests assert invariants of the shipped data/AAs/aas.db
(mirroring tests/eq2db/test_classes.py's approach to committed reference data).
"""

from __future__ import annotations

import pytest

from backend.eq2db import aas

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tree_json(nodes: list[dict], **tree_over) -> dict:
    tree = {
        "id": 42,
        "name": "Testar",
        "iswardertree": "false",
        "maximumpoints": 0,
        "minimumpointsrequired": 0,
        "version": 2,
        "alternateadvancementnode_list": nodes,
        **tree_over,
    }
    return {"alternateadvancement_list": [tree]}


def _node(node_id: int, **over) -> dict:
    base = {
        "nodeid": node_id,
        "name": f"Node {node_id}",
        "description": "",
        "classification": "Strength",
        "xcoord": 1,
        "ycoord": 0,
        "icon": {"id": 500, "backdrop": 456},
        "maxtier": 5,
        "pointspertier": 1,
        "spellcrc": 12345,
        "pointsspentintreetounlock": 0,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clear_caches():
    aas.clear_caches()
    yield
    aas.clear_caches()


# ---------------------------------------------------------------------------
# Round trip (tmp DB via the real build path)
# ---------------------------------------------------------------------------


def test_upsert_and_get_tree_round_trip(tmp_path):
    db = tmp_path / "aas.db"
    conn = aas.init_db(db)
    try:
        # class-shaped coords (xcoords {1,4,7,10,13}) → tree_type "class"
        nodes = [_node(100 + i, xcoord=x) for i, x in enumerate((1, 4, 7, 10, 13))]
        count = aas.upsert_tree(conn, 42, _tree_json(nodes))
    finally:
        conn.close()
    assert count == 5

    tree = aas.get_tree(42, path=db)
    assert tree is not None
    assert tree["name"] == "Testar"
    assert tree["tree_type"] == "class"
    assert tree["max_points"] == 5 * 5  # 5 nodes × maxtier 5 × ppt 1
    assert len(tree["nodes"]) == 5
    node = tree["nodes"][0]
    assert node["icon_id"] == 500 and node["icon_backdrop"] == 456
    assert node["points_per_tier"] == 1 and node["spellcrc"] == 12345


def test_upsert_coerces_census_string_numerics(tmp_path):
    """Census sometimes serialises numerics as strings — the loader coerces."""
    db = tmp_path / "aas.db"
    conn = aas.init_db(db)
    try:
        stringy = _node(
            "101",
            xcoord="1",
            ycoord="2",
            icon={"id": "500", "backdrop": "456"},
            maxtier="5",
            pointspertier="2",
            firstparentid="999",
            firstparentrequiredtier="3",
        )
        aas.upsert_tree(conn, 7, _tree_json([stringy]))
    finally:
        conn.close()
    tree = aas.get_tree(7, path=db)
    assert tree is not None
    n = tree["nodes"][0]
    assert (n["node_id"], n["xcoord"], n["ycoord"]) == (101, 1, 2)
    assert (n["maxtier"], n["points_per_tier"]) == (5, 2)
    assert (n["first_parent_id"], n["first_parent_required_tier"]) == (999, 3)
    assert tree["max_points"] == 10  # 5 × 2
    assert aas.tree_node_costs(7, path=db) == {101: 2}
    assert aas.tree_max_points(7, path=db) == 10


def test_rebuild_replaces_removed_nodes(tmp_path):
    """A rebuild fully replaces a tree's nodes — removed nodes never linger."""
    db = tmp_path / "aas.db"
    conn = aas.init_db(db)
    try:
        aas.upsert_tree(conn, 1, _tree_json([_node(101), _node(102, xcoord=4)]))
        aas.upsert_tree(conn, 1, _tree_json([_node(101)]))  # 102 removed upstream
    finally:
        conn.close()
    tree = aas.get_tree(1, path=db)
    assert tree is not None
    assert [n["node_id"] for n in tree["nodes"]] == [101]


def test_missing_db_and_unknown_tree(tmp_path):
    missing = tmp_path / "nope.db"
    assert aas.load_tree_index(path=missing) == {}
    assert aas.tree_node_costs(1, path=missing) == {}
    assert aas.tree_max_points(1, path=missing) == 0
    assert aas.total_max_points(frozenset({"tradeskill"}), path=missing) == 0
    assert aas.get_tree(1, path=missing) is None
    db = tmp_path / "empty.db"
    aas.init_db(db).close()
    assert aas.get_tree(999, path=db) is None


def test_total_max_points_filters_by_type(tmp_path):
    db = tmp_path / "aas.db"
    conn = aas.init_db(db)
    try:
        # tradeskill tree (classification "Crafting Expertise")
        aas.upsert_tree(conn, 1, _tree_json([_node(1, classification="Crafting Expertise", maxtier=3)]))
        # heroic tree
        aas.upsert_tree(conn, 2, _tree_json([_node(2, classification="Heroic", maxtier=4)]))
    finally:
        conn.close()
    assert aas.total_max_points(frozenset({"tradeskill"}), path=db) == 3
    assert aas.total_max_points(frozenset({"tradeskill", "heroic"}), path=db) == 7
    assert aas.total_max_points(frozenset(), path=db) == 0


# ---------------------------------------------------------------------------
# detect_tree_type (pure heuristic — build-time)
# ---------------------------------------------------------------------------


def test_detect_tree_type_cases():
    def t(nodes, **over):
        return aas.detect_tree_type(_tree_json(nodes, **over))

    assert t([_node(i, xcoord=x) for i, x in enumerate((1, 4, 7, 10, 13))]) == "class"
    assert t([_node(1, xcoord=15, ycoord=19)], ofyclassification="Expertise") == "subclass"
    assert t([_node(1, classification="Heroic", xcoord=2)]) == "heroic"
    assert t([_node(1, classification="Crafting Expertise", xcoord=2)]) == "tradeskill"
    assert t([_node(1, xcoord=99)]) == "unknown"


# ---------------------------------------------------------------------------
# Committed data/AAs/aas.db invariants (skipped if not built locally)
# ---------------------------------------------------------------------------

_committed = pytest.mark.skipif(not aas.DB_PATH.exists(), reason="committed aas.db not present")


@_committed
def test_committed_db_tree_count():
    idx = aas.load_tree_index()
    assert len(idx) == 157
    assert all(v["type"] != "unknown" for v in idx.values())


@_committed
def test_committed_db_known_values():
    # Bladedance (tree 1) costs 2 points/tier — the same real-data invariant
    # test_aa_routes.py relies on.
    assert aas.tree_node_costs(1).get(554687586) == 2
    # Tradeskill caps derived from the data: EoF (tradeskill only) → 45;
    # with tradeskill_general (AoD+) → 116.
    assert aas.total_max_points(frozenset({"tradeskill"})) == 45
    assert aas.total_max_points(frozenset({"tradeskill", "tradeskill_general"})) == 116


@_committed
def test_committed_db_meta_stamps():
    conn = aas.init_db(aas.DB_PATH)
    try:
        assert aas.get_meta(conn, "tree_count") == "157"
        assert aas.get_meta(conn, "built_at") is not None
    finally:
        conn.close()
