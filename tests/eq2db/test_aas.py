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


@pytest.fixture
def cat(tmp_path):
    """A fresh AACatalogue on a tmp db, built via init_db."""
    c = aas.AACatalogue(tmp_path / "aas.db")
    c.init_db().close()
    return c


# ---------------------------------------------------------------------------
# Round trip (tmp DB via the real build path)
# ---------------------------------------------------------------------------


def test_upsert_and_get_tree_round_trip(cat):
    conn = cat.init_db()
    try:
        # class-shaped coords (xcoords {1,4,7,10,13}) → tree_type "class"
        nodes = [_node(100 + i, xcoord=x) for i, x in enumerate((1, 4, 7, 10, 13))]
        count = cat.upsert_tree(conn, 42, _tree_json(nodes))
    finally:
        conn.close()
    assert count == 5

    tree = cat.get_tree(42)
    assert tree is not None
    assert tree["name"] == "Testar"
    assert tree["tree_type"] == "class"
    assert tree["max_points"] == 5 * 5  # 5 nodes × maxtier 5 × ppt 1
    assert len(tree["nodes"]) == 5
    node = tree["nodes"][0]
    assert node["icon_id"] == 500 and node["icon_backdrop"] == 456
    assert node["points_per_tier"] == 1 and node["spellcrc"] == 12345


def test_upsert_coerces_census_string_numerics(cat):
    """Census sometimes serialises numerics as strings — the loader coerces."""
    conn = cat.init_db()
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
        cat.upsert_tree(conn, 7, _tree_json([stringy]))
    finally:
        conn.close()
    tree = cat.get_tree(7)
    assert tree is not None
    n = tree["nodes"][0]
    assert (n["node_id"], n["xcoord"], n["ycoord"]) == (101, 1, 2)
    assert (n["maxtier"], n["points_per_tier"]) == (5, 2)
    assert (n["first_parent_id"], n["first_parent_required_tier"]) == (999, 3)
    assert tree["max_points"] == 10  # 5 × 2
    assert cat.tree_node_costs(7) == {101: 2}
    assert cat.tree_max_points(7) == 10


def test_rebuild_replaces_removed_nodes(cat):
    """A rebuild fully replaces a tree's nodes — removed nodes never linger."""
    conn = cat.init_db()
    try:
        cat.upsert_tree(conn, 1, _tree_json([_node(101), _node(102, xcoord=4)]))
        cat.upsert_tree(conn, 1, _tree_json([_node(101)]))  # 102 removed upstream
    finally:
        conn.close()
    tree = cat.get_tree(1)
    assert tree is not None
    assert [n["node_id"] for n in tree["nodes"]] == [101]


def test_missing_db_and_unknown_tree(tmp_path, cat):
    missing = aas.AACatalogue(tmp_path / "nope.db")
    assert missing.load_tree_index() == {}
    assert missing.tree_node_costs(1) == {}
    assert missing.tree_max_points(1) == 0
    assert missing.total_max_points(frozenset({"tradeskill"})) == 0
    assert missing.get_tree(1) is None
    assert cat.get_tree(999) is None  # built but empty


def test_total_max_points_filters_by_type(cat):
    conn = cat.init_db()
    try:
        # tradeskill tree (classification "Crafting Expertise")
        cat.upsert_tree(conn, 1, _tree_json([_node(1, classification="Crafting Expertise", maxtier=3)]))
        # heroic tree
        cat.upsert_tree(conn, 2, _tree_json([_node(2, classification="Heroic", maxtier=4)]))
    finally:
        conn.close()
    assert cat.total_max_points(frozenset({"tradeskill"})) == 3
    assert cat.total_max_points(frozenset({"tradeskill", "heroic"})) == 7
    assert cat.total_max_points(frozenset()) == 0


def test_limits_round_trip_and_alias_resolution(tmp_path, cat):
    conn = cat.init_db()
    try:
        cat.upsert_limits(
            conn, "Destiny of Velious", {"aa_cap": 300, "unlocked_trees": ["class", "subclass"], "notes": "x"}
        )
    finally:
        conn.close()
    for query in ("Destiny of Velious", "DoV", "dov", " DOV "):
        lim = cat.xpac_limits(query)
        assert lim == {"aa_cap": 300, "unlocked_trees": ["class", "subclass"], "visible_rows": {}}, query
    assert cat.xpac_limits("Unknown Xpac") is None
    assert aas.AACatalogue(tmp_path / "missing.db").xpac_limits("DoV") is None


def test_limits_visible_rows_round_trip(cat):
    """Era-partial trees: visible_rows survives the upsert → read cycle."""
    conn = cat.init_db()
    try:
        cat.upsert_limits(
            conn,
            "Echoes of Faydwer",
            {
                "aa_cap": 100,
                "unlocked_trees": ["class", "subclass", "tradeskill"],
                "visible_rows": {"class": [0, 1, 2, 3, 4], "subclass": [0, 3, 6, 9, 13]},
            },
        )
    finally:
        conn.close()
    lim = cat.xpac_limits("EoF")
    assert lim is not None
    assert lim["visible_rows"] == {"class": [0, 1, 2, 3, 4], "subclass": [0, 3, 6, 9, 13]}


def test_init_db_migrates_pre_visible_rows_limits_table(tmp_path):
    """init_db on a DB whose aa_limits predates the visible_rows column must
    migrate in place and keep existing rows readable.

    Memory [test-migrations-against-old-db-shape].
    """
    import sqlite3

    db_path = tmp_path / "old-aas.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE aa_limits (xpac TEXT PRIMARY KEY, aa_cap INTEGER NOT NULL DEFAULT 0,"
        " unlocked_trees TEXT NOT NULL DEFAULT '[]', notes TEXT)"
    )
    conn.execute(
        "INSERT INTO aa_limits (xpac, aa_cap, unlocked_trees, notes) VALUES ('Kingdom of Sky', 50, '[\"class\"]', 'x')"
    )
    conn.commit()
    conn.close()

    cat = aas.AACatalogue(db_path)
    cat.init_db().close()  # applies the ALTER migration
    lim = cat.xpac_limits("KoS")
    assert lim == {"aa_cap": 50, "unlocked_trees": ["class"], "visible_rows": {}}


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
    idx = aas.catalogue.load_tree_index()
    assert len(idx) == 157
    assert all(v["type"] != "unknown" for v in idx.values())


@_committed
def test_committed_db_known_values():
    # Bladedance (tree 1) costs 2 points/tier — the same real-data invariant
    # test_aa_routes.py relies on.
    assert aas.catalogue.tree_node_costs(1).get(554687586) == 2
    # Tradeskill caps derived from the data: EoF (tradeskill only) → 45;
    # with tradeskill_general (AoD+) → 116.
    assert aas.catalogue.total_max_points(frozenset({"tradeskill"})) == 45
    assert aas.catalogue.total_max_points(frozenset({"tradeskill", "tradeskill_general"})) == 116


@_committed
def test_committed_db_limits():
    lim = aas.catalogue.xpac_limits("Destiny of Velious")
    assert lim is not None and lim["aa_cap"] == 300
    assert aas.catalogue.xpac_limits("KoS") == aas.catalogue.xpac_limits("Kingdom of Sky")


@_committed
def test_committed_db_era_visible_rows():
    """The 2026-07 era curation: pre-Sentinel's-Fate xpacs hide the class
    tree's rows 5-6 and the subclass rows 16/19 (verified against live
    Wuoshi census data, boundary user-confirmed); SF+ show everything."""
    kos = aas.catalogue.xpac_limits("Kingdom of Sky")
    assert kos is not None and kos["visible_rows"] == {"class": [0, 1, 2, 3, 4]}
    for xpac in ("Echoes of Faydwer", "Rise of Kunark", "The Shadow Odyssey"):
        lim = aas.catalogue.xpac_limits(xpac)
        assert lim is not None, xpac
        assert lim["visible_rows"] == {
            "class": [0, 1, 2, 3, 4],
            "subclass": [0, 3, 6, 9, 13],
        }, xpac
    sf = aas.catalogue.xpac_limits("Sentinel's Fate")
    assert sf is not None and sf["visible_rows"] == {}


@_committed
def test_committed_db_meta_stamps():
    conn = aas.catalogue.init_db()
    try:
        assert aas.get_meta(conn, "tree_count") == "157"
        assert aas.get_meta(conn, "built_at") is not None
    finally:
        conn.close()
