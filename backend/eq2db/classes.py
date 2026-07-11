"""EQ2 class catalogue — read-only DB-backed accessor behind ClassCatalogue.

The canonical class catalogue is the committed SQLite file at
``data/classes/classes.db``. It holds:
  - 26 adventure classes (archetype ∈ {Fighter, Priest, Scout, Mage})
  - 9 crafters (archetype = "Crafter")

All access goes through :class:`ClassCatalogue` (the AACatalogue methodology:
one class encapsulating the DB path + per-instance caches, with the shared
module-level ``catalogue`` as the runtime entry point). Derived views —
archetype colours, crafter names, subclass/archetype groups — are catalogue
methods; ``backend.census.constants`` and ``backend.eq2db.items`` build their
module-level tables from these at import, so a missing/empty classes.db still
fails fast at process start. It does NOT define class data inline anywhere —
to change a class's role, colour, icon_id, or subclass, edit the row in
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
    step. First-access read cost is one SQLite open + 35-row scan, cached
    on the instance thereafter.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from backend.db_helpers import resolve_db_path
from backend.sql_loader import load_sql

_T = TypeVar("_T")

_SQL = load_sql(__file__)

DB_PATH: Path = resolve_db_path("DB_CLASSES_PATH", "classes", "classes.db")

_ADVENTURE_ARCHETYPES: tuple[str, ...] = ("Fighter", "Priest", "Scout", "Mage")
_CRAFTER_ARCHETYPE: str = "Crafter"


class ClassCatalogue:
    """Read access to one classes.db file, with per-instance caching.

    Class data is static per deploy — every read is cached forever on the
    instance; ``clear_caches()`` resets (tests). Returned rows/structures are
    shared cached objects: treat them as read-only.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = Path(path)
        self._rows: list[dict] | None = None
        self._derived: dict[str, Any] = {}

    def init_db(self) -> sqlite3.Connection:
        """Create the classes table/indexes if missing. Returns an open
        connection. Used by tests (``:memory:`` supported) and as a safety net;
        production never needs it — classes.db is committed pre-populated."""
        if str(self.path) == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute(_SQL["schema_classes"])
        conn.executescript(_SQL["indexes_classes"])
        conn.commit()
        return conn

    def clear_caches(self) -> None:
        """Reset the per-instance caches — used by tests."""
        self._rows = None
        self._derived.clear()

    def _cached(self, key: str, build: Callable[[], _T]) -> _T:
        """Build-once cache for derived views. Callers across the codebase may
        compare results by identity (same object every call), so derived views
        must be stable, not rebuilt per call."""
        if key not in self._derived:
            self._derived[key] = build()
        return self._derived[key]

    # ── Row accessors ────────────────────────────────────────────────────────

    def list_all(self) -> list[dict]:
        """All classes ordered by display_order.

        Raises RuntimeError when the DB is missing/unseeded — the catalogue is
        committed source-of-truth and an empty read means a broken checkout,
        not an empty game."""
        if self._rows is None:
            try:
                conn = self.init_db()
                try:
                    conn.row_factory = sqlite3.Row
                    rows = [dict(r) for r in conn.execute(_SQL["list_all"]).fetchall()]
                finally:
                    conn.close()
            except sqlite3.DatabaseError:
                rows = []
            if not rows:
                raise RuntimeError(
                    f"classes.db at {self.path} is empty or unreadable. The DB is committed at "
                    "data/classes/classes.db — if it's missing on a fresh clone, fetch the "
                    "file from origin or restore from the Railway volume."
                )
            self._rows = rows
        return self._rows

    def find_by_name(self, name: str) -> dict | None:
        return next((r for r in self.list_all() if r["name"] == name), None)

    def by_role(self, role: str) -> list[dict]:
        return [r for r in self.list_all() if r["role"] == role]

    def by_archetype(self, archetype: str) -> list[dict]:
        return [r for r in self.list_all() if r["archetype"] == archetype]

    # ── Derived views (each computed once from the cached rows) ─────────────

    def _adventure_rows(self) -> list[dict]:
        return [r for r in self.list_all() if r["archetype"] in _ADVENTURE_ARCHETYPES]

    def archetype_colours(self) -> dict[str, str]:
        """{archetype: colour}. Adventure archetypes only — crafters share a
        neutral colour that callers don't usually care about."""

        def build() -> dict[str, str]:
            seen: dict[str, str] = {}
            for r in self._adventure_rows():
                seen.setdefault(r["archetype"], r["colour"])
            return seen

        return self._cached("archetype_colours", build)

    def crafter_names(self) -> frozenset[str]:
        return self._cached(
            "crafter_names",
            lambda: frozenset(r["name"] for r in self.list_all() if r["archetype"] == _CRAFTER_ARCHETYPE),
        )

    def subclass_groups(self) -> tuple[tuple[str, frozenset[str]], ...]:
        """Ordered (subclass_name, frozenset[class_name]) for the 12 subclass
        pairs. Channeler / Beastlord have subclass=None so they're excluded.
        Order: first-occurrence by display_order so Fighter subclasses come
        before Priest, Scout, Mage."""

        def build() -> tuple[tuple[str, frozenset[str]], ...]:
            seen: dict[str, list[str]] = {}
            for r in self._adventure_rows():
                if r["subclass"] is not None:
                    seen.setdefault(r["subclass"], []).append(r["name"])
            return tuple((sub, frozenset(names)) for sub, names in seen.items())

        return self._cached("subclass_groups", build)

    def archetype_groups(self) -> tuple[tuple[str, frozenset[str]], ...]:
        """Ordered (archetype_name, frozenset[class_name]) — Fighter/Priest/Scout/Mage."""

        def build() -> tuple[tuple[str, frozenset[str]], ...]:
            seen: dict[str, list[str]] = {}
            for r in self._adventure_rows():
                seen.setdefault(r["archetype"], []).append(r["name"])
            return tuple((arc, frozenset(names)) for arc, names in seen.items())

        return self._cached("archetype_groups", build)

    def adventure_class_names(self) -> list[str]:
        """All adventure-class names in display_order."""
        return self._cached("adventure_class_names", lambda: [r["name"] for r in self._adventure_rows()])


# The shared default instance — every runtime consumer goes through this.
catalogue = ClassCatalogue()
