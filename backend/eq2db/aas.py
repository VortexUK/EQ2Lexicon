"""Local SQLite AA-tree catalogue (aas.db) — the single source of AA tree +
node reference data.

Condenses ``data/AAs/trees/{id}.json`` (157 files) into ``aa_trees`` +
``aa_nodes``. ``tree_type`` (the structural detect_tree_type heuristic) and
``max_points`` (Σ maxtier × points_per_tier) are precomputed at build time by
``scripts/build_aas_db.py``, so runtime consumers do simple indexed reads.

Like classes.db, aas.db is committed pre-populated (small, static reference
data); the JSONs stay committed as the rebuild source. ``DB_AAS_PATH`` env
overrides the location.

Accessors are ``lru_cache``d — AA data is static per deploy; tests use
``clear_caches()`` (or distinct tmp paths, which key separately).
"""

from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.db_helpers import resolve_db_path
from backend.eq2db import _meta as _meta_db
from backend.sql_loader import load_sql

_log = logging.getLogger(__name__)

_SQL = load_sql(__file__)

DB_PATH: Path = resolve_db_path("DB_AAS_PATH", "AAs", "aas.db")


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create the aas tables if missing. Returns an open connection."""
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    _meta_db.create_table(conn)
    conn.execute(_SQL["schema_aa_trees"])
    conn.execute(_SQL["schema_aa_nodes"])
    conn.executescript(_SQL["indexes_aas"])
    conn.commit()
    return conn


# Re-export the shared meta helpers (provenance stamps set by the build script).
get_meta = _meta_db.get_meta
set_meta = _meta_db.set_meta


# ---------------------------------------------------------------------------
# Runtime accessors (cached — static reference data)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_tree_index(path: Path = DB_PATH) -> dict[int, dict[str, str]]:
    """Return ``{tree_id: {"name": str, "type": str}}`` for every tree.

    Single source of truth for the web AA routes and the bot /aacheck cog
    (same contract as the old JSON-glob implementation).
    """
    if not path.exists():
        return {}
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(_SQL["select_tree_index"]).fetchall()
    except sqlite3.OperationalError:
        _log.exception("[aas-db] tree index query failed (unbuilt db?)")
        return {}
    finally:
        conn.close()
    return {int(r[0]): {"name": r[1], "type": r[2]} for r in rows}


@lru_cache(maxsize=256)
def tree_node_costs(tree_id: int, path: Path = DB_PATH) -> dict[int, int]:
    """``{node_id: points_per_tier}`` for a tree — the per-tier AA point cost of
    each node (most are 1, some endline nodes are 2). Unknown tree → {}."""
    if not path.exists():
        return {}
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(_SQL["select_node_costs"], (tree_id,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {int(r[0]): int(r[1]) for r in rows}


@lru_cache(maxsize=256)
def tree_max_points(tree_id: int, path: Path = DB_PATH) -> int:
    """The tree's fully-maxed point total (precomputed at build). Unknown → 0."""
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(_SQL["select_max_points"], (tree_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    return int(row[0]) if row else 0


@lru_cache(maxsize=32)
def total_max_points(tree_types: frozenset[str], path: Path = DB_PATH) -> int:
    """Σ max_points over every tree whose type is in ``tree_types`` — e.g. the
    tradeskill AA cap = total_max_points(frozenset({"tradeskill",
    "tradeskill_general"}))."""
    if not tree_types or not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        placeholders = ",".join("?" * len(tree_types))
        row = conn.execute(
            _SQL["sum_max_points_for_types"].format(placeholders=placeholders),
            sorted(tree_types),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    return int(row[0]) if row else 0


@lru_cache(maxsize=128)
def get_tree(tree_id: int, path: Path = DB_PATH) -> dict | None:
    """Full tree detail: the aa_trees row plus a ``nodes`` list of aa_nodes rows
    (dicts, DB column names) in tree reading order. None when unknown."""
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        tree = conn.execute(_SQL["select_tree"], (tree_id,)).fetchone()
        if tree is None:
            return None
        nodes = conn.execute(_SQL["select_nodes_for_tree"], (tree_id,)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    out = dict(tree)
    out["nodes"] = [dict(n) for n in nodes]
    return out


def clear_caches() -> None:
    """Reset every lru_cache — used by tests and the build script."""
    load_tree_index.cache_clear()
    tree_node_costs.cache_clear()
    tree_max_points.cache_clear()
    total_max_points.cache_clear()
    get_tree.cache_clear()


# ---------------------------------------------------------------------------
# Build-time helpers (scripts/build_aas_db.py)
# ---------------------------------------------------------------------------


def detect_tree_type(tree_data: dict) -> str:
    """Classify a raw tree JSON dict into its structural type key.

    Build-time only: the result is stored in aa_trees.tree_type, so runtime
    consumers never re-run this heuristic. Moved verbatim from
    backend/image/aa_tree.py.
    """
    tree = tree_data["alternateadvancement_list"][0]
    nodes = tree["alternateadvancementnode_list"]
    ofy = tree.get("ofyclassification", "")
    node_classes = {n.get("classification", "") for n in nodes}
    xs = {n["xcoord"] for n in nodes}
    ys = {n["ycoord"] for n in nodes}

    if xs == {1, 4, 7, 10, 13}:
        return "class"
    if ofy == "Expertise" and max(ys) == 19:
        return "subclass"
    if xs == {0, 6, 12, 18, 24, 30, 38, 42}:
        return "shadows"
    if "Heroic" in node_classes:
        return "heroic"
    if "Crafting Expertise" in node_classes:
        return "tradeskill"
    if xs == {3, 7, 11, 18, 22, 26, 33, 37, 41}:
        return "tradeskill_general"
    if "Warder Primals" in node_classes:
        return "warder"
    if ofy in ("Prestige Expertise", "Conversion") and "Prestige" in node_classes:
        return "prestige"
    if xs == {1, 5, 9, 13} and max(ys) == 4:
        return "dragon"
    if "Reign of Shadows" in node_classes:
        return "reign_of_shadows"
    if xs == {5, 13, 21, 29, 37}:
        return "far_seas"
    return "unknown"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def upsert_tree(conn: sqlite3.Connection, tree_id: int, tree_data: dict) -> int:
    """Insert/replace one tree (and all its nodes) from its raw JSON dict.

    Computes tree_type + max_points; fully replaces the tree's nodes so a
    rebuild never leaves removed nodes behind. Census sometimes serialises
    numeric fields as strings — everything is coerced. Returns the number of
    node rows actually inserted (nodeid-less nodes are skipped, not counted).
    """
    aa_list = tree_data.get("alternateadvancement_list") or []
    if not aa_list:
        return 0
    tree = aa_list[0]
    nodes = tree.get("alternateadvancementnode_list") or []
    tree_type = detect_tree_type(tree_data)

    # Coerce node rows FIRST, then derive max_points from the same values that
    # get stored — the cap and the rows can never disagree about a default.
    rows: list[tuple] = []
    for n in nodes:
        if "nodeid" not in n:
            continue
        icon = n.get("icon") or {}
        rows.append(
            (
                tree_id,
                _as_int(n["nodeid"]),
                str(n.get("name", "")),
                str(n.get("description", "")),
                str(n.get("classification", "")),
                str(n.get("group", "")),
                str(n.get("title", "")),
                _as_int(n.get("titlelevel", 0)),
                _as_int(n["xcoord"]),
                _as_int(n["ycoord"]),
                _as_int(icon.get("id", 0)),
                _as_int(icon.get("backdrop", -1), -1),
                _as_int(n.get("maxtier", 1), 1),
                _as_int(n.get("pointspertier", 1), 1),
                _as_int(n.get("minlevel", 1), 1),
                _as_int(n.get("spellcrc", 0)),
                _as_int(n.get("pointsspentintreetounlock", 0)),
                _as_int(n.get("pointsspentgloballytounlock", 0)),
                _as_int(n.get("classificationpointsrequired", 0)),
                _as_int(n["firstparentid"]) if "firstparentid" in n else None,
                _as_int(n["firstparentrequiredtier"]) if "firstparentrequiredtier" in n else None,
            )
        )
    max_points = sum(r[12] * r[13] for r in rows)  # maxtier × points_per_tier, as stored

    conn.execute(
        _SQL["upsert_tree"],
        (
            tree_id,
            str(tree.get("name", tree_id)),
            tree_type,
            max_points,
            1 if str(tree.get("iswardertree", "false")).lower() == "true" else 0,
            _as_int(tree.get("maximumpoints", 0)),
            _as_int(tree.get("minimumpointsrequired", 0)),
            tree.get("ofxclassification"),
            tree.get("ofyclassification"),
            _as_int(tree.get("version", 0)),
        ),
    )
    conn.execute(_SQL["delete_nodes_for_tree"], (tree_id,))
    conn.executemany(_SQL["insert_node"], rows)
    conn.commit()
    return len(rows)
