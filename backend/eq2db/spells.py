"""
Local SQLite mirror of the Census /spell/ collection.

Each row is one spell entry — a specific tier of a specific spell (e.g.
"Divine Strike III Adept" is a separate row from "Divine Strike III Master").
The `crc` field groups all tier-variants of the same base spell together.

167 k rows total; download once with scripts/download_spells.py and refresh
whenever spells are patched (rare — typically expansion launches only).

Character spell-check looks up spell IDs in this table so the per-character
Census call can return bare IDs instead of resolved spell objects, making it
faster and removing the c:resolve overhead.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from backend.census._coerce import coerce_float as _float
from backend.census._coerce import coerce_int as _int


class SpellRow(TypedDict, total=False):
    """Row shape returned by ``find_by_id`` / ``find_by_ids`` / ``find_by_crc`` / ``find_by_name``.

    ``total=False`` because a query with a narrower SELECT list (e.g. name-only)
    still returns a valid but incomplete dict. Callers that need a guaranteed
    field should use ``dict.get`` with a sensible default.
    """

    id: int
    name: str
    name_lower: str
    base_name: str
    base_name_lower: str
    tier: int
    tier_name: str
    type: str
    typeid: int
    level: int
    given_by: str
    crc: int
    beneficial: int
    passes_spellcheck: int
    cast_secs: float
    recast_secs: float
    recovery_secs: float
    target_type: str
    aoe_radius: float
    max_targets: int
    description: str
    icon_id: int
    icon_backdrop: int
    effects: str  # JSON-encoded
    last_update: int


_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    env = os.getenv("DB_SPELLS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data" / "spells" / "spells.db"


DB_PATH: Path = _db_path()

# Roman-numeral suffix pattern (I–XX) used for base_name computation.
# Matches a space-separated Roman numeral at the end of a spell name.
_ROMAN_RE = re.compile(
    r"\s+(?:XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)$",
    re.IGNORECASE,
)


def strip_roman(name: str) -> str:
    """Strip a trailing Roman-numeral rank (I–XX) from a spell name."""
    return _ROMAN_RE.sub("", name).strip()


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
CREATE TABLE IF NOT EXISTS spells (
    -- Identity
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    name_lower      TEXT    NOT NULL,

    -- Pre-computed base name (Roman-numeral suffix stripped)
    base_name       TEXT    NOT NULL,
    base_name_lower TEXT    NOT NULL,

    -- Classification
    tier            INTEGER,            -- numeric tier id (1=Novice, 2=Apprentice, 5=Adept …)
    tier_name       TEXT,               -- "Apprentice", "Adept", "Master", "Grandmaster" …
    type            TEXT,               -- "spells", "arts", "pcinnates", "tradeskill" …
    typeid          INTEGER,
    level           INTEGER,            -- minimum level to use
    given_by        TEXT,               -- "any", "class", "alternateadvancement" …
    crc             INTEGER,            -- base-spell grouping key: all tiers of the same spell share a CRC
    beneficial      INTEGER,            -- 1 = beneficial, 0 = hostile

    -- Pre-computed spellcheck eligibility:
    --   level > 0  AND  type IN ('spells','arts')
    --   AND given_by NOT IN ('alternateadvancement','class')
    passes_spellcheck INTEGER NOT NULL DEFAULT 0,

    -- Timing
    cast_secs       REAL,               -- cast_secs_hundredths / 100
    recast_secs     REAL,
    recovery_secs   REAL,               -- recovery_secs_tenths / 10

    -- Targeting
    target_type     TEXT,               -- "self", "single", "group", "ae" …
    aoe_radius      REAL,
    max_targets     INTEGER,

    -- Display
    description     TEXT,
    icon_id         INTEGER,
    icon_backdrop   INTEGER,

    -- Spell effects: JSON array of {description, indentation} objects
    -- Populated from effect_list[] in the Census /spell/ response.
    effects         TEXT,

    -- Metadata
    last_update     INTEGER
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_name_lower       ON spells (name_lower);",
    "CREATE INDEX IF NOT EXISTS idx_base_name_lower  ON spells (base_name_lower);",
    "CREATE INDEX IF NOT EXISTS idx_crc              ON spells (crc);",
    "CREATE INDEX IF NOT EXISTS idx_type             ON spells (type);",
    "CREATE INDEX IF NOT EXISTS idx_given_by         ON spells (given_by);",
    "CREATE INDEX IF NOT EXISTS idx_level            ON spells (level);",
    "CREATE INDEX IF NOT EXISTS idx_tier_name        ON spells (tier_name);",
    "CREATE INDEX IF NOT EXISTS idx_last_update      ON spells (last_update);",
    # Composite indexes for common query patterns
    "CREATE INDEX IF NOT EXISTS idx_sc_level         ON spells (passes_spellcheck, level);",
    "CREATE INDEX IF NOT EXISTS idx_base_tier        ON spells (base_name_lower, tier);",
]

_UPSERT_SQL = """
INSERT OR REPLACE INTO spells (
    id, name, name_lower, base_name, base_name_lower,
    tier, tier_name, type, typeid, level, given_by, crc, beneficial,
    passes_spellcheck,
    cast_secs, recast_secs, recovery_secs,
    target_type, aoe_radius, max_targets,
    description, icon_id, icon_backdrop,
    effects,
    last_update
) VALUES (
    :id, :name, :name_lower, :base_name, :base_name_lower,
    :tier, :tier_name, :type, :typeid, :level, :given_by, :crc, :beneficial,
    :passes_spellcheck,
    :cast_secs, :recast_secs, :recovery_secs,
    :target_type, :aoe_radius, :max_targets,
    :description, :icon_id, :icon_backdrop,
    :effects,
    :last_update
)
"""


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------


def _like_escape(s: str) -> str:
    """Escape SQLite ``LIKE`` wildcards. Will move to ``web/lib/db_helpers.py``
    in Phase 2a — duplicated per-module for the Phase 1 surgical fix."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _str(v) -> str | None:
    if v is None or isinstance(v, dict):
        return None
    s = str(v).strip()
    return s or None


def _passes_spellcheck(row: dict) -> int:
    """Return 1 if this spell row would survive the spellcheck filter, else 0."""
    level = row.get("level") or 0
    typ = row.get("type") or ""
    given_by = row.get("given_by") or ""
    if level <= 0:
        return 0
    if typ not in ("spells", "arts"):
        return 0
    if given_by in ("alternateadvancement", "class"):
        return 0
    return 1


def _parse_effects(spell: dict) -> str:
    """Extract effect_list into a compact JSON string.

    Always returns a JSON string (never None):
      - Non-empty array  → the effect lines
      - '[]'             → processed, genuinely no effects in Census
    """
    raw = spell.get("effect_list")
    if raw is None:
        return "[]"
    if not isinstance(raw, list):
        _log.warning(
            "[spells_db] effect_list for spell %s has unexpected shape %s — returning empty",
            spell.get("id"),
            type(raw).__name__,
        )
        return "[]"
    effects = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        desc = str(e.get("description") or "").strip()
        if not desc:
            continue
        effects.append(
            {
                "description": desc,
                "indentation": int(e.get("indentation") or 0),
            }
        )
    return json.dumps(effects)


def spell_to_row(spell: dict) -> dict:
    """Convert a raw Census /spell/ dict into a flat DB row dict."""
    icon = spell.get("icon") or {}
    cast_h = _int(spell.get("cast_secs_hundredths"))
    rec_t = _int(spell.get("recovery_secs_tenths"))
    desc = spell.get("description")
    if isinstance(desc, dict):
        desc = None  # Census sometimes returns {} for empty descriptions

    name = str(spell.get("name") or "")
    name_lower = name.lower()
    base = strip_roman(name)
    base_lower = base.lower()

    row = {
        "id": _int(spell.get("id")),
        "name": name,
        "name_lower": name_lower,
        "base_name": base,
        "base_name_lower": base_lower,
        "tier": _int(spell.get("tier")),
        "tier_name": _str(spell.get("tier_name")),
        "type": _str(spell.get("type")),
        "typeid": _int(spell.get("typeid")),
        "level": _int(spell.get("level")),
        "given_by": _str(spell.get("given_by")),
        "crc": _int(spell.get("crc")),
        "beneficial": 1 if spell.get("beneficial") == 1 else 0,
        "cast_secs": cast_h / 100.0 if cast_h is not None else None,
        "recast_secs": _float(spell.get("recast_secs")),
        "recovery_secs": rec_t / 10.0 if rec_t is not None else None,
        "target_type": _str(spell.get("target_type")),
        "aoe_radius": _float(spell.get("aoe_radius_meters")),
        "max_targets": _int(spell.get("max_targets")),
        "description": _str(desc),
        "icon_id": _int(icon.get("id")),
        "icon_backdrop": _int(icon.get("backdrop")),
        "effects": _parse_effects(spell),
        "last_update": _int(spell.get("last_update")),
    }
    row["passes_spellcheck"] = _passes_spellcheck(row)
    return row


# ---------------------------------------------------------------------------
# DB management (synchronous — used by download script and web startup)
# ---------------------------------------------------------------------------


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables/indexes if missing. Returns an open connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous  = NORMAL;")
    conn.execute(_CREATE_META)
    conn.execute(_CREATE_TABLE)
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    # Idempotent migration: add effects column if missing (pre-existing DBs)
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(spells)").fetchall()}
    if "effects" not in existing_cols:
        conn.execute("ALTER TABLE spells ADD COLUMN effects TEXT;")
    conn.commit()
    return conn


# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401


def upsert_spells(spells: list[dict], conn: sqlite3.Connection) -> int:
    """Upsert a batch of raw Census spell dicts. Returns the number inserted/replaced."""
    rows = [spell_to_row(s) for s in spells if s.get("id") is not None]
    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()
    find_by_crc.cache_clear()  # BE-236: spell data changed; stale CRC lookups would lie
    return len(rows)


def spell_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM spells").fetchone()[0]


# ---------------------------------------------------------------------------
# Lookup helpers (async-friendly via asyncio.to_thread)
# ---------------------------------------------------------------------------

# All non-rowid columns we select for spell row dicts.
_SELECT_COLS = (
    "id, name, name_lower, base_name, base_name_lower, "
    "tier, tier_name, type, typeid, level, given_by, crc, beneficial, "
    "passes_spellcheck, "
    "cast_secs, recast_secs, recovery_secs, "
    "target_type, aoe_radius, max_targets, "
    "description, icon_id, icon_backdrop, "
    "effects, last_update"
)


def _row_to_dict(row: sqlite3.Row) -> SpellRow:
    return dict(row)  # type: ignore[return-value]


def find_by_id(spell_id: int, path: Path = DB_PATH) -> SpellRow | None:
    """Return a spell row dict for the given ID, or None."""
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT {_SELECT_COLS} FROM spells WHERE id = ? LIMIT 1", (spell_id,)).fetchone()
    return _row_to_dict(row) if row else None


def find_by_ids(spell_ids: list[int], path: Path = DB_PATH) -> dict[int, SpellRow]:
    """Return {spell_id: row_dict} for all matching IDs. Missing IDs are omitted."""
    if not spell_ids or not path.exists():
        return {}
    placeholders = ",".join("?" * len(spell_ids))
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM spells WHERE id IN ({placeholders})",
            spell_ids,
        ).fetchall()
    return {row["id"]: _row_to_dict(row) for row in rows}


@lru_cache(maxsize=4096)
def find_by_crc(crc: int, tier: int | None = None, path: Path = DB_PATH) -> SpellRow | None:
    """Return the spell row for the given CRC and AA rank tier.

    AA nodes reference spells by CRC; multiple rows share a CRC — one per
    rank (tier).  Pass the character's spent tier to get the right values.
    Falls back to the highest available tier if the exact one isn't found.
    """
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if tier is not None:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM spells WHERE crc = ? AND tier = ? LIMIT 1",
                (crc, tier),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        # Fallback: highest available tier
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM spells WHERE crc = ? ORDER BY tier DESC LIMIT 1",
            (crc,),
        ).fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Spell blocklist
# ---------------------------------------------------------------------------

_BLOCKLIST_PATH: Path = Path(__file__).resolve().parent.parent.parent / "data" / "spells" / "blocklist.json"


def unique_highest_entries(entries: list) -> list:
    """For each base spell name + spell_type, keep only the highest-level entry.

    Works on any objects (or dicts) that expose .name/.spell_type/.level
    (SpellEntry) or ["name"]/["type"]/["level"] (raw DB rows).
    """
    best: dict[tuple, object] = {}
    for e in entries:
        if isinstance(e, dict):
            name = e.get("name") or ""
            spell_type = e.get("type") or ""
            level = e.get("level") or 0
        else:
            name = getattr(e, "name", "")
            spell_type = getattr(e, "spell_type", "")
            level = getattr(e, "level", 0) or 0
        key = (strip_roman(name), spell_type)
        if key not in best:
            best[key] = e
        else:
            existing = best[key]
            elevel = (
                (existing.get("level") or 0) if isinstance(existing, dict) else (getattr(existing, "level", 0) or 0)
            )
            if level > elevel:
                best[key] = e
    return list(best.values())


class Blocklist:
    """
    Immutable set of blocked base-spell names that supports both exact matches
    and wildcard patterns (fnmatch-style).

    Examples in blocklist.json:
        "Fighting Chance"   – exact base-name match (Roman suffix stripped by caller)
        "Illusion:*"        – wildcard: blocks any spell whose base name starts
                              with "Illusion:" (spaces, colons, etc. all matched by *)

    Usage is identical to a frozenset — callers just use ``name in blocklist``.
    """

    __slots__ = ("_exact", "_patterns")

    def __init__(self, exact: frozenset[str], patterns: list[str]) -> None:
        self._exact = exact  # lowercased, Roman-stripped literals
        self._patterns = patterns  # lowercased wildcard patterns

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        if name in self._exact:
            return True
        for pat in self._patterns:
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def __bool__(self) -> bool:
        return bool(self._exact or self._patterns)

    def __repr__(self) -> str:
        return f"Blocklist(exact={len(self._exact)}, patterns={len(self._patterns)})"


def load_blocklist(path: Path = _BLOCKLIST_PATH) -> Blocklist:
    """Parse blocklist.json and return a Blocklist.

    Each entry may be:
      - an exact base-spell name  (Roman suffixes stripped automatically)
      - a wildcard pattern        (fnmatch: * matches anything, ? matches one char)

    Re-reads the file on every call so edits take effect without a restart.
    """
    if not path.exists():
        return Blocklist(frozenset(), [])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        names: list[str] = data.get("blocked", []) if isinstance(data, dict) else data

        exact: list[str] = []
        patterns: list[str] = []
        for n in names:
            if not isinstance(n, str):
                continue
            lowered = n.strip().lower()
            if not lowered:
                continue
            if "*" in lowered or "?" in lowered:
                # Wildcard — keep as-is (caller already strips Roman suffixes
                # before the `in` check, so patterns match the stripped name)
                patterns.append(lowered)
            else:
                # Exact — strip Roman suffix so "Fighting Chance" also blocks
                # "Fighting Chance I", "Fighting Chance II", etc.
                exact.append(strip_roman(lowered))

        return Blocklist(frozenset(exact), patterns)
    except Exception as exc:
        _log.warning("[spells_db] Failed to load blocklist: %s", exc)
        return Blocklist(frozenset(), [])


def find_by_name(name: str, path: Path = DB_PATH) -> list[SpellRow]:
    """Return all spell rows whose name matches (exact, then LIKE). Ordered by level."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM spells WHERE name_lower = ? ORDER BY level",
            (name.lower(),),
        ).fetchall()
        if not rows:
            # LIKE fallback — escape user wildcards (BE-006).
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM spells WHERE name_lower LIKE ? ESCAPE '\\' ORDER BY level",
                (f"%{_like_escape(name.lower())}%",),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]
