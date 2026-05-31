"""EQ2 class catalogue — read-only DB-backed accessor.

The canonical class catalogue is the committed SQLite file at
``data/classes/classes.db``. It holds:
  - 26 adventure classes (archetype ∈ {Fighter, Priest, Scout, Mage})
  - 9 crafters (archetype = "Crafter")

This module reads it ONCE at import time and exposes the derived view used
across the codebase (ARCHETYPE_COLOURS, CRAFTER_NAMES, SUBCLASS_GROUPS,
ARCHETYPE_GROUPS). It does NOT define class data inline anywhere — to
change a class's role, colour, icon_id, or subclass, edit the row in
classes.db and commit the new file.

Keyed by class NAME: EQ2 has several unrelated class-id schemes (icon_id is
the EQ2wire icon id; AA trees and Census type.classid use different ids), so
name is the only stable cross-reference.

Why DB-backed instead of a Python literal:
  - One source of truth at runtime. Code never disagrees with the DB.
  - Maintainers (and admin tooling) can update class metadata by editing
    the committed .db file — no code redeploy needed for cosmetic
    changes like archetype colours.
  - The DB is small (~20 KB) and committed, so CI works without a build
    step. Module-import-time read cost is one SQLite open + 35-row scan.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from backend.db_helpers import resolve_db_path
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


@dataclass(frozen=True)
class ClassInfo:
    name: str
    archetype: str  # Fighter | Priest | Scout | Mage | Crafter
    subclass: str | None  # middle tier; None for Beastlord, Channeler, crafters
    role: str  # Tank | Healer | Melee DPS | Ranged DPS | Support | Crafter
    colour: str  # hex (archetype colour)
    icon_id: int  # EQ2wire class_medium icon id (crafters get 100+ placeholders)


DB_PATH: Path = resolve_db_path("DB_CLASSES_PATH", "classes", "classes.db")

# Schema (CREATE TABLE / INDEX) lives in classes.sql; init_db runs each block.


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create the classes table/indexes if missing. Returns an open connection.

    Used by tests that want an in-memory DB (`:memory:`), and as a safety net
    when the file at `path` exists but is missing the table. Production never
    needs this — classes.db is committed pre-populated.
    """
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(_SQL["schema_classes"])
    conn.executescript(_SQL["indexes_classes"])
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# DB-backed accessors (used at runtime by routes)
# ---------------------------------------------------------------------------


def list_all(path: Path = DB_PATH) -> list[dict]:
    """All classes ordered by display_order. Empty list if the DB is missing/unseeded."""
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(_SQL["list_all"]).fetchall()]
    finally:
        conn.close()


def find_by_name(name: str, path: Path = DB_PATH) -> dict | None:
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(_SQL["find_by_name"], (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def by_role(role: str, path: Path = DB_PATH) -> list[dict]:
    return [c for c in list_all(path) if c["role"] == role]


def by_archetype(archetype: str, path: Path = DB_PATH) -> list[dict]:
    return [c for c in list_all(path) if c["archetype"] == archetype]


# ---------------------------------------------------------------------------
# Derived module-level constants
# ---------------------------------------------------------------------------
# Loaded ONCE at import time from the committed classes.db. Anything that needs
# a snapshot of class groupings (compute_class_label, CLASS_GROUPS in
# census/constants.py, the archetype decomposition in server/api/item.py) reads
# these constants instead of redefining the data inline. Changing class
# metadata is a matter of editing the .db row and committing the file.

_ADVENTURE_ARCHETYPES: tuple[str, ...] = ("Fighter", "Priest", "Scout", "Mage")
_CRAFTER_ARCHETYPE: str = "Crafter"


def _load_rows() -> list[dict]:
    """Read the entire catalogue at module import. ~35 rows, one SQLite open."""
    try:
        rows = list_all(DB_PATH)
    except sqlite3.DatabaseError:
        rows = []
    if not rows:
        raise RuntimeError(
            f"classes.db at {DB_PATH} is empty or unreadable. The DB is committed at "
            "data/classes/classes.db — if it's missing on a fresh clone, fetch the "
            "file from origin or restore from the Railway volume."
        )
    return rows


_ROWS: list[dict] = _load_rows()
_ADV_ROWS: list[dict] = [r for r in _ROWS if r["archetype"] in _ADVENTURE_ARCHETYPES]
_CRAFTER_ROWS: list[dict] = [r for r in _ROWS if r["archetype"] == _CRAFTER_ARCHETYPE]


def _build_archetype_colours() -> dict[str, str]:
    """{ archetype: colour } from DB. Adventure archetypes only — crafters
    share a neutral colour that callers don't usually care about."""
    seen: dict[str, str] = {}
    for r in _ADV_ROWS:
        arc = r["archetype"]
        if arc not in seen:
            seen[arc] = r["colour"]
    return seen


ARCHETYPE_COLOURS: dict[str, str] = _build_archetype_colours()

CRAFTER_NAMES: frozenset[str] = frozenset(r["name"] for r in _CRAFTER_ROWS)


def _build_subclass_groups() -> tuple[tuple[str, frozenset[str]], ...]:
    """Ordered (subclass_name, frozenset[class_name]) for the 12 subclass
    pairs. Channeler / Beastlord have subclass=None so they're excluded.
    Order: first-occurrence by display_order so Fighter subclasses come
    before Priest, Scout, Mage."""
    seen: dict[str, list[str]] = {}
    for r in _ADV_ROWS:
        sub = r["subclass"]
        if sub is None:
            continue
        seen.setdefault(sub, []).append(r["name"])
    return tuple((sub, frozenset(names)) for sub, names in seen.items())


SUBCLASS_GROUPS: tuple[tuple[str, frozenset[str]], ...] = _build_subclass_groups()


def _build_archetype_groups() -> tuple[tuple[str, frozenset[str]], ...]:
    """Ordered (archetype_name, frozenset[class_name]) — Fighter/Priest/Scout/Mage."""
    seen: dict[str, list[str]] = {}
    for r in _ADV_ROWS:
        seen.setdefault(r["archetype"], []).append(r["name"])
    return tuple((arc, frozenset(names)) for arc, names in seen.items())


ARCHETYPE_GROUPS: tuple[tuple[str, frozenset[str]], ...] = _build_archetype_groups()


def iter_adventure_class_names() -> list[str]:
    """All adventure-class names in display_order. Used by routes that need
    the class list without a per-request DB round-trip."""
    return [r["name"] for r in _ADV_ROWS]
