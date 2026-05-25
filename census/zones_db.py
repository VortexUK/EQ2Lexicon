"""
Local SQLite catalogue of EverQuest 2 zones.

Sourced from ``scripts/dev/eq2_zones.cleaned.json`` (produced by
``scripts/dev/clean_eq2_zones.py`` from a noisy EQ2 wiki dump). Run
``scripts/build_zones_db.py`` to (re)build the DB after the cleaned JSON
changes — idempotent.

Schema (three tables):

  * **zones**         — one row per canonical zone with classification.
  * **zone_types**    — many-to-many zone ↔ type tokens (`raid_x4`,
                        `solo`, `tradeskill`, etc.). A zone can have
                        multiple types (a solo+group instance, for
                        example).
  * **zone_aliases**  — alias name → canonical zone id. ACT logs may
                        emit either form ("Fabled Deathtoll" vs "The
                        Fabled Deathtoll"); the lookup resolves both.

`find_by_name()` is the primary log-lookup entry point — it checks
aliases before falling back to a fuzzy LIKE on the canonical name.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    env = os.getenv("ZONES_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "zones" / "zones.db"


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

_CREATE_ZONES = """
CREATE TABLE IF NOT EXISTS zones (
    -- Identity
    id                      INTEGER PRIMARY KEY,
    name                    TEXT    NOT NULL UNIQUE,
    name_lower              TEXT    NOT NULL,

    -- Expansion attribution
    expansion_short         TEXT    NOT NULL,    -- 'DoF', 'AoM', 'CoE', ...
    expansion_name          TEXT    NOT NULL,    -- 'Desert of Flames', ...
    expansion_year          INTEGER,
    expansion_confidence    TEXT    NOT NULL,    -- 'category', 'live_update', ...
    expansion_source        TEXT,                -- audit trail / reason

    -- Flags
    is_persistent_instance  INTEGER NOT NULL DEFAULT 0,
    is_endless_persistent   INTEGER NOT NULL DEFAULT 0,
    is_tradeskill           INTEGER NOT NULL DEFAULT 0,
    is_pvp                  INTEGER NOT NULL DEFAULT 0,
    is_openworld            INTEGER NOT NULL DEFAULT 0,
    is_instance             INTEGER NOT NULL DEFAULT 0,
    is_live_event           INTEGER NOT NULL DEFAULT 0,
    is_city                 INTEGER NOT NULL DEFAULT 0,
    is_contested            INTEGER NOT NULL DEFAULT 0,
    is_deprecated           INTEGER NOT NULL DEFAULT 0,

    -- Optional metadata
    event_name              TEXT,                -- when is_live_event=1
    wiki_url                TEXT
);
"""

# Many-to-many zone ↔ type. Same pattern as recipe_classes — a zone
# with both Solo and Group variants gets two rows here so a "all group
# zones in RoK" query is one indexed JOIN.
_CREATE_ZONE_TYPES = """
CREATE TABLE IF NOT EXISTS zone_types (
    zone_id  INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    type     TEXT    NOT NULL,    -- 'solo', 'group', 'raid_x4', etc.
    PRIMARY KEY (zone_id, type)
);
"""

# Alias name → canonical zone. ACT logs may emit either form
# ("The Fabled Deathtoll" vs "Fabled Deathtoll"); the find_by_name
# lookup checks aliases before failing.
_CREATE_ZONE_ALIASES = """
CREATE TABLE IF NOT EXISTS zone_aliases (
    alias        TEXT    NOT NULL PRIMARY KEY,
    alias_lower  TEXT    NOT NULL,
    zone_id      INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE
);
"""

# Raid bosses (named encounters) per zone. Sourced from the EQ2 wiki via
# scripts/dev/scrape_eq2i_raids.py — committed as data, rebuilt from
# scripts/dev/eq2_raid_data.json (and an optional overrides file for
# excluding false-positive scrape hits). Position preserves wiki order
# where meaningful. Same name in two zones (e.g. a Fabled variant)
# distinguishes by zone_id.
_CREATE_ZONE_BOSSES = """
CREATE TABLE IF NOT EXISTS zone_bosses (
    id              INTEGER PRIMARY KEY,
    zone_id         INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    mob_name        TEXT    NOT NULL,
    mob_name_lower  TEXT    NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    wiki_url        TEXT,
    UNIQUE (zone_id, mob_name_lower)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_zones_name_lower    ON zones (name_lower);",
    "CREATE INDEX IF NOT EXISTS idx_zones_expansion     ON zones (expansion_short);",
    "CREATE INDEX IF NOT EXISTS idx_zones_event         ON zones (is_live_event, event_name);",
    "CREATE INDEX IF NOT EXISTS idx_zones_tradeskill    ON zones (is_tradeskill);",
    "CREATE INDEX IF NOT EXISTS idx_zone_types_type     ON zone_types (type);",
    "CREATE INDEX IF NOT EXISTS idx_zone_types_zone     ON zone_types (zone_id);",
    "CREATE INDEX IF NOT EXISTS idx_zone_aliases_lower  ON zone_aliases (alias_lower);",
    "CREATE INDEX IF NOT EXISTS idx_zone_aliases_zone   ON zone_aliases (zone_id);",
    "CREATE INDEX IF NOT EXISTS idx_zone_bosses_zone    ON zone_bosses (zone_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_zone_bosses_lower   ON zone_bosses (mob_name_lower);",
]


_UPSERT_ZONE_SQL = """
INSERT INTO zones (
    name, name_lower,
    expansion_short, expansion_name, expansion_year,
    expansion_confidence, expansion_source,
    is_persistent_instance, is_endless_persistent,
    is_tradeskill, is_pvp, is_openworld, is_instance,
    is_live_event, is_city, is_contested, is_deprecated,
    event_name, wiki_url
) VALUES (
    :name, :name_lower,
    :expansion_short, :expansion_name, :expansion_year,
    :expansion_confidence, :expansion_source,
    :is_persistent_instance, :is_endless_persistent,
    :is_tradeskill, :is_pvp, :is_openworld, :is_instance,
    :is_live_event, :is_city, :is_contested, :is_deprecated,
    :event_name, :wiki_url
)
ON CONFLICT(name) DO UPDATE SET
    name_lower             = excluded.name_lower,
    expansion_short        = excluded.expansion_short,
    expansion_name         = excluded.expansion_name,
    expansion_year         = excluded.expansion_year,
    expansion_confidence   = excluded.expansion_confidence,
    expansion_source       = excluded.expansion_source,
    is_persistent_instance = excluded.is_persistent_instance,
    is_endless_persistent  = excluded.is_endless_persistent,
    is_tradeskill          = excluded.is_tradeskill,
    is_pvp                 = excluded.is_pvp,
    is_openworld           = excluded.is_openworld,
    is_instance            = excluded.is_instance,
    is_live_event          = excluded.is_live_event,
    is_city                = excluded.is_city,
    is_contested           = excluded.is_contested,
    is_deprecated          = excluded.is_deprecated,
    event_name             = excluded.event_name,
    wiki_url               = excluded.wiki_url
"""

_SELECT_COLS = (
    "id, name, name_lower, "
    "expansion_short, expansion_name, expansion_year, "
    "expansion_confidence, expansion_source, "
    "is_persistent_instance, is_endless_persistent, "
    "is_tradeskill, is_pvp, is_openworld, is_instance, "
    "is_live_event, is_city, is_contested, is_deprecated, "
    "event_name, wiki_url"
)


# ---------------------------------------------------------------------------
# Row conversion (cleaned JSON → DB row + type/alias side tables)
# ---------------------------------------------------------------------------


def zone_to_row(z: dict) -> dict:
    """Flatten a cleaned-JSON zone record into the columns of the zones
    table. `types` and `aliases` are handled separately by upsert_zones."""
    name = z["name"]
    cls = z["classification"]
    exp = cls["expansion"]
    return {
        "name": name,
        "name_lower": name.lower(),
        "expansion_short": exp["short"],
        "expansion_name": exp["name"],
        "expansion_year": exp.get("year"),
        "expansion_confidence": exp["confidence"],
        "expansion_source": exp.get("source") or "",
        "is_persistent_instance": int(bool(cls.get("is_persistent_instance"))),
        "is_endless_persistent": int(bool(cls.get("is_endless_persistent"))),
        "is_tradeskill": int(bool(cls.get("is_tradeskill"))),
        "is_pvp": int(bool(cls.get("is_pvp"))),
        "is_openworld": int(bool(cls.get("is_openworld"))),
        "is_instance": int(bool(cls.get("is_instance"))),
        "is_live_event": int(bool(cls.get("is_live_event"))),
        "is_city": int(bool(cls.get("is_city"))),
        "is_contested": int(bool(cls.get("is_contested"))),
        "is_deprecated": int(bool(cls.get("is_deprecated"))),
        "event_name": cls.get("event_name") or None,
        # The first source_pages entry is the wiki URL for the canonical
        # record (variants may have multiple — those go into aliases).
        "wiki_url": (z.get("source_pages") or [None])[0],
    }


# ---------------------------------------------------------------------------
# DB management
# ---------------------------------------------------------------------------


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables/indexes if missing. Returns an open connection.

    Foreign keys are enabled per-connection so ON DELETE CASCADE on the
    zone_types / zone_aliases child tables actually fires.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous  = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(_CREATE_META)
    conn.execute(_CREATE_ZONES)
    conn.execute(_CREATE_ZONE_TYPES)
    conn.execute(_CREATE_ZONE_ALIASES)
    conn.execute(_CREATE_ZONE_BOSSES)
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def upsert_zones(zones: list[dict], conn: sqlite3.Connection) -> int:
    """Bulk upsert from cleaned-JSON zone records.

    For each input zone:
      * Insert/replace the row in `zones`.
      * Replace its rows in `zone_types` (so removed types disappear).
      * Replace its rows in `zone_aliases` (so removed aliases disappear).

    Atomic per-zone within a single transaction. Re-runnable.
    """
    n = 0
    with conn:  # single transaction for the whole batch
        for z in zones:
            row = zone_to_row(z)
            conn.execute(_UPSERT_ZONE_SQL, row)
            zone_id = conn.execute("SELECT id FROM zones WHERE name = ?", (z["name"],)).fetchone()[0]

            # Reset and repopulate types + aliases for this zone. Cheaper
            # than diffing on every rebuild.
            conn.execute("DELETE FROM zone_types WHERE zone_id = ?", (zone_id,))
            types = z["classification"].get("types") or []
            if types:
                conn.executemany(
                    "INSERT INTO zone_types (zone_id, type) VALUES (?, ?)",
                    [(zone_id, t) for t in types],
                )

            conn.execute("DELETE FROM zone_aliases WHERE zone_id = ?", (zone_id,))
            aliases = z.get("aliases") or []
            if aliases:
                conn.executemany(
                    "INSERT INTO zone_aliases (alias, alias_lower, zone_id) VALUES (?, ?, ?)",
                    [(a, a.lower(), zone_id) for a in aliases],
                )
            n += 1
    return n


def zone_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def _hydrate_zone(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Convert a zones row + sub-queries for types/aliases/bosses into a dict."""
    d = dict(row)
    # Booleans as Python bools for ergonomics
    for k in (
        "is_persistent_instance",
        "is_endless_persistent",
        "is_tradeskill",
        "is_pvp",
        "is_openworld",
        "is_instance",
        "is_live_event",
        "is_city",
        "is_contested",
        "is_deprecated",
    ):
        d[k] = bool(d.get(k))
    d["types"] = [r[0] for r in conn.execute("SELECT type FROM zone_types WHERE zone_id = ? ORDER BY type", (d["id"],))]
    d["aliases"] = [
        r[0] for r in conn.execute("SELECT alias FROM zone_aliases WHERE zone_id = ? ORDER BY alias", (d["id"],))
    ]
    d["bosses"] = [
        {"mob_name": r[0], "position": r[1], "wiki_url": r[2]}
        for r in conn.execute(
            "SELECT mob_name, position, wiki_url FROM zone_bosses WHERE zone_id = ? ORDER BY position, mob_name",
            (d["id"],),
        )
    ]
    return d


def find_by_name(name: str, path: Path = DB_PATH) -> dict | None:
    """Resolve a zone by name.

    Lookup order:
      1. Exact canonical match (case-insensitive).
      2. Exact alias match (case-insensitive) → returns the canonical zone.

    Returns None on miss. ACT log lookups should call this — the alias
    table covers the "with-The vs without-The" wiki dup pairs.
    """
    if not path.exists() or not name:
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM zones WHERE name_lower = ? LIMIT 1",
            (name.lower(),),
        ).fetchone()
        if row is None:
            alias_row = conn.execute(
                "SELECT zone_id FROM zone_aliases WHERE alias_lower = ? LIMIT 1",
                (name.lower(),),
            ).fetchone()
            if alias_row is None:
                return None
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM zones WHERE id = ?",
                (alias_row[0],),
            ).fetchone()
            if row is None:
                return None  # orphaned alias — shouldn't happen with FKs
        return _hydrate_zone(conn, row)


def list_by_expansion(
    short: str,
    type_filter: str | None = None,
    path: Path = DB_PATH,
) -> list[dict]:
    """All zones in an expansion. Optionally filter to a single type
    token (e.g. 'raid_x4', 'group', 'tradeskill'). Ordered by name."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if type_filter:
            rows = conn.execute(
                f"""
                SELECT {_SELECT_COLS} FROM zones
                WHERE expansion_short = ?
                  AND id IN (SELECT zone_id FROM zone_types WHERE type = ?)
                ORDER BY name
                """,
                (short, type_filter),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM zones WHERE expansion_short = ? ORDER BY name",
                (short,),
            ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


def list_by_event(event_name: str, path: Path = DB_PATH) -> list[dict]:
    """All zones for a recurring event (e.g. 'Tinkerfest', 'Frostfell')."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM zones WHERE is_live_event = 1 AND event_name = ? ORDER BY name",
            (event_name,),
        ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


def list_by_type(type_token: str, path: Path = DB_PATH) -> list[dict]:
    """All zones tagged with a given type token across all expansions."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM zones
            WHERE id IN (SELECT zone_id FROM zone_types WHERE type = ?)
            ORDER BY name
            """,
            (type_token,),
        ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


# ---------------------------------------------------------------------------
# Raid-boss helpers
# ---------------------------------------------------------------------------


def replace_bosses_for_zone(
    conn: sqlite3.Connection,
    zone_id: int,
    bosses: list[dict],
) -> int:
    """Replace the bosses list for a zone. Atomic per-zone.

    Each input dict shape: ``{"mob_name": str, "position": int,
    "wiki_url": str | None}``. Order in the list is preserved via the
    ``position`` field. Returns the number of bosses written.

    Re-runnable: a second call with the same zone wipes and rewrites
    so removed bosses drop cleanly.
    """
    conn.execute("DELETE FROM zone_bosses WHERE zone_id = ?", (zone_id,))
    if not bosses:
        return 0
    rows = [
        (zone_id, b["mob_name"], b["mob_name"].lower(), int(b.get("position", 0)), b.get("wiki_url")) for b in bosses
    ]
    conn.executemany(
        "INSERT INTO zone_bosses (zone_id, mob_name, mob_name_lower, position, wiki_url) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def list_bosses_for_zone(zone_name: str, path: Path = DB_PATH) -> list[dict]:
    """All raid bosses in a zone (looked up by canonical name OR alias).

    Returns ``[{"mob_name", "position", "wiki_url"}, ...]`` sorted by
    position then name. Empty list if zone unknown or has no bosses.
    """
    if not path.exists() or not zone_name:
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        # Resolve via canonical, fall back to alias
        row = conn.execute("SELECT id FROM zones WHERE name_lower = ?", (zone_name.lower(),)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT zone_id AS id FROM zone_aliases WHERE alias_lower = ?",
                (zone_name.lower(),),
            ).fetchone()
        if row is None:
            return []
        bosses = conn.execute(
            "SELECT mob_name, position, wiki_url FROM zone_bosses WHERE zone_id = ? ORDER BY position, mob_name",
            (row["id"],),
        ).fetchall()
        return [{"mob_name": b["mob_name"], "position": b["position"], "wiki_url": b["wiki_url"]} for b in bosses]


def find_zones_by_boss(mob_name: str, path: Path = DB_PATH) -> list[dict]:
    """Reverse lookup: which zone(s) host a given raid boss?

    Returns a list because the same mob name can appear in multiple
    zones (e.g. Mayong Mistmoore exists in both the Castle Mistmoore
    raid AND the Inner Sanctum raid; Fabled variants reuse names).
    """
    if not path.exists() or not mob_name:
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM zones
            WHERE id IN (
                SELECT zone_id FROM zone_bosses WHERE mob_name_lower = ?
            )
            ORDER BY name
            """,
            (mob_name.lower(),),
        ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


def expansion_counts(path: Path = DB_PATH) -> dict[str, int]:
    """Diagnostic: zones per expansion short. Used by the build report."""
    if not path.exists():
        return {}
    with sqlite3.connect(path) as conn:
        return dict(
            conn.execute("SELECT expansion_short, COUNT(*) FROM zones GROUP BY expansion_short ORDER BY 2 DESC")
        )
