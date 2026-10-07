"""EQ2 class catalogue — read-only accessor behind ClassCatalogue.

The canonical class catalogue is the Postgres ``classes`` schema, seeded by
db/migrations/0011_classes.sql (the seeds ARE the canonical data; there is
no build script). It holds:
  - 26 adventure classes (archetype ∈ {Fighter, Priest, Scout, Mage})
  - 9 crafters (archetype = "Crafter")

Derived views — archetype colours, crafter names, subclass/archetype groups —
are catalogue methods; ``backend.census.constants`` builds its module-level
tables from these AT IMPORT, so main.py applies migrations BEFORE importing
the app and a missing/empty classes schema fails fast at process start. To
change a class's role, colour, icon_id, or subclass, add a new migration.

Keyed by class NAME: EQ2 has several unrelated class-id schemes (icon_id is
the EQ2wire icon id; AA trees and Census type.classid use different ids), so
name is the only stable cross-reference.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, TypeVar

from backend.db_catalogue import PgCatalogue

_T = TypeVar("_T")

SCHEMA = "classes"

# DB_CLASSES_PATH is ignored (with a warning): class data lives in the
# Postgres `classes` schema.
if os.getenv("DB_CLASSES_PATH"):
    logging.getLogger(__name__).warning(
        "[classes-db] DB_CLASSES_PATH is set but ignored — class data lives in the "
        "Postgres `classes` schema (seeded by db/migrations/0011_classes.sql). "
        "Remove the env var to silence this."
    )

_ADVENTURE_ARCHETYPES: tuple[str, ...] = ("Fighter", "Priest", "Scout", "Mage")
_CRAFTER_ARCHETYPE: str = "Crafter"


class ClassCatalogue(PgCatalogue):
    """Read access to the classes schema, with per-instance caching.

    Class data is static per deploy — every read is cached forever on the
    instance; ``clear_caches()`` resets (tests). Returned rows/structures are
    shared cached objects: treat them as read-only.
    """

    READY_TABLE = "classes"

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)
        self._rows: list[dict] | None = None
        self._derived: dict[str, Any] = {}

    def clear_caches(self) -> None:
        """Reset the per-instance caches — used by tests."""
        super().clear_caches()
        self._rows = None
        self._derived.clear()

    def _cache_info(self) -> dict[str, int]:
        return {"rows": len(self._rows or ()), "derived": len(self._derived)}

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

        Raises RuntimeError when the schema is missing/unseeded — the
        catalogue is migration-seeded source-of-truth and an empty read means
        a broken environment, not an empty game."""
        if self._rows is None:
            try:
                rows = [dict(r) for r in self._fetchall("SELECT * FROM classes ORDER BY display_order")]
            except Exception as exc:  # psycopg errors → same fail-fast contract
                raise RuntimeError(
                    f"classes schema {self.schema!r} is unreadable ({exc}). It is seeded by "
                    "db/migrations/0011_classes.sql — run migrations (app startup does, and "
                    "main.py applies them before the app imports)."
                ) from exc
            if not rows:
                raise RuntimeError(
                    f"classes schema {self.schema!r} is empty. It is seeded by "
                    "db/migrations/0011_classes.sql — run migrations against this database."
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
