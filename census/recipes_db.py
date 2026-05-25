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
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

# Ordered from lowest to highest so tier-comparison logic can use the index.
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


def _db_path() -> Path:
    env = os.getenv("RECIPES_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "recipes" / "recipes.db"


DB_PATH: Path = _db_path()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_META = """
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS recipes (
    -- Identity
    id              INTEGER PRIMARY KEY,
    crc             INTEGER,
    name            TEXT    NOT NULL,
    name_lower      TEXT    NOT NULL,

    -- Classification
    bench           TEXT,       -- crafting station, e.g. "chemistry_table", "forge"
    version         INTEGER,

    -- Primary component (always exactly one)
    primary_comp    TEXT,       -- ingredient display name
    primary_qty     INTEGER,

    -- Secondary components (0 – N) stored as JSON array
    -- [{"description": "Raw Lead", "quantity": 1}, …]
    secondary_comps TEXT    NOT NULL DEFAULT '[]',

    -- Fuel component
    fuel_comp       TEXT,
    fuel_qty        INTEGER,

    -- Output per quality tier: item ID + quantity produced
    out_unfinished_id       INTEGER,
    out_unfinished_count    INTEGER,
    out_simple_id           INTEGER,
    out_simple_count        INTEGER,
    out_worked_id           INTEGER,
    out_worked_count        INTEGER,
    out_elaborate_id        INTEGER,
    out_elaborate_count     INTEGER,
    out_formed_id           INTEGER,
    out_formed_count        INTEGER,

    -- Spell-scroll helpers (NULL for non-spell recipes)
    base_name_lower TEXT,   -- spell name without tier suffix, e.g. "lightning palm iii"
    crafted_tier    TEXT,   -- tier suffix as stored in recipe name, e.g. "Expert"

    -- Metadata
    last_update     INTEGER
);
"""

# Recipe → tradeskill-class mapping. The recipe JSON has no class (only a
# shared crafting station), so the class is derived from recipe-book items
# (typeinfo.classes + typeinfo.recipe_list) by scripts/build_recipe_classes.py.
# Many-to-many: a recipe taught by both an Armorer and a Weaponsmith book gets
# a row for each, so it shows under either class filter.
_CREATE_RECIPE_CLASSES = """
CREATE TABLE IF NOT EXISTS recipe_classes (
    recipe_id  INTEGER NOT NULL,
    class      TEXT    NOT NULL,   -- tradeskill class display name, e.g. "Armorer"
    PRIMARY KEY (recipe_id, class)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_name_lower      ON recipes (name_lower);",
    "CREATE INDEX IF NOT EXISTS idx_bench           ON recipes (bench);",
    # recipe_classes: filter by class, and join back to recipes by id
    "CREATE INDEX IF NOT EXISTS idx_rc_class        ON recipe_classes (class);",
    "CREATE INDEX IF NOT EXISTS idx_rc_recipe       ON recipe_classes (recipe_id);",
    "CREATE INDEX IF NOT EXISTS idx_crc             ON recipes (crc);",
    # Reverse-lookup: which recipe produces a given item?
    "CREATE INDEX IF NOT EXISTS idx_out_formed      ON recipes (out_formed_id);",
    "CREATE INDEX IF NOT EXISTS idx_out_elaborate   ON recipes (out_elaborate_id);",
    "CREATE INDEX IF NOT EXISTS idx_out_simple      ON recipes (out_simple_id);",
    # Composite for station + name searches
    "CREATE INDEX IF NOT EXISTS idx_bench_name      ON recipes (bench, name_lower);",
    # Spell-scroll lookup: base name + tier (primary use-case for spellcheck feature)
    "CREATE INDEX IF NOT EXISTS idx_spell_tier      ON recipes (base_name_lower, crafted_tier);",
]

_UPSERT_SQL = """
INSERT OR REPLACE INTO recipes (
    id, crc, name, name_lower,
    bench, version,
    primary_comp, primary_qty,
    secondary_comps,
    fuel_comp, fuel_qty,
    out_unfinished_id, out_unfinished_count,
    out_simple_id,    out_simple_count,
    out_worked_id,    out_worked_count,
    out_elaborate_id, out_elaborate_count,
    out_formed_id,    out_formed_count,
    base_name_lower, crafted_tier,
    last_update
) VALUES (
    :id, :crc, :name, :name_lower,
    :bench, :version,
    :primary_comp, :primary_qty,
    :secondary_comps,
    :fuel_comp, :fuel_qty,
    :out_unfinished_id, :out_unfinished_count,
    :out_simple_id,     :out_simple_count,
    :out_worked_id,     :out_worked_count,
    :out_elaborate_id,  :out_elaborate_count,
    :out_formed_id,     :out_formed_count,
    :base_name_lower, :crafted_tier,
    :last_update
)
"""


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------


def _int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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
    base_name_lower, crafted_tier = _parse_spell_tier(name)

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


# ---------------------------------------------------------------------------
# DB management
# ---------------------------------------------------------------------------

_MIGRATIONS = [
    "ALTER TABLE recipes ADD COLUMN base_name_lower TEXT;",
    "ALTER TABLE recipes ADD COLUMN crafted_tier    TEXT;",
]


def _backfill_spell_tiers(conn: sqlite3.Connection) -> int:
    """Populate base_name_lower / crafted_tier for rows that predate the columns.

    Only touches rows where crafted_tier IS NULL so it is safe to call on every
    startup — it's a no-op once all rows are filled.  Returns the number of rows
    updated.
    """
    rows = conn.execute("SELECT id, name FROM recipes WHERE crafted_tier IS NULL").fetchall()
    if not rows:
        return 0
    updates = []
    for rid, name in rows:
        base, tier = _parse_spell_tier(name)
        if tier is not None:
            updates.append((base, tier, rid))
    if updates:
        conn.executemany(
            "UPDATE recipes SET base_name_lower = ?, crafted_tier = ? WHERE id = ?",
            updates,
        )
        conn.commit()
    return len(updates)


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables/indexes if missing. Returns an open connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous  = NORMAL;")
    conn.execute(_CREATE_META)
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_RECIPE_CLASSES)
    # Migrate existing DBs that predate the spell-tier columns
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()
    # Backfill spell-tier columns for any rows that have NULL (covers both
    # freshly-migrated DBs and rows upserted before this version).
    _backfill_spell_tiers(conn)
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def upsert_recipes(recipes: list[dict], conn: sqlite3.Connection) -> int:
    """Upsert a batch of raw Census recipe dicts. Returns rows inserted/replaced."""
    rows = [recipe_to_row(r) for r in recipes]
    rows = [r for r in rows if r is not None]
    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()
    return len(rows)


def recipe_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_SELECT_COLS = (
    "id, crc, name, name_lower, bench, version, "
    "primary_comp, primary_qty, secondary_comps, "
    "fuel_comp, fuel_qty, "
    "out_unfinished_id, out_unfinished_count, "
    "out_simple_id, out_simple_count, "
    "out_worked_id, out_worked_count, "
    "out_elaborate_id, out_elaborate_count, "
    "out_formed_id, out_formed_count, "
    "base_name_lower, crafted_tier, "
    "last_update"
)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Deserialise secondary_comps back to a list
    try:
        d["secondary_comps"] = json.loads(d.get("secondary_comps") or "[]")
    except Exception:
        d["secondary_comps"] = []
    return d


def find_by_id(recipe_id: int, path: Path = DB_PATH) -> dict | None:
    """Return a recipe row dict for the given ID, or None."""
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT {_SELECT_COLS} FROM recipes WHERE id = ? LIMIT 1", (recipe_id,)).fetchone()
    return _row_to_dict(row) if row else None


def find_by_name(name: str, path: Path = DB_PATH) -> list[dict]:
    """Return recipes whose name matches (exact then LIKE), ordered by name."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM recipes WHERE name_lower = ? ORDER BY name",
            (name.lower(),),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM recipes WHERE name_lower LIKE ? ORDER BY name",
                (f"%{name.lower()}%",),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_by_spell(
    spell_name: str,
    tier: str,
    path: Path = DB_PATH,
) -> list[dict]:
    """Return recipes that craft a spell scroll for the given base name and tier.

    Args:
        spell_name: The spell's base name, e.g. "Lightning Palm III".
                    Matched case-insensitively against base_name_lower.
        tier:       One of the SPELL_TIERS values, e.g. "Expert".
    """
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM recipes
            WHERE base_name_lower = ?
              AND crafted_tier    = ?
            ORDER BY name
            """,
            (spell_name.lower(), tier),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_spells_by_tier(
    spell_names: list[str],
    tier: str,
    path: Path = DB_PATH,
) -> dict[str, dict]:
    """Bulk lookup: given a list of spell base names, return a mapping of
    lowercased spell name → recipe row for the requested tier.

    Recipes not found in the DB are omitted from the result.  Designed for
    the spellcheck upgrade-path feature (one DB query for N spells).
    """
    if not path.exists() or not spell_names:
        return {}
    placeholders = ",".join("?" * len(spell_names))
    params = [n.lower() for n in spell_names] + [tier]
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM recipes
            WHERE base_name_lower IN ({placeholders})
              AND crafted_tier    = ?
            ORDER BY name
            """,
            params,
        ).fetchall()
    return {r["base_name_lower"]: _row_to_dict(r) for r in rows}


def find_by_output_id(item_id: int, path: Path = DB_PATH) -> list[dict]:
    """Return all recipes that produce the given item ID at any quality tier."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM recipes
            WHERE out_formed_id    = :id
               OR out_elaborate_id = :id
               OR out_worked_id    = :id
               OR out_simple_id    = :id
               OR out_unfinished_id = :id
            ORDER BY name
            """,
            {"id": item_id},
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
