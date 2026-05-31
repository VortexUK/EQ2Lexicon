"""
Local SQLite catalogue of EverQuest 2 zones.

Sourced from ``scripts/dev/eq2_zones.cleaned.json`` (produced by
``scripts/dev/clean_eq2_zones.py`` from a noisy EQ2 wiki dump). Run
``scripts/build_zones_db.py`` to (re)build the DB after the cleaned JSON
changes — idempotent.

Schema (five tables):

  * **zones**                  — one row per canonical zone with
                                 classification.
  * **zone_types**             — many-to-many zone ↔ type tokens
                                 (`raid_x4`, `solo`, etc.). A zone can
                                 have multiple types.
  * **zone_aliases**           — alias name → canonical zone id. ACT
                                 logs may emit either form ("Fabled
                                 Deathtoll" vs "The Fabled Deathtoll").
  * **zone_encounters**        — raid bosses per zone, one row per
                                 named encounter (solo OR group),
                                 with optional stage label and the
                                 curator-supplied position.
  * **zone_encounter_mobs**    — individual mob names inside an
                                 encounter. Solo encounters get one
                                 row; a 4-mob group gets four. Indexed
                                 lowercased for fast reverse lookup.

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
    env = os.getenv("DB_ZONES_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data" / "zones" / "zones.db"


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

# Raid encounters per zone — hand-curated from EQ2i (see
# scripts/dev/eq2_raid_bosses.review.txt). Each row is a single named
# *encounter*: usually one mob, but EQ2 has plenty of group encounters
# where 2-4 mobs spawn together (e.g. The Protector's Realm's "Ludmila
# Kystov + Jracol Binari + Blorgok the Brutal + Meldrath Kloktik" all
# at once). The individual mob names live in the zone_encounter_mobs
# join table below so reverse-lookup ("what zone is mob X in?") stays
# an indexed query.
#
# `encounter_name` is the display label as written by the curator —
# joined names for groups, single name for solo bosses. `stage` is an
# optional grouping label ("Wing 1", "First Floor", etc.) for the
# multi-stage raids like Veeshan's Peak and The Emerald Halls. Position
# preserves the curator's intended order (which is typically the order
# you encounter them in the zone).
_CREATE_ZONE_ENCOUNTERS = """
CREATE TABLE IF NOT EXISTS zone_encounters (
    id              INTEGER PRIMARY KEY,
    zone_id         INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    encounter_name  TEXT    NOT NULL,             -- display: "Adkar Vyx" or
                                                  -- "Ludmila Kystov, Jracol Binari, ..."
    position        INTEGER NOT NULL,             -- order within the zone
    stage           TEXT,                         -- "Wing 1", "First Floor", etc.
    wiki_url        TEXT,
    UNIQUE (zone_id, position)
);
"""

# Per-encounter individual mob names. A solo-boss encounter has one
# row; a 4-mob group has four. Lower-cased column is what reverse
# lookups (find_zones_by_boss) hit.
_CREATE_ZONE_ENCOUNTER_MOBS = """
CREATE TABLE IF NOT EXISTS zone_encounter_mobs (
    id              INTEGER PRIMARY KEY,
    encounter_id    INTEGER NOT NULL REFERENCES zone_encounters(id) ON DELETE CASCADE,
    mob_name        TEXT    NOT NULL,
    mob_name_lower  TEXT    NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0    -- position within the encounter
);
"""

# Admin-curated featured raid expansions for the /raids page. An expansion
# only appears on /raids if either (a) it has a row here, or (b) it has at
# least one row in featured_raid_zones (implicit). Decoupled from
# featured_raid_zones because an admin may want to add an empty expansion
# placeholder before they pick which raid zones go in it.
_CREATE_FEATURED_RAID_EXPANSIONS = """
CREATE TABLE IF NOT EXISTS featured_raid_expansions (
    expansion_short TEXT PRIMARY KEY,
    added_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);
"""

# Admin-curated featured raid zones — explicit per-zone allowlist of which
# raid_x4 / raid_x2 zones appear under each expansion on /raids. The raid_x4
# seed in zones.db is polluted with obscure content; this table lets the
# admin pick exactly which raid zones to feature. Removing a row hides the
# zone from /raids but preserves its zone_encounters boss data — re-adding
# the zone restores everything.
_CREATE_FEATURED_RAID_ZONES = """
CREATE TABLE IF NOT EXISTS featured_raid_zones (
    zone_id INTEGER PRIMARY KEY REFERENCES zones(id) ON DELETE CASCADE,
    added_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);
"""

# Admin-controlled category ordering per expansion. A row exists when an
# admin has used a category name at least once for a zone in this expansion;
# position determines the lane order on /raids. The implicit "Uncategorised"
# lane (zones whose featured_raid_zones.category IS NULL) is NEVER stored
# here — it's always pinned at the top by the frontend.
_CREATE_FEATURED_RAID_CATEGORIES = """
CREATE TABLE IF NOT EXISTS featured_raid_categories (
    expansion_short TEXT NOT NULL,
    name            TEXT NOT NULL,
    position        INTEGER NOT NULL,
    PRIMARY KEY (expansion_short, name)
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
    "CREATE INDEX IF NOT EXISTS idx_zone_enc_zone       ON zone_encounters (zone_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_zone_enc_mobs_enc   ON zone_encounter_mobs (encounter_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_zone_enc_mobs_lower ON zone_encounter_mobs (mob_name_lower);",
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
    conn.execute(_CREATE_ZONE_ENCOUNTERS)
    conn.execute(_CREATE_ZONE_ENCOUNTER_MOBS)
    conn.execute(_CREATE_FEATURED_RAID_EXPANSIONS)
    conn.execute(_CREATE_FEATURED_RAID_ZONES)
    conn.execute(_CREATE_FEATURED_RAID_CATEGORIES)
    # Migration: zone categories + position for drag-reorder.
    # Idempotent — already-applied schemas raise OperationalError on the
    # duplicate-column attempt, which we swallow.
    for stmt in (
        "ALTER TABLE featured_raid_zones ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE featured_raid_zones ADD COLUMN category TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    # Migration: drop the pre-v2 zone_bosses table if it lingers from
    # an older DB build. No need to preserve data — bosses are always
    # rebuilt from the curated source file.
    conn.execute("DROP TABLE IF EXISTS zone_bosses;")
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    # One-time data normalization (idempotent): legacy `encounter_name`
    # values were the comma-joined display of every mob in the encounter
    # ("Ire, Malevolence"). The web roster editor treats encounter_name
    # as the PRIMARY mob's name (kept in sync with the mob at
    # position 0). Rewrite any comma-containing row to its position-0
    # mob name; rows without any mobs are left untouched.
    # NOTE: Not version-gated — this UPDATE is cheap (only touches rows
    # with commas in the name) and must remain idempotent across multiple
    # init_db calls (see test_init_db_normalizes_comma_joined_encounter_name).
    conn.execute(
        """
        UPDATE zone_encounters
           SET encounter_name = (
                   SELECT mob_name FROM zone_encounter_mobs m
                    WHERE m.encounter_id = zone_encounters.id
                    ORDER BY position ASC
                    LIMIT 1
               )
         WHERE encounter_name LIKE '%,%'
           AND EXISTS (
                   SELECT 1 FROM zone_encounter_mobs m
                    WHERE m.encounter_id = zone_encounters.id
               )
        """
    )
    # One-time data normalization (idempotent): strip the wiki-import
    # " (Zone)" disambiguator suffix from zone names (e.g. "Kurn's Tower
    # (Zone)" → "Kurn's Tower"). EQ2i uses the parenthetical to
    # disambiguate a wiki article from the in-game zone of the same name;
    # the in-game logs and our UI both use the bare name. The old name
    # is also inserted as an alias so anything historically referencing
    # the parenthesised form still resolves via find_by_name. Idempotent:
    # subsequent runs match zero rows (LIKE filter no longer hits the
    # already-cleaned names).
    conn.execute(
        """
        INSERT OR IGNORE INTO zone_aliases (alias, alias_lower, zone_id)
        SELECT name, name_lower, id
          FROM zones
         WHERE name LIKE '% (Zone)%'
        """
    )
    conn.execute(
        """
        UPDATE zones
           SET name       = REPLACE(name,       ' (Zone)', ''),
               name_lower = REPLACE(name_lower, ' (zone)', '')
         WHERE name LIKE '% (Zone)%'
        """
    )
    conn.commit()
    return conn


# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401


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
    # Encounters: ordered list of named bosses with optional stage
    # label. Each carries a `mobs` array — single-mob encounters get
    # one entry, group encounters carry all the individual mob names.
    encounter_rows = conn.execute(
        "SELECT id, encounter_name, position, stage, wiki_url FROM zone_encounters WHERE zone_id = ? ORDER BY position",
        (d["id"],),
    ).fetchall()
    d["bosses"] = []
    for er in encounter_rows:
        mobs = [
            {"id": r["id"], "mob_name": r["mob_name"], "position": r["position"]}
            for r in conn.execute(
                "SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position",
                (er["id"],),
            )
        ]
        d["bosses"].append(
            {
                "id": er["id"],
                "encounter_name": er["encounter_name"],
                "position": er["position"],
                "stage": er["stage"],
                "wiki_url": er["wiki_url"],
                "mobs": mobs,
            }
        )
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
# Raid-encounter helpers
# ---------------------------------------------------------------------------


def replace_bosses_for_zone(
    conn: sqlite3.Connection,
    zone_id: int,
    encounters: list[dict],
) -> int:
    """Replace the encounters list for a zone. Atomic per-zone.

    Each input dict shape:
        {
            "encounter_name": "Adkar Vyx" or "Ludmila Kystov, Jracol ...",
            "position": int,                  # order within the zone
            "stage": str | None,              # "Wing 1", "First Floor", ...
            "wiki_url": str | None,
            "mobs": [                         # one entry per individual mob
                {"mob_name": "Adkar Vyx", "position": 0},
                ...
            ],
        }

    Re-runnable: wipes and rewrites both child tables so removed
    encounters and removed group mobs both disappear cleanly. Returns
    the number of *encounters* written (not individual mobs).
    """
    conn.execute("DELETE FROM zone_encounters WHERE zone_id = ?", (zone_id,))
    if not encounters:
        return 0
    for enc in encounters:
        cur = conn.execute(
            "INSERT INTO zone_encounters (zone_id, encounter_name, position, stage, wiki_url) VALUES (?, ?, ?, ?, ?)",
            (
                zone_id,
                enc["encounter_name"],
                int(enc["position"]),
                enc.get("stage"),
                enc.get("wiki_url"),
            ),
        )
        encounter_id = int(cur.lastrowid or 0)
        mobs = enc.get("mobs") or []
        if not mobs:
            # Defensive: an encounter with no listed mobs gets one mob
            # synthesised from the display name so reverse lookup still
            # works. Curator-curated data shouldn't hit this branch.
            mobs = [{"mob_name": enc["encounter_name"], "position": 0}]
        conn.executemany(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) VALUES (?, ?, ?, ?)",
            [
                (
                    encounter_id,
                    m["mob_name"],
                    m["mob_name"].lower(),
                    int(m.get("position", 0)),
                )
                for m in mobs
            ],
        )
    return len(encounters)


def list_bosses_for_zone(zone_name: str, path: Path = DB_PATH) -> list[dict]:
    """All raid encounters in a zone (looked up by canonical name OR alias).

    Returns a list of dicts in curator order. Each entry has
    ``encounter_name``, ``position``, ``stage`` (or None), ``wiki_url``
    (or None), and a ``mobs`` array of ``{"mob_name", "position"}``.
    Empty list if zone unknown or has no bosses.
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
        zone_id = row["id"]
        encounter_rows = conn.execute(
            "SELECT id, encounter_name, position, stage, wiki_url "
            "FROM zone_encounters WHERE zone_id = ? ORDER BY position",
            (zone_id,),
        ).fetchall()
        out: list[dict] = []
        for er in encounter_rows:
            mobs = [
                {"id": r["id"], "mob_name": r["mob_name"], "position": r["position"]}
                for r in conn.execute(
                    "SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position",
                    (er["id"],),
                )
            ]
            out.append(
                {
                    "id": er["id"],
                    "encounter_name": er["encounter_name"],
                    "position": er["position"],
                    "stage": er["stage"],
                    "wiki_url": er["wiki_url"],
                    "mobs": mobs,
                }
            )
        return out


def find_zones_by_boss(mob_name: str, path: Path = DB_PATH) -> list[dict]:
    """Reverse lookup: which zone(s) host a given raid boss?

    Joins through zone_encounter_mobs so individual mob names inside a
    group encounter all resolve (querying for any one of the four mobs
    in a 4-mob group finds the encounter and its zone).

    Returns a list because the same mob name can appear in multiple
    zones (Fabled variants, multi-instance bosses).
    """
    if not path.exists() or not mob_name:
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM zones
            WHERE id IN (
                SELECT e.zone_id FROM zone_encounters e
                INNER JOIN zone_encounter_mobs m ON m.encounter_id = e.id
                WHERE m.mob_name_lower = ?
            )
            ORDER BY name
            """,
            (mob_name.lower(),),
        ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


def list_expansions(path: Path = DB_PATH) -> list[dict]:
    """Return distinct expansions ordered newest first (by expansion_year DESC).

    Each entry is ``{"short": expansion_short, "name": expansion_name}``.
    Returns [] when zones.db is missing or the zones table does not yet exist
    (graceful degradation — the admin endpoint must never 500 on a missing DB).
    """
    if not path.exists():
        return []
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT expansion_short, expansion_name, expansion_year "
                "FROM zones "
                "WHERE expansion_short IS NOT NULL "
                "ORDER BY expansion_year DESC"
            ).fetchall()
        # De-duplicate by short (same short can have multiple rows with the same year).
        seen: set[str] = set()
        result: list[dict] = []
        for short, name, _year in rows:
            if short not in seen:
                seen.add(short)
                result.append({"short": short, "name": name})
        return result
    except sqlite3.OperationalError:
        # zones table may not exist yet (e.g. pre-seeded zones.db stub).
        return []


def expansion_counts(path: Path = DB_PATH) -> dict[str, int]:
    """Diagnostic: zones per expansion short. Used by the build report."""
    if not path.exists():
        return {}
    with sqlite3.connect(path) as conn:
        return dict(
            conn.execute("SELECT expansion_short, COUNT(*) FROM zones GROUP BY expansion_short ORDER BY 2 DESC")
        )


# ---------------------------------------------------------------------------
# Editable encounter helpers
# ---------------------------------------------------------------------------


def _row_to_encounter(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Shape an encounter row as list_bosses_for_zone returns one."""
    mobs = [
        {"mob_name": r["mob_name"], "position": r["position"]}
        for r in conn.execute(
            "SELECT mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position ASC",
            (row["id"],),
        )
    ]
    return {
        "id": row["id"],
        "zone_id": row["zone_id"],
        "encounter_name": row["encounter_name"],
        "position": row["position"],
        "stage": row["stage"],
        "wiki_url": row["wiki_url"],
        "mobs": mobs,
    }


def _zone_name_and_expansion(zone_id: int, path: Path) -> tuple[str | None, str | None]:
    """Canonical zone name + expansion for the raids_db mirror."""
    with sqlite3.connect(path) as conn:
        r = conn.execute("SELECT name, expansion_short FROM zones WHERE id = ?", (zone_id,)).fetchone()
        return (r[0], r[1]) if r else (None, None)


def add_encounter(
    zone_id: int,
    *,
    primary_mob: str,
    position: int | None = None,
    stage: str | None = None,
    wiki_url: str | None = None,
    path: Path = DB_PATH,
) -> dict:
    """Append a new encounter to a zone with a single primary mob at position 0.

    If `position` is None, appends after the current max. If provided, inserts
    at that slot — caller is responsible for it being free (UNIQUE(zone_id,
    position) will raise otherwise)."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if position is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM zone_encounters WHERE zone_id = ?",
                (zone_id,),
            ).fetchone()
            position = int(row["p"])
        cur = conn.execute(
            "INSERT INTO zone_encounters (zone_id, encounter_name, position, stage, wiki_url) VALUES (?, ?, ?, ?, ?)",
            (zone_id, primary_mob, position, stage, wiki_url),
        )
        enc_id = cur.lastrowid
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) VALUES (?, ?, ?, 0)",
            (enc_id, primary_mob, primary_mob.lower()),
        )
        conn.commit()
        encounter_row = conn.execute(
            "SELECT id, zone_id, encounter_name, position, stage, wiki_url FROM zone_encounters WHERE id = ?",
            (enc_id,),
        ).fetchone()
        return _row_to_encounter(conn, encounter_row)


# Sentinel used by update_encounter to distinguish "leave unchanged" from
# "explicitly set to None". `stage = None` should clear the stage; `stage`
# omitted entirely should keep whatever was there.
_UNSET: object = object()


def update_encounter(
    encounter_id: int,
    *,
    primary_mob: str | None = None,
    stage: str | None = _UNSET,  # type: ignore[assignment]
    wiki_url: str | None = _UNSET,  # type: ignore[assignment]
    path: Path = DB_PATH,
) -> dict:
    """Edit encounter metadata. When `primary_mob` is given, also renames the
    position-0 mob in zone_encounter_mobs (the canonical primary) and mirrors
    the rename onto raids_db.raid_encounters (if a row exists there)."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            "SELECT id, zone_id, encounter_name, position, stage, wiki_url FROM zone_encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"zone_encounter {encounter_id} not found")
        new_name = primary_mob if primary_mob is not None else row["encounter_name"]
        new_stage = row["stage"] if stage is _UNSET else stage
        new_wiki = row["wiki_url"] if wiki_url is _UNSET else wiki_url
        conn.execute(
            "UPDATE zone_encounters SET encounter_name = ?, stage = ?, wiki_url = ? WHERE id = ?",
            (new_name, new_stage, new_wiki, encounter_id),
        )
        if primary_mob is not None:
            conn.execute(
                "UPDATE zone_encounter_mobs SET mob_name = ?, mob_name_lower = ? "
                "WHERE encounter_id = ? AND position = 0",
                (primary_mob, primary_mob.lower(), encounter_id),
            )
        conn.commit()
        updated = conn.execute(
            "SELECT id, zone_id, encounter_name, position, stage, wiki_url FROM zone_encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
        result = _row_to_encounter(conn, updated)
    # Mirror rename onto raids_db (if a row exists there). Deferred import
    # to avoid any import-time cycle.
    if primary_mob is not None:
        zone_name, _exp = _zone_name_and_expansion(row["zone_id"], path)
        if zone_name is not None:
            from backend.eq2db import raids as _raids_db

            # init_db is idempotent and ensures the raids_db schema exists
            # even on a fresh deploy / fresh test env where raids.db has no
            # tables yet — without this the mirror call hits "no such table".
            with _raids_db.init_db() as rconn:
                _raids_db.rename_raid_encounter_if_exists(
                    rconn,
                    zone_name=zone_name,
                    old_mob_name=row["encounter_name"],
                    new_mob_name=primary_mob,
                )
                rconn.commit()
    return result


def reorder_encounters(
    zone_id: int,
    ordered_encounter_ids: list[int],
    path: Path = DB_PATH,
) -> None:
    """Atomically renumber the zone's encounters to 1..N matching the given
    order. The list MUST be a complete permutation of that zone's current
    encounter ids (no duplicates, no missing ids, no foreign ids) — raises
    ValueError otherwise. The two-phase write (negative sentinels then
    1..N) is needed because UNIQUE(zone_id, position) would otherwise
    reject mid-update collisions. After the zones.db commit, mirrors the
    new positions onto any matching raids_db.raid_encounters rows."""
    if len(ordered_encounter_ids) != len(set(ordered_encounter_ids)):
        raise ValueError("ordered_encounter_ids contains duplicates")
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        current = {
            r["id"]: (r["encounter_name"], r["position"])
            for r in conn.execute(
                "SELECT id, encounter_name, position FROM zone_encounters WHERE zone_id = ?",
                (zone_id,),
            )
        }
        if set(ordered_encounter_ids) != set(current.keys()):
            missing = set(current.keys()) - set(ordered_encounter_ids)
            extra = set(ordered_encounter_ids) - set(current.keys())
            raise ValueError(
                f"reorder_encounters: not a permutation of zone {zone_id}'s "
                f"encounters (missing={sorted(missing)}, extra={sorted(extra)})"
            )
        zone_row = conn.execute("SELECT name FROM zones WHERE id = ?", (zone_id,)).fetchone()
        zone_name = zone_row["name"] if zone_row else None
        with conn:  # single transaction
            # Two-phase write to dodge the UNIQUE(zone_id, position) collision
            # on mid-update overlap: negative sentinels first, then 1..N.
            for tmp_neg, enc_id in enumerate(ordered_encounter_ids, start=1):
                conn.execute(
                    "UPDATE zone_encounters SET position = ? WHERE id = ?",
                    (-tmp_neg, enc_id),
                )
            for new_pos, enc_id in enumerate(ordered_encounter_ids, start=1):
                conn.execute(
                    "UPDATE zone_encounters SET position = ? WHERE id = ?",
                    (new_pos, enc_id),
                )
    # Mirror onto raids_db: for each encounter whose primary mob has a
    # raid_encounters row, update its position. We look up by the CURRENT
    # encounter_name (which is the primary mob name post-Task-1 normalization).
    if zone_name is None:
        return
    from backend.eq2db import raids as _raids_db

    # init_db is idempotent and self-heals a fresh raids.db (CI/test env).
    with _raids_db.init_db() as rconn:
        for new_pos, enc_id in enumerate(ordered_encounter_ids, start=1):
            name, _old_pos = current[enc_id]
            _raids_db.update_raid_encounter_if_exists(
                rconn,
                zone_name=zone_name,
                mob_name=name,
                position=new_pos,
            )
        rconn.commit()


# ---------------------------------------------------------------------------
# Mob helpers (position 0 = primary; positions 1..N = siblings)
# ---------------------------------------------------------------------------


def list_mobs(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    """All mobs for an encounter, ordered by position. Each row is
    {id, mob_name, position}."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            {"id": r["id"], "mob_name": r["mob_name"], "position": r["position"]}
            for r in conn.execute(
                "SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position ASC",
                (encounter_id,),
            )
        ]


def _mirror_primary_rename(encounter_id: int, old_name: str, new_name: str, path: Path) -> None:
    """If the parent encounter's zone has a raid_encounters mirror row
    keyed by (zone_name, old_name), rename it to new_name. Looks up the
    zone from the encounter row."""
    if old_name == new_name:
        return
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT zone_id FROM zone_encounters WHERE id = ?", (encounter_id,)).fetchone()
        if row is None:
            return
        zone_name, _exp = _zone_name_and_expansion(row["zone_id"], path)
    if zone_name is None:
        return
    from backend.eq2db import raids as _raids_db

    # init_db is idempotent and self-heals a fresh raids.db (CI/test env).
    with _raids_db.init_db() as rconn:
        _raids_db.rename_raid_encounter_if_exists(
            rconn,
            zone_name=zone_name,
            old_mob_name=old_name,
            new_mob_name=new_name,
        )
        rconn.commit()


def add_mob(
    encounter_id: int,
    *,
    mob_name: str,
    make_primary: bool = False,
    path: Path = DB_PATH,
) -> dict:
    """Add a mob to an encounter. By default appends as a sibling at the
    next available position. With make_primary=True, shifts every existing
    mob down by 1 and inserts the new mob at position 0, then updates the
    parent encounter_name to the new primary (mirrored to raids_db)."""
    old_primary_name: str | None = None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        with conn:
            if make_primary:
                # Capture the current primary's name BEFORE the shift, so
                # we know what to rename in raids_db.
                primary = conn.execute(
                    "SELECT mob_name FROM zone_encounter_mobs WHERE encounter_id = ? AND position = 0",
                    (encounter_id,),
                ).fetchone()
                if primary is not None:
                    old_primary_name = primary["mob_name"]
                # Two-phase shift of existing mobs down by 1 (negative
                # sentinels avoid the would-be UNIQUE collision if we ever
                # add one on (encounter_id, position); harmless either way).
                conn.execute(
                    "UPDATE zone_encounter_mobs SET position = -position - 1 WHERE encounter_id = ?",
                    (encounter_id,),
                )
                conn.execute(
                    "UPDATE zone_encounter_mobs SET position = -position WHERE encounter_id = ?",
                    (encounter_id,),
                )
                cur = conn.execute(
                    "INSERT INTO zone_encounter_mobs "
                    "(encounter_id, mob_name, mob_name_lower, position) "
                    "VALUES (?, ?, ?, 0)",
                    (encounter_id, mob_name, mob_name.lower()),
                )
                new_id = cur.lastrowid
                conn.execute(
                    "UPDATE zone_encounters SET encounter_name = ? WHERE id = ?",
                    (mob_name, encounter_id),
                )
            else:
                next_pos = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM zone_encounter_mobs WHERE encounter_id = ?",
                    (encounter_id,),
                ).fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO zone_encounter_mobs "
                    "(encounter_id, mob_name, mob_name_lower, position) "
                    "VALUES (?, ?, ?, ?)",
                    (encounter_id, mob_name, mob_name.lower(), next_pos),
                )
                new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, mob_name, position FROM zone_encounter_mobs WHERE id = ?",
            (new_id,),
        ).fetchone()
        result = {
            "id": row["id"],
            "mob_name": row["mob_name"],
            "position": row["position"],
        }
    if make_primary and old_primary_name is not None:
        _mirror_primary_rename(encounter_id, old_primary_name, mob_name, path)
    return result


def update_mob(mob_id: int, *, mob_name: str, path: Path = DB_PATH) -> dict:
    """Rename a mob. If it's at position 0 (the primary), also updates the
    parent encounter_name so the two stay in sync, and mirrors the rename
    onto raids_db.raid_encounters (if a row exists there)."""
    encounter_id_for_mirror: int | None = None
    old_name: str | None = None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            "SELECT encounter_id, mob_name, position FROM zone_encounter_mobs WHERE id = ?",
            (mob_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"zone_encounter_mob {mob_id} not found")
        if row["position"] == 0:
            encounter_id_for_mirror = row["encounter_id"]
            old_name = row["mob_name"]
        with conn:
            conn.execute(
                "UPDATE zone_encounter_mobs SET mob_name = ?, mob_name_lower = ? WHERE id = ?",
                (mob_name, mob_name.lower(), mob_id),
            )
            if row["position"] == 0:
                conn.execute(
                    "UPDATE zone_encounters SET encounter_name = ? WHERE id = ?",
                    (mob_name, row["encounter_id"]),
                )
        out = conn.execute(
            "SELECT id, mob_name, position FROM zone_encounter_mobs WHERE id = ?",
            (mob_id,),
        ).fetchone()
        result = {
            "id": out["id"],
            "mob_name": out["mob_name"],
            "position": out["position"],
        }
    if encounter_id_for_mirror is not None and old_name is not None:
        _mirror_primary_rename(encounter_id_for_mirror, old_name, mob_name, path)
    return result


def promote_mob(mob_id: int, path: Path = DB_PATH) -> dict:
    """Swap a sibling with the current primary (position 0). No-op if the
    mob is already primary. Updates the parent encounter_name to the new
    primary and mirrors the rename onto raids_db."""
    encounter_id_for_mirror: int | None = None
    old_name: str | None = None
    new_name: str | None = None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            "SELECT id, encounter_id, mob_name, position FROM zone_encounter_mobs WHERE id = ?",
            (mob_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"zone_encounter_mob {mob_id} not found")
        if row["position"] == 0:
            return {"id": row["id"], "mob_name": row["mob_name"], "position": 0}
        primary = conn.execute(
            "SELECT id, mob_name FROM zone_encounter_mobs WHERE encounter_id = ? AND position = 0",
            (row["encounter_id"],),
        ).fetchone()
        if primary is None:
            # Shouldn't happen if invariants hold, but defensively: just move
            # this mob to position 0 with no swap.
            with conn:
                conn.execute(
                    "UPDATE zone_encounter_mobs SET position = 0 WHERE id = ?",
                    (mob_id,),
                )
                conn.execute(
                    "UPDATE zone_encounters SET encounter_name = ? WHERE id = ?",
                    (row["mob_name"], row["encounter_id"]),
                )
            return {"id": row["id"], "mob_name": row["mob_name"], "position": 0}
        encounter_id_for_mirror = row["encounter_id"]
        old_name = primary["mob_name"]
        new_name = row["mob_name"]
        with conn:
            # Park the old primary at -1 (sentinel), promote the sibling
            # to 0, then move the old primary into the sibling's old slot.
            conn.execute(
                "UPDATE zone_encounter_mobs SET position = -1 WHERE id = ?",
                (primary["id"],),
            )
            conn.execute(
                "UPDATE zone_encounter_mobs SET position = 0 WHERE id = ?",
                (mob_id,),
            )
            conn.execute(
                "UPDATE zone_encounter_mobs SET position = ? WHERE id = ?",
                (row["position"], primary["id"]),
            )
            conn.execute(
                "UPDATE zone_encounters SET encounter_name = ? WHERE id = ?",
                (row["mob_name"], row["encounter_id"]),
            )
        out = conn.execute(
            "SELECT id, mob_name, position FROM zone_encounter_mobs WHERE id = ?",
            (mob_id,),
        ).fetchone()
        result = {
            "id": out["id"],
            "mob_name": out["mob_name"],
            "position": out["position"],
        }
    if encounter_id_for_mirror is not None and old_name is not None and new_name is not None:
        _mirror_primary_rename(encounter_id_for_mirror, old_name, new_name, path)
    return result


def delete_mob(mob_id: int, path: Path = DB_PATH) -> bool:
    """Delete a mob. Refuses with ValueError when it's the only mob in the
    encounter (an encounter needs >= 1 mob) or when it's the primary while
    siblings exist (the user must promote a sibling first so encounter_name
    has somewhere to point). Returns False if the mob_id is not found, True
    on successful delete."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            "SELECT id, encounter_id, position FROM zone_encounter_mobs WHERE id = ?",
            (mob_id,),
        ).fetchone()
        if row is None:
            return False
        total = conn.execute(
            "SELECT COUNT(*) FROM zone_encounter_mobs WHERE encounter_id = ?",
            (row["encounter_id"],),
        ).fetchone()[0]
        if total <= 1:
            raise ValueError("cannot delete the last mob of an encounter")
        if row["position"] == 0:
            raise ValueError("cannot delete the primary mob while siblings exist; promote a sibling to primary first")
        conn.execute("DELETE FROM zone_encounter_mobs WHERE id = ?", (mob_id,))
        conn.commit()
        return True


def delete_encounter(encounter_id: int, path: Path = DB_PATH) -> bool:
    """Delete an encounter. Cascades zone_encounter_mobs via FK; cascades the
    matching raids_db row (if any) which itself cascades triggers/timers/
    strategies via their FK. Returns True if a zone_encounter row was deleted."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            "SELECT id, zone_id, encounter_name FROM zone_encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
        if row is None:
            return False
        zone_name, _exp = _zone_name_and_expansion(row["zone_id"], path)
        conn.execute("DELETE FROM zone_encounters WHERE id = ?", (encounter_id,))
        conn.commit()
    if zone_name is not None:
        from backend.eq2db import raids as _raids_db

        # init_db is idempotent and self-heals a fresh raids.db (CI/test env).
        with _raids_db.init_db() as rconn:
            _raids_db.delete_raid_encounter_by_zone_mob(rconn, zone_name=zone_name, mob_name=row["encounter_name"])
            rconn.commit()
    return True


# ---------------------------------------------------------------------------
# Zone-type tag helpers (used by the dungeon-curation UI on /raids)
# ---------------------------------------------------------------------------


def add_zone_type(zone_name: str, type_token: str, path: Path = DB_PATH) -> dict | None:
    """Add a type tag (e.g. 'dungeon') to a zone. Idempotent — adding the
    same tag twice is a no-op (INSERT OR IGNORE against the PK).

    Returns the hydrated zone dict (same shape as find_by_name) after the
    mutation, or None if the zone_name doesn't resolve. Route layer is
    responsible for turning None into a 404."""
    if not path.exists() or not zone_name:
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM zones WHERE name_lower = ? LIMIT 1",
            (zone_name.lower(),),
        ).fetchone()
        if row is None:
            alias_row = conn.execute(
                "SELECT zone_id FROM zone_aliases WHERE alias_lower = ? LIMIT 1",
                (zone_name.lower(),),
            ).fetchone()
            if alias_row is None:
                return None
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM zones WHERE id = ?",
                (alias_row[0],),
            ).fetchone()
            if row is None:
                return None
        conn.execute(
            "INSERT OR IGNORE INTO zone_types (zone_id, type) VALUES (?, ?)",
            (row["id"], type_token),
        )
        conn.commit()
        return _hydrate_zone(conn, row)


# ---------------------------------------------------------------------------
# Featured raid expansions (admin-curated /raids page list)
# ---------------------------------------------------------------------------


def list_featured_raid_expansions(path: Path = DB_PATH) -> list[dict]:
    """Return admin-featured expansions for /raids, plus any expansion that
    has at least one featured raid zone (implicit). Sorted by expansion_year
    DESC. Each entry: ``{"short", "name", "year"}``."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            WITH all_shorts AS (
                SELECT expansion_short AS short FROM featured_raid_expansions
                UNION
                SELECT DISTINCT z.expansion_short AS short
                FROM featured_raid_zones f
                JOIN zones z ON z.id = f.zone_id
                WHERE z.expansion_short IS NOT NULL
            )
            SELECT DISTINCT z.expansion_short AS short,
                            z.expansion_name  AS name,
                            z.expansion_year  AS year
            FROM zones z
            JOIN all_shorts s ON s.short = z.expansion_short
            WHERE z.expansion_short IS NOT NULL
            ORDER BY z.expansion_year DESC, z.expansion_short
            """
        ).fetchall()
        # SELECT DISTINCT on (short, name, year) can return duplicates if the
        # same expansion has rows with different name/year combos. Collapse
        # by short, keeping the first hit (newest year first thanks to ORDER BY).
        seen: set[str] = set()
        result: list[dict] = []
        for r in rows:
            short = r["short"]
            if short in seen:
                continue
            seen.add(short)
            result.append({"short": short, "name": r["name"], "year": r["year"]})
        return result


def list_available_raid_expansions(path: Path = DB_PATH) -> list[dict]:
    """Return expansions in zones.db NOT yet featured. For the admin
    'Add expansion' picker. Sorted by expansion_year DESC. An expansion
    counts as 'already featured' if either it has a featured_raid_expansions
    row OR any zone of its appears in featured_raid_zones."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT DISTINCT z.expansion_short AS short,
                            z.expansion_name  AS name,
                            z.expansion_year  AS year
            FROM zones z
            WHERE z.expansion_short IS NOT NULL
              AND z.expansion_short NOT IN (SELECT expansion_short FROM featured_raid_expansions)
              AND z.expansion_short NOT IN (
                  SELECT DISTINCT z2.expansion_short
                  FROM featured_raid_zones f
                  JOIN zones z2 ON z2.id = f.zone_id
                  WHERE z2.expansion_short IS NOT NULL
              )
            ORDER BY z.expansion_year DESC, z.expansion_short
            """
        ).fetchall()
        seen: set[str] = set()
        result: list[dict] = []
        for r in rows:
            short = r["short"]
            if short in seen:
                continue
            seen.add(short)
            result.append({"short": short, "name": r["name"], "year": r["year"]})
        return result


def add_featured_raid_expansion(expansion_short: str, path: Path = DB_PATH) -> bool:
    """Mark an expansion as featured. Returns True if newly added, False if
    already featured OR if the expansion is unknown to zones.db. The route
    layer treats False as a 404."""
    if not path.exists() or not expansion_short:
        return False
    with sqlite3.connect(path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM zones WHERE expansion_short = ? LIMIT 1",
            (expansion_short,),
        ).fetchone()
        if not exists:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO featured_raid_expansions (expansion_short) VALUES (?)",
            (expansion_short,),
        )
        conn.commit()
        # We return True even if the row was already present — that case is
        # an idempotent success ("already featured"), not the 404 case which
        # is reserved for "expansion doesn't exist in zones.db at all".
        return True


def remove_featured_raid_expansion(expansion_short: str, path: Path = DB_PATH) -> bool:
    """Remove an expansion from featured AND cascade-remove its featured
    raid zones (preserves the underlying zone_encounters boss data — just
    hides it from /raids until re-added).

    Returns True if the featured_raid_expansions row was removed, False if
    nothing to remove. Note that cascaded featured_raid_zones deletions
    don't influence the return value."""
    if not path.exists():
        return False
    with sqlite3.connect(path) as conn:
        with conn:
            # Cascade-remove featured zones in this expansion first.
            conn.execute(
                """
                DELETE FROM featured_raid_zones
                 WHERE zone_id IN (
                     SELECT id FROM zones WHERE expansion_short = ?
                 )
                """,
                (expansion_short,),
            )
            cur = conn.execute(
                "DELETE FROM featured_raid_expansions WHERE expansion_short = ?",
                (expansion_short,),
            )
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Featured raid zones (per-expansion admin curation)
# ---------------------------------------------------------------------------


def list_featured_raid_zones(expansion_short: str, path: Path = DB_PATH) -> list[dict]:
    """Return admin-featured raid zones for an expansion, hydrated like
    find_by_name (types/aliases/bosses) and additionally annotated with
    `position` and `category`. Sorted by (category, position) — NULL
    categories sort first per SQLite's default NULL ordering, so the
    implicit "Uncategorised" lane appears before any named lane."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS},
                   f.position AS featured_position,
                   f.category AS featured_category
            FROM zones z
            JOIN featured_raid_zones f ON f.zone_id = z.id
            WHERE z.expansion_short = ?
            ORDER BY f.category, f.position
            """,
            (expansion_short,),
        ).fetchall()
        result: list[dict] = []
        for r in rows:
            z = _hydrate_zone(conn, r)
            z["position"] = r["featured_position"]
            z["category"] = r["featured_category"]
            result.append(z)
        return result


def list_available_raid_zones(expansion_short: str, path: Path = DB_PATH) -> list[dict]:
    """Return zones in an expansion that are tagged raid_x4 OR raid_x2 but
    NOT yet featured. For the admin 'Add raid zone' picker. Sorted
    alphabetically by zone name."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT DISTINCT {_SELECT_COLS}
            FROM zones z
            JOIN zone_types t ON t.zone_id = z.id
            WHERE z.expansion_short = ?
              AND t.type IN ('raid_x4', 'raid_x2')
              AND z.id NOT IN (SELECT zone_id FROM featured_raid_zones)
            ORDER BY z.name
            """,
            (expansion_short,),
        ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


def add_featured_raid_zone(zone_name: str, path: Path = DB_PATH) -> dict | None:
    """Mark a raid zone as featured. Validates that the zone exists AND is
    tagged raid_x4 or raid_x2 (we don't want any old zone surfacing on the
    /raids page just because admin typed its name). Newly-added zones land
    in the implicit "Uncategorised" lane (category=NULL) at MAX(position)+1
    so they appear at the end of that lane. Admin can drag them into a
    named lane afterwards. Returns the hydrated zone dict on success, None
    if the zone doesn't exist or isn't raid-typed (route layer turns None
    into a 400)."""
    if not path.exists() or not zone_name:
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        zone = conn.execute(
            f"SELECT {_SELECT_COLS} FROM zones WHERE name_lower = ? LIMIT 1",
            (zone_name.lower(),),
        ).fetchone()
        if zone is None:
            return None
        is_raid = conn.execute(
            "SELECT 1 FROM zone_types WHERE zone_id = ? AND type IN ('raid_x4', 'raid_x2') LIMIT 1",
            (zone["id"],),
        ).fetchone()
        if not is_raid:
            return None
        # Position = MAX position currently in this expansion's NULL-category
        # lane + 1, so newly-added zones land at the bottom of Uncategorised.
        max_pos_row = conn.execute(
            """
            SELECT COALESCE(MAX(f.position), -1)
            FROM featured_raid_zones f
            JOIN zones z2 ON z2.id = f.zone_id
            WHERE z2.expansion_short = ? AND f.category IS NULL
            """,
            (zone["expansion_short"],),
        ).fetchone()
        new_position = (max_pos_row[0] if max_pos_row else -1) + 1
        conn.execute(
            "INSERT OR IGNORE INTO featured_raid_zones (zone_id, position, category) VALUES (?, ?, NULL)",
            (zone["id"], new_position),
        )
        conn.commit()
        return _hydrate_zone(conn, zone)


def remove_featured_raid_zone(zone_name: str, path: Path = DB_PATH) -> bool:
    """Remove a raid zone from featured. Preserves zone_encounters boss
    data (re-adding the zone restores everything). Returns True if a row
    was removed."""
    if not path.exists() or not zone_name:
        return False
    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            """
            DELETE FROM featured_raid_zones
             WHERE zone_id = (SELECT id FROM zones WHERE name_lower = ? LIMIT 1)
            """,
            (zone_name.lower(),),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Drag-reorder helpers for featured raid zones + categories
# ---------------------------------------------------------------------------


def reorder_featured_raid_zones(
    expansion_short: str,
    ordering: list[dict],
    path: Path = DB_PATH,
) -> bool:
    """Atomically rewrite category + position for every zone in `ordering`.

    Each entry is ``{"name": str, "category": str | None, "position": int}``.

    Uses the two-phase shift pattern (write all to temporary negative
    positions first, then to their final values) so any UNIQUE or
    ordering constraints can't transient-collide.

    Auto-creates missing featured_raid_categories rows (at MAX+1 position)
    for any category name that appears in `ordering` but isn't already
    tracked for this expansion — this is how a fresh-typed lane name
    becomes a draggable lane header on the next render.

    Returns True on success, False if any zone in `ordering` isn't in
    featured_raid_zones for this expansion.
    """
    if not path.exists():
        return False
    with sqlite3.connect(path) as conn:
        with conn:
            # Validate every zone in ordering is currently featured in this
            # expansion. Surfaces typos / stale clients as a 400 at the route.
            zone_ids: dict[str, int] = {}
            for entry in ordering:
                row = conn.execute(
                    """
                    SELECT z.id FROM zones z
                    JOIN featured_raid_zones f ON f.zone_id = z.id
                    WHERE z.name_lower = ? AND z.expansion_short = ?
                    """,
                    (entry["name"].lower(), expansion_short),
                ).fetchone()
                if not row:
                    return False
                zone_ids[entry["name"]] = row[0]

            # Auto-create missing categories at end of existing positions.
            seen_categories = {e["category"] for e in ordering if e.get("category")}
            if seen_categories:
                max_pos_row = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) FROM featured_raid_categories WHERE expansion_short = ?",
                    (expansion_short,),
                ).fetchone()
                next_pos = (max_pos_row[0] if max_pos_row else -1) + 1
                for cat in seen_categories:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO featured_raid_categories "
                        "(expansion_short, name, position) VALUES (?, ?, ?)",
                        (expansion_short, cat, next_pos),
                    )
                    if cur.rowcount:
                        next_pos += 1

            # Two-phase position write: temp negatives first to dodge any
            # ordering/UNIQUE collision risk, then the final values. The
            # category column is freely overwritten in both phases.
            for i, entry in enumerate(ordering):
                conn.execute(
                    "UPDATE featured_raid_zones SET position = ?, category = ? WHERE zone_id = ?",
                    (-(i + 1), entry.get("category"), zone_ids[entry["name"]]),
                )
            for entry in ordering:
                conn.execute(
                    "UPDATE featured_raid_zones SET position = ? WHERE zone_id = ?",
                    (entry["position"], zone_ids[entry["name"]]),
                )
            return True


def reorder_featured_raid_categories(
    expansion_short: str,
    ordering: list[dict],
    path: Path = DB_PATH,
) -> bool:
    """Atomic two-phase position rewrite for category lanes in an expansion.

    Each entry is ``{"name": str, "position": int}``. Returns True on
    success, False if any category in `ordering` doesn't exist for this
    expansion (route layer turns False into a 400)."""
    if not path.exists():
        return False
    with sqlite3.connect(path) as conn:
        with conn:
            # Validate all categories exist for this expansion.
            for entry in ordering:
                row = conn.execute(
                    "SELECT 1 FROM featured_raid_categories WHERE expansion_short = ? AND name = ?",
                    (expansion_short, entry["name"]),
                ).fetchone()
                if not row:
                    return False
            # Two-phase write: temp negatives then final positions.
            for i, entry in enumerate(ordering):
                conn.execute(
                    "UPDATE featured_raid_categories SET position = ? WHERE expansion_short = ? AND name = ?",
                    (-(i + 1), expansion_short, entry["name"]),
                )
            for entry in ordering:
                conn.execute(
                    "UPDATE featured_raid_categories SET position = ? WHERE expansion_short = ? AND name = ?",
                    (entry["position"], expansion_short, entry["name"]),
                )
            return True


def list_featured_raid_categories(expansion_short: str, path: Path = DB_PATH) -> list[dict]:
    """Return ordered list of admin-defined categories for an expansion.

    Used by the frontend to render lane headers in their saved order.
    The implicit "Uncategorised" lane (category IS NULL on zones) is NOT
    in this list — the frontend always pins it at the top.
    """
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, position FROM featured_raid_categories WHERE expansion_short = ? ORDER BY position",
            (expansion_short,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_featured_raid_category(expansion_short: str, name: str, path: Path = DB_PATH) -> bool:
    """Create an empty category lane at MAX+1 position. Returns True if
    newly created, False if already exists."""
    if not path.exists():
        return False
    with sqlite3.connect(path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM featured_raid_categories WHERE expansion_short = ? AND name = ?",
            (expansion_short, name),
        ).fetchone()
        if existing:
            return False
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM featured_raid_categories WHERE expansion_short = ?",
            (expansion_short,),
        ).fetchone()
        new_pos = (max_pos[0] if max_pos else -1) + 1
        conn.execute(
            "INSERT INTO featured_raid_categories (expansion_short, name, position) VALUES (?, ?, ?)",
            (expansion_short, name, new_pos),
        )
        conn.commit()
        return True


def delete_featured_raid_category(expansion_short: str, name: str, path: Path = DB_PATH) -> bool:
    """Delete a category. Zones in this category have their category set
    to NULL (move to Uncategorised). Returns True if a category was
    deleted, False if it didn't exist."""
    if not path.exists():
        return False
    with sqlite3.connect(path) as conn:
        with conn:
            # Move zones in this category to NULL.
            conn.execute(
                """
                UPDATE featured_raid_zones SET category = NULL
                WHERE category = ?
                  AND zone_id IN (SELECT id FROM zones WHERE expansion_short = ?)
                """,
                (name, expansion_short),
            )
            cur = conn.execute(
                "DELETE FROM featured_raid_categories WHERE expansion_short = ? AND name = ?",
                (expansion_short, name),
            )
            return cur.rowcount > 0


def remove_zone_type(zone_name: str, type_token: str, path: Path = DB_PATH) -> dict | None:
    """Remove a type tag from a zone. Idempotent — a no-op when the tag
    isn't present. Returns the hydrated zone dict after the mutation, or
    None if the zone_name doesn't resolve (route layer maps to 404)."""
    if not path.exists() or not zone_name:
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM zones WHERE name_lower = ? LIMIT 1",
            (zone_name.lower(),),
        ).fetchone()
        if row is None:
            alias_row = conn.execute(
                "SELECT zone_id FROM zone_aliases WHERE alias_lower = ? LIMIT 1",
                (zone_name.lower(),),
            ).fetchone()
            if alias_row is None:
                return None
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM zones WHERE id = ?",
                (alias_row[0],),
            ).fetchone()
            if row is None:
                return None
        conn.execute(
            "DELETE FROM zone_types WHERE zone_id = ? AND type = ?",
            (row["id"], type_token),
        )
        conn.commit()
        return _hydrate_zone(conn, row)
