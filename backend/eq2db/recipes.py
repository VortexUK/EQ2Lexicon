"""
Local SQLite mirror of the Census /recipe/ collection.

~70 k rows; download once with scripts/download_recipes.py.

Each row is one recipe.  Variable-length secondary components are stored
as a JSON array so the table stays flat and queries stay simple.

Output quality tiers (all stored as item_id + count):
  unfinished  – failed product (partial attempt)
  simple      – lowest quality tier
  worked      – medium quality tier
  elaborate   – high quality tier
  formed      – perfect / mastercrafted tier

Spell-scroll recipes (e.g. "Lightning Palm III (Expert)") also populate:
  base_name_lower – spell name without tier suffix, lowercased ("lightning palm iii")
  crafted_tier    – tier suffix ("Expert", "Grandmaster", "Ancient", …)
Non-spell recipes leave both columns NULL.

All behaviour lives on :class:`RecipeCatalogue` (the eq2db data-interface
convention — see AACatalogue / SpellCatalogue): DB lookups are instance
methods; the pure recipe-domain helpers (_parse_spell_tier, recipe_to_row)
are staticmethods on the same class so consumers import ONE name — the
shared ``catalogue`` instance. Module level holds only types (RecipeRow),
constants (SPELL_TIERS, DB_PATH), and the instance.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import TypedDict, cast

from backend.census._coerce import coerce_int as _int
from backend.db_catalogue import BaseCatalogue
from backend.db_helpers import resolve_db_path
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class _RecipeRowRequired(TypedDict):
    """Required fields present in every full RecipeRow."""

    id: int
    name: str


class RecipeRow(_RecipeRowRequired, total=False):
    """Row shape returned by ``find_by_id`` / ``find_by_name`` / ``find_by_output_id``.

    ``total=False`` (for optional fields) because partial queries produce
    valid but incomplete dicts. ``secondary_comps`` is deserialised from JSON
    into a list by ``_row_to_dict``.
    """

    crc: int
    name_lower: str
    bench: str
    version: int
    primary_comp: str | None  # ingredient display name (TEXT column)
    primary_qty: int
    secondary_comps: list  # deserialised from JSON
    fuel_comp: str | None  # ingredient display name (TEXT column)
    fuel_qty: int
    out_unfinished_id: int | None
    out_unfinished_count: int | None
    out_simple_id: int | None
    out_simple_count: int | None
    out_worked_id: int | None
    out_worked_count: int | None
    out_elaborate_id: int | None
    out_elaborate_count: int | None
    out_formed_id: int | None
    out_formed_count: int | None
    base_name_lower: str | None
    crafted_tier: str | None
    out_level: int | None  # crafted-output level (backfilled from items.db); drives craft tier
    last_update: int


_log = logging.getLogger(__name__)

# Ordered from lowest to highest so tier-comparison logic can use the index.
# BE-225: candidate for StrEnum conversion (ordering would be self-documenting),
# but consumers rely on iterating bare strings for canonicalisation — keep as tuple.
SPELL_TIERS: tuple[str, ...] = (
    "Apprentice",
    "Journeyman",
    "Adept",
    "Expert",
    "Master",
    "Grandmaster",
    "Ancient",
)

_SPELL_TIER_SET: frozenset[str] = frozenset(t.lower() for t in SPELL_TIERS)

# Matches "Some Spell Name III (Expert)" → groups: base name, tier
_TIER_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DB_PATH: Path = resolve_db_path("DB_RECIPES_PATH", "recipes", "recipes.db")

# Schema (CREATE TABLE / INDEX) lives in recipes.sql; init_db runs each block.

# The column list lives in recipes.sql under the `select_cols` block — fragment
# spliced into every find_* query at format-time.
_SELECT_COLS = _SQL["select_cols"]

_MIGRATIONS = [
    _SQL["migrate_add_base_name_lower"],
    _SQL["migrate_add_crafted_tier"],
    _SQL["migrate_add_out_level"],
]

# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401


def _row_to_dict(row: sqlite3.Row) -> RecipeRow:
    d = dict(row)
    # Deserialise secondary_comps back to a list
    try:
        d["secondary_comps"] = json.loads(d.get("secondary_comps") or "[]")
    except Exception as exc:
        _log.warning("[recipes-db] Failed to parse secondary_comps for recipe id=%s: %s", d.get("id"), exc)
        d["secondary_comps"] = []
    return cast(RecipeRow, d)


class RecipeCatalogue(BaseCatalogue):
    """Read (and build) access to one recipes.db file.

    The eq2db data-interface convention (see AACatalogue / SpellCatalogue):
    the DB path lives on the instance; the shared module-level ``catalogue``
    is the runtime entry point, and tests construct ``RecipeCatalogue(tmp_db)``.
    The pure recipe-domain helpers are staticmethods here so the class is the
    one interface for everything recipe-shaped.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_recipes"])
        conn.execute(_SQL["schema_recipe_classes"])
        # Migrate existing DBs that predate the spell-tier columns
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.executescript(_SQL["indexes_recipes"])

    def _post_init(self, conn: sqlite3.Connection) -> None:
        # Backfill spell-tier columns for any rows that have NULL (covers both
        # freshly-migrated DBs and rows upserted before this version).
        self._backfill_spell_tiers(conn)

    # ── Pure helpers (no DB access — statics so the class is the ONE interface) ──

    @staticmethod
    def _parse_spell_tier(name: str) -> tuple[str | None, str | None]:
        """Return (base_name_lower, crafted_tier) for spell-scroll recipe names.

        e.g. "Lightning Palm III (Expert)" → ("lightning palm iii", "Expert")
             "Fried Cucumber"              → (None, None)
             "Starfire (2H Superior)"      → (None, None)  – not a spell tier
        """
        m = _TIER_RE.match(name)
        if not m:
            return None, None
        tier = m.group(2).strip()
        if tier.lower() not in _SPELL_TIER_SET:
            return None, None
        # Preserve the canonical capitalisation from SPELL_TIERS
        canonical = next(t for t in SPELL_TIERS if t.lower() == tier.lower())
        return m.group(1).strip().lower(), canonical

    @staticmethod
    def recipe_to_row(r: dict) -> dict | None:
        """Convert a raw Census /recipe/ dict into a flat DB row dict.

        Returns None if the record has no usable id.
        """
        rid = _int(r.get("id"))
        if rid is None:
            return None

        name = str(r.get("name") or "")

        # Primary component
        pc = r.get("primarycomponent") or {}
        p_comp = str(pc.get("description") or "").strip() or None
        p_qty = _int(pc.get("quantity"))

        # Secondary components → compact JSON
        sc_raw = r.get("secondarycomponent_list") or []
        sc = [
            {"description": str(c.get("description") or "").strip(), "quantity": _int(c.get("quantity"))}
            for c in sc_raw
            if isinstance(c, dict) and c.get("description")
        ]

        # Fuel component
        fc = r.get("fuelcomponent") or {}
        f_comp = str(fc.get("description") or "").strip() or None
        f_qty = _int(fc.get("quantity"))

        # Output tiers
        out = r.get("output") or {}

        # Spell-scroll tier extraction
        base_name_lower, crafted_tier = RecipeCatalogue._parse_spell_tier(name)

        return {
            "id": rid,
            "crc": _int(r.get("crc")),
            "name": name,
            "name_lower": name.lower(),
            "bench": str(r.get("bench") or "").strip() or None,
            "version": _int(r.get("version")),
            "primary_comp": p_comp,
            "primary_qty": p_qty,
            "secondary_comps": json.dumps(sc),
            "fuel_comp": f_comp,
            "fuel_qty": f_qty,
            "out_unfinished_id": _int(out.get("unfinished")),
            "out_unfinished_count": _int(out.get("unfinished_count")),
            "out_simple_id": _int(out.get("simple")),
            "out_simple_count": _int(out.get("simple_count")),
            "out_worked_id": _int(out.get("worked")),
            "out_worked_count": _int(out.get("worked_count")),
            "out_elaborate_id": _int(out.get("elaborate")),
            "out_elaborate_count": _int(out.get("elaborate_count")),
            "out_formed_id": _int(out.get("formed")),
            "out_formed_count": _int(out.get("formed_count")),
            "base_name_lower": base_name_lower,
            "crafted_tier": crafted_tier,
            "last_update": _int(r.get("last_update")),
        }

    # ── Build (scripts/download_recipes.py) ──────────────────────────────────

    @staticmethod
    def _backfill_spell_tiers(conn: sqlite3.Connection) -> int:
        """Populate base_name_lower / crafted_tier for rows that predate the columns.

        Only touches rows where crafted_tier IS NULL so it is safe to call on every
        startup — it's a no-op once all rows are filled.  Returns the number of rows
        updated.
        """
        rows = conn.execute(_SQL["select_unbackfilled_tiers"]).fetchall()
        if not rows:
            return 0
        updates = []
        for rid, name in rows:
            base, tier = RecipeCatalogue._parse_spell_tier(name)
            if tier is not None:
                updates.append((base, tier, rid))
        if updates:
            conn.executemany(_SQL["backfill_tier"], updates)
            conn.commit()
        return len(updates)

    def upsert_recipes(self, recipes: list[dict], conn: sqlite3.Connection) -> int:
        """Upsert a batch of raw Census recipe dicts. Returns rows inserted/replaced."""
        rows = [self.recipe_to_row(r) for r in recipes]
        rows = [r for r in rows if r is not None]
        conn.executemany(_SQL["upsert"], rows)
        conn.commit()
        return len(rows)

    def recipe_count(self, conn: sqlite3.Connection) -> int:
        return conn.execute(_SQL["count"]).fetchone()[0]

    # ── Lookups (async-friendly via asyncio.to_thread) ───────────────────────

    def find_by_id(self, recipe_id: int) -> RecipeRow | None:
        """Return a recipe row dict for the given ID, or None."""
        row = self._fetchone(_SQL["find_by_id"].format(cols=_SELECT_COLS), (recipe_id,))
        return _row_to_dict(row) if row else None

    def find_by_name(self, name: str) -> list[RecipeRow]:
        """Return recipes whose name matches (exact then LIKE), ordered by name."""
        rows = self._find_exact_then_like(
            _SQL["find_by_name_exact"].format(cols=_SELECT_COLS),
            _SQL["find_by_name_like"].format(cols=_SELECT_COLS),
            name,
        )
        return [_row_to_dict(r) for r in rows]

    def find_by_spell(self, spell_name: str, tier: str) -> list[RecipeRow]:
        """Return recipes that craft a spell scroll for the given base name and tier.

        Args:
            spell_name: The spell's base name, e.g. "Lightning Palm III".
                        Matched case-insensitively against base_name_lower.
            tier:       One of the SPELL_TIERS values, e.g. "Expert".
        """
        rows = self._fetchall(
            _SQL["find_by_spell"].format(cols=_SELECT_COLS),
            (spell_name.lower(), tier),
        )
        return [_row_to_dict(r) for r in rows]

    def find_spells_by_tier(self, spell_names: list[str], tier: str) -> dict[str, RecipeRow]:
        """Bulk lookup: given a list of spell base names, return a mapping of
        lowercased spell name → recipe row for the requested tier.

        Recipes not found in the DB are omitted from the result.  Designed for
        the spellcheck upgrade-path feature (one DB query for N spells).
        """
        if not spell_names:
            return {}
        placeholders = ",".join("?" * len(spell_names))
        params = [n.lower() for n in spell_names] + [tier]
        rows = self._fetchall(
            _SQL["find_spells_by_tier"].format(cols=_SELECT_COLS, placeholders=placeholders),
            params,
        )
        return {r["base_name_lower"]: _row_to_dict(r) for r in rows}

    def find_by_output_id(self, item_id: int) -> list[RecipeRow]:
        """Return all recipes that produce the given item ID at any quality tier."""
        rows = self._fetchall(
            _SQL["find_by_output_id"].format(cols=_SELECT_COLS),
            {"id": item_id},
        )
        return [_row_to_dict(r) for r in rows]


# The shared default instance — every runtime consumer goes through this.
catalogue = RecipeCatalogue()
