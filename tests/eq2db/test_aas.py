"""Tests for backend/eq2db/aas.py — the AA-tree catalogue (Postgres ``aas`` schema).

Round-trip tests lease an isolated scratch schema via the ``aas_schema``
fixture (tests/fixtures/catalogues_db.py) and go through the real build path
(upsert_tree / upsert_limits); the ``cat`` fixture empties the lease first so
every assertion controls its own data, like the old tmp-file db. The
seeded-data tests assert invariants of the migration seeds in
db/migrations/0012_aas.sql — every environment is data-complete from
migrations alone (mirroring tests/eq2db/test_classes.py's approach to
seeded reference data), so there is no "not built locally" skip anymore.
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
def cat(aas_schema):
    """A fresh AACatalogue on an EMPTIED leased schema — the analog of the
    old tmp-file db built via init_db. The lease arrives fully seeded
    (157 trees / 12 limits); wiping lets every round-trip assertion own
    the whole table (e.g. total_max_points sums globally per type). The
    leaser re-seeds on release."""
    c = aas.AACatalogue(aas_schema)
    conn = c.init_db()
    try:
        conn.execute("DELETE FROM aa_trees")  # cascades to aa_nodes
        conn.execute("DELETE FROM aa_limits")
        conn.commit()
    finally:
        conn.close()
    return c


# ---------------------------------------------------------------------------
# Round trip (leased schema via the real build path)
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


def test_empty_schema_and_unknown_tree(cat):
    """(Was: missing-DB degradation — files are gone, and a lease is always
    migration-seeded.) The NEW contract: an emptied schema reads not-ready
    and the seeded default schema is ready; the unknown-tree soft paths
    still return None/{}/0 instead of raising."""
    assert not cat.ready()  # emptied lease — routes degrade via ready()
    assert cat.load_tree_index() == {}
    assert cat.tree_node_costs(1) == {}
    assert cat.tree_max_points(1) == 0
    assert cat.total_max_points(frozenset({"tradeskill"})) == 0
    assert cat.get_tree(1) is None
    seeded = aas.AACatalogue("aas")  # the session schema, migration-seeded
    assert seeded.ready()
    assert seeded.get_tree(999_999_999) is None  # seeded but unknown id


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


def test_limits_round_trip_and_alias_resolution(cat):
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
    # (Was: a missing-file catalogue → None.) A fresh instance over the same
    # schema still soft-Nones an alias whose row is absent, cache-independent.
    assert aas.AACatalogue(cat.schema).xpac_limits("EoF") is None


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


def test_limits_visible_rows_defaults_empty_when_omitted(cat):
    """An upsert_limits entry without visible_rows reads back as {} — the
    column is part of the migration-owned DDL (db/migrations/0012_aas.sql)
    with DEFAULT '{}'.

    (Retired: the SQLite-era in-place ALTER — migrate_aa_limits_visible_rows —
    that backfilled the column onto pre-2026-07 aas.db files; this test used
    to seed an old-shape aa_limits table via raw sqlite3 and assert init_db
    migrated it. Memory [test-migrations-against-old-db-shape] is now served
    by migrations owning all DDL up front.)"""
    conn = cat.init_db()
    try:
        cat.upsert_limits(conn, "Kingdom of Sky", {"aa_cap": 50, "unlocked_trees": ["class"], "notes": "x"})
    finally:
        conn.close()
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
# Migration-seed invariants (db/migrations/0012_aas.sql — the former
# committed data/AAs/aas.db, present in every environment by construction)
# ---------------------------------------------------------------------------


def test_seeded_db_tree_count():
    idx = aas.catalogue.load_tree_index()
    assert len(idx) == 157
    assert all(v["type"] != "unknown" for v in idx.values())


def test_seeded_db_known_values():
    # Bladedance (tree 1) costs 2 points/tier — the same real-data invariant
    # test_aa_routes.py relies on.
    assert aas.catalogue.tree_node_costs(1).get(554687586) == 2
    # Tradeskill caps derived from the data: EoF (tradeskill only) → 45;
    # with tradeskill_general (AoD+) → 116.
    assert aas.catalogue.total_max_points(frozenset({"tradeskill"})) == 45
    assert aas.catalogue.total_max_points(frozenset({"tradeskill", "tradeskill_general"})) == 116


def test_seeded_db_limits():
    lim = aas.catalogue.xpac_limits("Destiny of Velious")
    assert lim is not None and lim["aa_cap"] == 300
    assert aas.catalogue.xpac_limits("KoS") == aas.catalogue.xpac_limits("Kingdom of Sky")


def test_seeded_db_era_visible_rows():
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


def test_seeded_db_meta_stamps():
    conn = aas.catalogue.init_db()
    try:
        # The seeds carry the provenance stamps of the final SQLite build.
        assert aas.catalogue.get_meta(conn, "tree_count") == "157"
        assert aas.catalogue.get_meta(conn, "built_at") is not None
    finally:
        conn.close()


class TestBotHelpers:
    """resolve_tree_id + points_spent — extracted from the /aacheck cog.
    The instance caches make injection trivial: prime _tree_index /
    _node_costs directly instead of mocking."""

    def test_resolve_tree_id_first_match(self, cat):
        cat._tree_index = {10: {"name": "Templar", "type": "subclass"}, 20: {"name": "Priest", "type": "class"}}
        assert cat.resolve_tree_id([10, 20], {"class"}) == 20
        assert cat.resolve_tree_id([10, 20], {"subclass"}) == 10
        # Trade choice accepts either tradeskill type; none here.
        assert cat.resolve_tree_id([10, 20], {"tradeskill", "tradeskill_general"}) is None
        assert cat.resolve_tree_id([], {"class"}) is None

    def test_points_spent_uses_per_tier_costs(self, cat):
        cat._node_costs[5] = {1: 2}
        # node 1 costs 2/tier; node 99 unknown -> default 1/tier
        assert cat.points_spent(5, {1: 3, 99: 4}) == 3 * 2 + 4 * 1
