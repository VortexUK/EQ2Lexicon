"""Local SQLite AA catalogue (aas.db) — the single source of AA reference data.

Condenses the AA JSONs (census tree downloads + ``aa_limits.json``) into
``aa_trees`` + ``aa_nodes`` + ``aa_limits``. ``tree_type`` (the structural
detect_tree_type heuristic) and ``max_points`` (Σ maxtier × points_per_tier)
are precomputed at build time by ``scripts/build_aas_db.py``, so runtime
consumers do simple indexed reads.

All access goes through the :class:`AACatalogue` class — one method call per
question, so AA code elsewhere stays minimal. The module-level ``catalogue``
is the shared default instance (committed ``data/AAs/aas.db``, like
classes.db; ``DB_AAS_PATH`` env overrides). Tests construct their own
``AACatalogue(tmp_path)`` — every instance carries its own caches.

Rebuild flow (tree JSONs are LOCAL intermediates, gitignored — only aas.db
and the hand-curated aa_limits.json are committed):
``scripts/download_aa_trees.py`` → ``scripts/build_aas_db.py`` → commit aas.db.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from backend.db_catalogue import BaseCatalogue
from backend.db_helpers import resolve_db_path
from backend.eq2db import _meta as _meta_db
from backend.sql_loader import load_sql

_log = logging.getLogger(__name__)

_SQL = load_sql(__file__)

DB_PATH: Path = resolve_db_path("DB_AAS_PATH", "AAs", "aas.db")

# Re-export the shared meta helpers (provenance stamps set by the build script).
get_meta = _meta_db.get_meta
set_meta = _meta_db.set_meta

# EQ2 expansion short codes → canonical aa_limits keys. A server's current_xpac
# is often stored as a short code (e.g. "DoV"); without this the limits lookup
# misses and the AA cap silently reads 0 — which hides the Raid-Ready check and
# the per-expansion cap on the AA tab.
_XPAC_ALIASES: dict[str, str] = {
    "kos": "Kingdom of Sky",
    "eof": "Echoes of Faydwer",
    "rok": "Rise of Kunark",
    "tso": "The Shadow Odyssey",
    "sf": "Sentinel's Fate",
    "dov": "Destiny of Velious",
    "aod": "Age of Discovery",
    "coe": "Chains of Eternity",
    "tov": "Tears of Veeshan",
    "aom": "Altar of Malice",
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def detect_tree_type(tree_data: dict) -> str:
    """Classify a raw tree JSON dict into its structural type key.

    Build-time only: the result is stored in aa_trees.tree_type, so runtime
    consumers never re-run this heuristic.
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


class AACatalogue(BaseCatalogue):
    """Read (and build) access to one aas.db file, with per-instance caching.

    AA data is static per deploy, so every read is cached forever on the
    instance; ``clear_caches()`` resets (tests + the build script).
    """

    FOREIGN_KEYS = True

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)
        self._tree_index: dict[int, dict[str, str]] | None = None
        self._trees: dict[int, dict | None] = {}
        self._node_costs: dict[int, dict[int, int]] = {}
        self._max_points: dict[int, int] = {}
        self._total_max_points: dict[frozenset[str], int] = {}
        self._limits: dict[str, dict | None] = {}

    # ── Connection helpers ───────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_aa_trees"])
        conn.execute(_SQL["schema_aa_nodes"])
        conn.execute(_SQL["schema_aa_limits"])
        conn.executescript(_SQL["indexes_aas"])

    def _query(self, name: str, params: tuple = ()) -> list:
        """Run one read query by its _SQL block name; [] when the DB is
        missing or unbuilt (see BaseCatalogue._fetchall)."""
        return self._fetchall(_SQL[name], params)

    def clear_caches(self) -> None:
        """Reset every per-instance cache — used by tests and the build script."""
        self._tree_index = None
        self._trees.clear()
        self._node_costs.clear()
        self._max_points.clear()
        self._total_max_points.clear()
        self._limits.clear()

    def _cache_info(self) -> dict[str, int]:
        return {
            "tree_index": len(self._tree_index or ()),
            "trees": len(self._trees),
            "node_costs": len(self._node_costs),
            "max_points": len(self._max_points),
            "total_max_points": len(self._total_max_points),
            "limits": len(self._limits),
        }

    # ── Runtime accessors ────────────────────────────────────────────────────

    def load_tree_index(self) -> dict[int, dict[str, str]]:
        """``{tree_id: {"name": str, "type": str}}`` for every tree.

        Single source of truth for the web AA routes and the bot /aacheck cog.
        """
        if self._tree_index is None:
            rows = self._query("select_tree_index")
            self._tree_index = {int(r[0]): {"name": r[1], "type": r[2]} for r in rows}
        return self._tree_index

    def tree_node_costs(self, tree_id: int) -> dict[int, int]:
        """``{node_id: points_per_tier}`` — the per-tier AA point cost of each
        node (most are 1, some endline nodes are 2). Unknown tree → {}."""
        if tree_id not in self._node_costs:
            rows = self._query("select_node_costs", (tree_id,))
            self._node_costs[tree_id] = {int(r[0]): int(r[1]) for r in rows}
        return self._node_costs[tree_id]

    def tree_max_points(self, tree_id: int) -> int:
        """The tree's fully-maxed point total (precomputed at build). Unknown → 0."""
        if tree_id not in self._max_points:
            rows = self._query("select_max_points", (tree_id,))
            self._max_points[tree_id] = int(rows[0][0]) if rows else 0
        return self._max_points[tree_id]

    def total_max_points(self, tree_types: frozenset[str]) -> int:
        """Σ max_points over every tree whose type is in ``tree_types`` — e.g.
        the tradeskill AA cap."""
        if not tree_types:
            return 0
        if tree_types not in self._total_max_points:
            placeholders = ",".join("?" * len(tree_types))
            row = self._fetchone(
                _SQL["sum_max_points_for_types"].format(placeholders=placeholders),
                sorted(tree_types),
            )
            if row is None:
                return 0  # missing/unbuilt DB — don't cache the zero
            self._total_max_points[tree_types] = int(row[0]) if row[0] is not None else 0
        return self._total_max_points[tree_types]

    def get_tree(self, tree_id: int) -> dict | None:
        """Full tree detail: the aa_trees row plus a ``nodes`` list of aa_nodes
        rows (dicts, DB column names) in tree reading order. None when unknown."""
        if tree_id not in self._trees:
            trees = self._query("select_tree", (tree_id,))
            if not trees:
                self._trees[tree_id] = None
            else:
                out = dict(trees[0])
                nodes = self._query("select_nodes_for_tree", (tree_id,))
                out["nodes"] = [dict(n) for n in nodes]
                self._trees[tree_id] = out
        return self._trees[tree_id]

    def xpac_limits(self, xpac: str) -> dict | None:
        """``{"aa_cap": int, "unlocked_trees": [tree_type, ...]}`` for an
        expansion. Tolerates short codes ("DoV" → "Destiny of Velious") and
        case/whitespace. None when unknown (caller decides the fallback)."""
        if xpac not in self._limits:
            self._limits[xpac] = self._resolve_limits(xpac)
        return self._limits[xpac]

    def _resolve_limits(self, xpac: str) -> dict | None:
        candidates = [xpac]
        norm = xpac.strip().lower()
        if norm:
            aliased = _XPAC_ALIASES.get(norm)
            if aliased:
                candidates.append(aliased)
        row = None
        for candidate in candidates:
            rows = self._query("select_limit", (candidate,))
            if rows:
                row = rows[0]
                break
        if row is None and norm:
            # Case-insensitive full-name fallback
            for (key,) in self._query("select_limit_xpacs"):
                if key.lower() == norm:
                    row = self._query("select_limit", (key,))[0]
                    break
        if row is None:
            return None
        try:
            unlocked = json.loads(row[1] or "[]")
        except json.JSONDecodeError:
            unlocked = []
        return {"aa_cap": int(row[0]), "unlocked_trees": unlocked}

    # ── Build (scripts/build_aas_db.py) ──────────────────────────────────────

    def upsert_tree(self, conn: sqlite3.Connection, tree_id: int, tree_data: dict) -> int:
        """Insert/replace one tree (and all its nodes) from its raw JSON dict.

        Computes tree_type + max_points from the SAME coerced values that get
        stored (the cap and the rows can never disagree); fully replaces the
        tree's nodes so a rebuild never leaves removed nodes behind. Census
        string-numerics are coerced. Returns the count of rows inserted
        (nodeid-less nodes are skipped, not counted).
        """
        aa_list = tree_data.get("alternateadvancement_list") or []
        if not aa_list:
            return 0
        tree = aa_list[0]
        nodes = tree.get("alternateadvancementnode_list") or []
        tree_type = detect_tree_type(tree_data)

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

    def upsert_limits(self, conn: sqlite3.Connection, xpac: str, entry: dict) -> None:
        """Insert/replace one expansion's AA limits (from aa_limits.json's
        ``{xpac: {aa_cap, unlocked_trees, notes}}`` entries)."""
        conn.execute(
            _SQL["upsert_limit"],
            (
                xpac,
                _as_int(entry.get("aa_cap", 0)),
                json.dumps(entry.get("unlocked_trees", [])),
                entry.get("notes"),
            ),
        )
        conn.commit()


# The shared default instance — every runtime consumer goes through this.
catalogue = AACatalogue()
