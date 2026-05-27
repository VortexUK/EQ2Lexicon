"""
Local SQLite catalogue of EverQuest 2 raid strategies.

Companion to ``census/zones_db.py`` — zones.db is read-only reference
data rebuilt from JSON; this DB accumulates strategy content sourced
initially from the EQ2 wiki (EQ2i / Fandom) and then progressively
hand-edited by guild officers.

Scope (deliberate): Vanilla through Rise of Kunark only. Picked to
align with the TLE-server content cycle. Live-expansion strategies are
out of scope for the moment.

Schema (four tables):

  * **raid_zones**            — one row per raid zone with zone-level
                                metadata (access, level range, etc.).
                                Loose FK by ``zone_name`` to zones.db
                                (different DB file — no enforced FK).
  * **raid_encounters**       — one row per named boss within a raid
                                zone. ``strategy_md`` is a single
                                markdown blob (PoC simplicity; can
                                split into structured fields later if
                                a pattern emerges).
  * **raid_encounter_revisions** — version history. Every UPDATE to
                                   raid_encounters.strategy_md writes
                                   a row here with before/after +
                                   editor identity + timestamp.
  * **_meta**                  — provenance: built_at, scraper_source,
                                 source_count, etc.

The `source` column on raid_zones / raid_encounters tracks where the
content came from:
  * 'eq2i_scrape' — auto-extracted from the wiki, untouched
  * 'manual'      — added or edited by a human via the future editor
  * 'parse_data'  — derived from encounter parses (e.g. mechanic timing
                    confirmed from log analysis; future feature)

A row can transition: 'eq2i_scrape' → 'manual' on first hand-edit.
The revision history preserves the original scrape for audit.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    env = os.getenv("DB_RAIDS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "raids" / "raids.db"


DB_PATH: Path = _db_path()


# Source provenance tokens for the `source` columns. Centralised so
# typos don't silently produce a third category.
SOURCE_SCRAPE = "eq2i_scrape"
SOURCE_MANUAL = "manual"
SOURCE_PARSE = "parse_data"

VALID_SOURCES: frozenset[str] = frozenset({SOURCE_SCRAPE, SOURCE_MANUAL, SOURCE_PARSE})


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_META = """
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_CREATE_RAID_ZONES = """
CREATE TABLE IF NOT EXISTS raid_zones (
    -- Identity
    id              INTEGER PRIMARY KEY,
    zone_name       TEXT    NOT NULL UNIQUE,   -- matches zones.db zones.name
    zone_name_lower TEXT    NOT NULL,

    -- Denormalised from zones.db (intentional duplication so this DB
    -- is queryable standalone; if the canonical changes, re-run the
    -- scraper / sync job to refresh).
    expansion_short TEXT    NOT NULL,          -- 'Vanilla' / 'DoF' / 'KoS' / 'EoF' / 'RoK'
    wiki_url        TEXT,

    -- Zone-level metadata extracted from the IZoneInformation template
    -- on the wiki. All optional — missing fields just stay NULL.
    access_md       TEXT,                      -- how to get into the zone
    background_md   TEXT,                      -- lore / "Background" wiki section
    overview_md     TEXT,                      -- general zone-level tactics
    level_range     TEXT,                      -- e.g. '72-75'
    zdiff           TEXT,                      -- 'x4' / 'x2' / 'x3'
    lockout_min     TEXT,                      -- e.g. '2 days 20 hours'
    lockout_max     TEXT,                      -- e.g. '7 days'

    -- Audit trail
    source          TEXT    NOT NULL,          -- SOURCE_SCRAPE / SOURCE_MANUAL
    last_synced_at  INTEGER,                   -- unix ts of last wiki re-scrape
    last_edited_at  INTEGER,
    last_edited_by  TEXT                       -- discord_id or 'eq2i_scrape'
);
"""

_CREATE_RAID_ENCOUNTERS = """
CREATE TABLE IF NOT EXISTS raid_encounters (
    id              INTEGER PRIMARY KEY,
    raid_zone_id    INTEGER NOT NULL REFERENCES raid_zones(id) ON DELETE CASCADE,
    mob_name        TEXT    NOT NULL,
    mob_name_lower  TEXT    NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,   -- order within the zone

    -- Free-form markdown strategy. Single blob deliberately — PoC
    -- simplicity. If a structured pattern emerges (cures, dispels,
    -- phases) we can split later without breaking callers.
    strategy_md     TEXT,

    wiki_url        TEXT,
    source          TEXT    NOT NULL,
    last_synced_at  INTEGER,
    last_edited_at  INTEGER,
    last_edited_by  TEXT,

    UNIQUE (raid_zone_id, mob_name_lower)
);
"""

_CREATE_REVISIONS = """
CREATE TABLE IF NOT EXISTS raid_encounter_revisions (
    id            INTEGER PRIMARY KEY,
    encounter_id  INTEGER NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,
    edited_at     INTEGER NOT NULL,
    edited_by     TEXT    NOT NULL,              -- discord_id or scrape token
    before_md     TEXT,                          -- previous strategy_md (NULL on create)
    after_md      TEXT NOT NULL,                 -- new strategy_md
    edit_note     TEXT                           -- optional commit-message style note
);
"""

# ACT Triggers — regex-driven matchers a player imports into Advanced Combat
# Tracker to react to in-game log lines (boss callouts, debuffs, mechanic
# triggers). One row maps 1:1 to a <Trigger> element in ACT's
# `spell_timers.xml` export format (column names mirror XML attributes via
# snake_case).
#
# A trigger with `timer=1` references an entry in `act_spell_timers` by
# `timer_name`; on XML export both rows are emitted so the dropped file
# round-trips in ACT without manual fix-up.
_CREATE_ACT_TRIGGERS = """
CREATE TABLE IF NOT EXISTS act_triggers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_encounter_id   INTEGER NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,

    -- Display / curation (web-only — no XML counterpart)
    position            INTEGER NOT NULL DEFAULT 0,    -- ordering within encounter
    label               TEXT,                          -- human-readable summary line; falls back to sound_data/regex preview
    notes               TEXT,                          -- contributor explanation, never exported

    -- ACT <Trigger> attributes (9 fields)
    active              INTEGER NOT NULL DEFAULT 1,
    regex               TEXT    NOT NULL,
    sound_data          TEXT    NOT NULL DEFAULT '',
    sound_type          INTEGER NOT NULL DEFAULT 3,    -- 3 = TTS, 0 = silent / file
    category_restrict   INTEGER NOT NULL DEFAULT 0,
    category            TEXT,                          -- defaults to mob_name at write time
    timer               INTEGER NOT NULL DEFAULT 0,
    timer_name          TEXT,                          -- loose name-FK into act_spell_timers (same encounter)
    tabbed              INTEGER NOT NULL DEFAULT 0,

    -- Audit
    last_edited_at      INTEGER,
    last_edited_by      TEXT,
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
"""

# ACT Spell Timers — named timer definitions referenced by `act_triggers`
# via `timer_name`. One row maps 1:1 to a <Spell> element in ACT's
# spell_timers.xml. Multiple triggers MAY reference the same timer name
# within an encounter (DRY); export deduplicates by name.
_CREATE_ACT_SPELL_TIMERS = """
CREATE TABLE IF NOT EXISTS act_spell_timers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_encounter_id    INTEGER NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,

    -- Identity (Name is what triggers reference via TimerName)
    name                 TEXT NOT NULL,
    name_lower           TEXT NOT NULL,

    -- ACT <Spell> attributes (17 fields)
    checked              INTEGER NOT NULL DEFAULT 0,
    timer_duration_s     INTEGER NOT NULL,             -- "Timer" attribute in XML
    only_master_ticks    INTEGER NOT NULL DEFAULT 0,
    restrict             INTEGER NOT NULL DEFAULT 0,
    absolute_            INTEGER NOT NULL DEFAULT 0,   -- "Absolute" — column name disambiguated from SQL keyword
    start_wav            TEXT    NOT NULL DEFAULT '',
    warning_wav          TEXT    NOT NULL DEFAULT '',
    warning_value        INTEGER NOT NULL DEFAULT 10,
    radial_display       INTEGER NOT NULL DEFAULT 0,
    modable              INTEGER NOT NULL DEFAULT 0,
    tooltip              TEXT    NOT NULL DEFAULT '',
    fill_color           INTEGER NOT NULL DEFAULT -16776961,  -- ACT default blue (.NET ARGB packed int)
    panel1               INTEGER NOT NULL DEFAULT 1,
    panel2               INTEGER NOT NULL DEFAULT 0,
    remove_value         INTEGER NOT NULL DEFAULT -15,
    category             TEXT,                          -- defaults to mob_name at write time
    restrict_category    INTEGER NOT NULL DEFAULT 0,

    -- Audit
    last_edited_at       INTEGER,
    last_edited_by       TEXT,
    created_at           INTEGER NOT NULL DEFAULT (strftime('%s','now')),

    UNIQUE (raid_encounter_id, name_lower)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_raid_zones_name_lower  ON raid_zones (zone_name_lower);",
    "CREATE INDEX IF NOT EXISTS idx_raid_zones_expansion   ON raid_zones (expansion_short);",
    "CREATE INDEX IF NOT EXISTS idx_raid_enc_zone          ON raid_encounters (raid_zone_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_raid_enc_mob_lower     ON raid_encounters (mob_name_lower);",
    "CREATE INDEX IF NOT EXISTS idx_raid_rev_encounter     ON raid_encounter_revisions (encounter_id, edited_at);",
    "CREATE INDEX IF NOT EXISTS idx_act_triggers_enc       ON act_triggers (raid_encounter_id, position);",
    "CREATE INDEX IF NOT EXISTS idx_act_triggers_timer     ON act_triggers (raid_encounter_id, timer_name);",
    "CREATE INDEX IF NOT EXISTS idx_act_spell_timers_enc   ON act_spell_timers (raid_encounter_id);",
]


# ---------------------------------------------------------------------------
# DB management
# ---------------------------------------------------------------------------


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables/indexes if missing. Returns an open connection
    with FKs enabled (so the ON DELETE CASCADE on revisions/encounters
    actually fires)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous  = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(_CREATE_META)
    conn.execute(_CREATE_RAID_ZONES)
    conn.execute(_CREATE_RAID_ENCOUNTERS)
    conn.execute(_CREATE_REVISIONS)
    conn.execute(_CREATE_ACT_TRIGGERS)
    conn.execute(_CREATE_ACT_SPELL_TIMERS)
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


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def upsert_raid_zone(
    conn: sqlite3.Connection,
    *,
    zone_name: str,
    expansion_short: str,
    wiki_url: str | None = None,
    access_md: str | None = None,
    background_md: str | None = None,
    overview_md: str | None = None,
    level_range: str | None = None,
    zdiff: str | None = None,
    lockout_min: str | None = None,
    lockout_max: str | None = None,
    source: str = SOURCE_SCRAPE,
) -> int:
    """Insert or update a raid_zones row. Returns its id.

    Re-runnable. The behaviour depends on the existing row's ``source``:

      * **New row** — inserts as given.
      * **Existing row, called with SOURCE_SCRAPE** — refreshes the wiki-
        owned columns (``expansion_short``, ``wiki_url``, level_range,
        ``zdiff``, ``lockout_*``, ``last_synced_at``) but **never clobbers
        a human-edited markdown blob**. When the existing source is
        ``SOURCE_MANUAL``, the markdown columns (access/background/overview)
        are left as-is. When the existing source is ``SOURCE_SCRAPE``, the
        markdown is refreshed with the latest scrape (so wiki edits
        propagate).
      * **Existing row, called with SOURCE_MANUAL** — this helper isn't the
        canonical write path for manual edits (the route layer uses targeted
        UPDATEs that only touch the field the user edited — see
        ``_write_overview_sync`` in web/routes/raid_strategies.py). Calling
        this helper with SOURCE_MANUAL upserts every field passed and stamps
        ``source='manual'`` — useful from migration scripts, not user-facing.

    Doesn't touch ``last_edited_at`` — that's reserved for the route layer's
    targeted UPDATEs.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}, got {source!r}")
    now = int(time.time())
    last_synced = now if source == SOURCE_SCRAPE else None

    existing = conn.execute("SELECT id, source FROM raid_zones WHERE zone_name = ?", (zone_name,)).fetchone()

    if existing and source == SOURCE_SCRAPE and existing[1] == SOURCE_MANUAL:
        # Re-scrape against a human-edited row: refresh the wiki-owned
        # metadata but leave the markdown blobs + source flag alone. The
        # revision history (encounters only) doesn't apply at the zone
        # level for now; future raid_zone_revisions table is the right
        # home for tracking these.
        conn.execute(
            """
            UPDATE raid_zones SET
                expansion_short = ?,
                wiki_url        = ?,
                level_range     = ?,
                zdiff           = ?,
                lockout_min     = ?,
                lockout_max     = ?,
                last_synced_at  = ?
            WHERE id = ?
            """,
            (
                expansion_short,
                wiki_url,
                level_range,
                zdiff,
                lockout_min,
                lockout_max,
                now,
                existing[0],
            ),
        )
        conn.commit()
        return int(existing[0])

    conn.execute(
        """
        INSERT INTO raid_zones (
            zone_name, zone_name_lower,
            expansion_short, wiki_url,
            access_md, background_md, overview_md,
            level_range, zdiff, lockout_min, lockout_max,
            source, last_synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(zone_name) DO UPDATE SET
            expansion_short = excluded.expansion_short,
            wiki_url        = excluded.wiki_url,
            access_md       = excluded.access_md,
            background_md   = excluded.background_md,
            overview_md     = excluded.overview_md,
            level_range     = excluded.level_range,
            zdiff           = excluded.zdiff,
            lockout_min     = excluded.lockout_min,
            lockout_max     = excluded.lockout_max,
            source          = excluded.source,
            last_synced_at  = COALESCE(excluded.last_synced_at, raid_zones.last_synced_at)
        """,
        (
            zone_name,
            zone_name.lower(),
            expansion_short,
            wiki_url,
            access_md,
            background_md,
            overview_md,
            level_range,
            zdiff,
            lockout_min,
            lockout_max,
            source,
            last_synced,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM raid_zones WHERE zone_name = ?", (zone_name,)).fetchone()
    return int(row[0])


def upsert_raid_encounter(
    conn: sqlite3.Connection,
    *,
    raid_zone_id: int,
    mob_name: str,
    position: int = 0,
    strategy_md: str | None = None,
    wiki_url: str | None = None,
    source: str = SOURCE_SCRAPE,
    edited_by: str | None = None,
    edit_note: str | None = None,
) -> int:
    """Insert or update a raid_encounters row. Returns its id.

    When the strategy_md actually changes (vs the current row), also
    appends a row to raid_encounter_revisions so the change is
    auditable. The first ever insert produces a revision with
    before_md=NULL.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}, got {source!r}")
    now = int(time.time())
    actor = edited_by or ("eq2i_scrape" if source == SOURCE_SCRAPE else "unknown")

    existing = conn.execute(
        "SELECT id, strategy_md FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
        (raid_zone_id, mob_name.lower()),
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            """
            INSERT INTO raid_encounters (
                raid_zone_id, mob_name, mob_name_lower, position,
                strategy_md, wiki_url, source,
                last_synced_at, last_edited_at, last_edited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raid_zone_id,
                mob_name,
                mob_name.lower(),
                position,
                strategy_md,
                wiki_url,
                source,
                now if source == SOURCE_SCRAPE else None,
                now if source != SOURCE_SCRAPE else None,
                actor if source != SOURCE_SCRAPE else None,
            ),
        )
        new_id = int(cur.lastrowid or 0)
        # First-ever revision row: before is NULL, after is the seeded content.
        if strategy_md is not None:
            conn.execute(
                "INSERT INTO raid_encounter_revisions "
                "(encounter_id, edited_at, edited_by, before_md, after_md, edit_note) "
                "VALUES (?, ?, ?, NULL, ?, ?)",
                (new_id, now, actor, strategy_md, edit_note or "initial scrape"),
            )
        conn.commit()
        return new_id

    enc_id, prev_md = int(existing[0]), existing[1]
    # On re-scrape: only update fields that the scraper authoritatively
    # owns (wiki_url, position, last_synced_at). Don't clobber a
    # human-edited strategy_md with a fresh scrape — that's what
    # SOURCE_MANUAL exists to protect.
    if source == SOURCE_SCRAPE:
        current_source = conn.execute("SELECT source FROM raid_encounters WHERE id = ?", (enc_id,)).fetchone()[0]
        if current_source == SOURCE_MANUAL:
            # Refresh sync timestamp + url/position only, leave strategy alone.
            conn.execute(
                "UPDATE raid_encounters SET wiki_url = ?, position = ?, last_synced_at = ? WHERE id = ?",
                (wiki_url, position, now, enc_id),
            )
            conn.commit()
            return enc_id

    # Strategy actually changing? Record a revision before the update.
    if strategy_md is not None and strategy_md != prev_md:
        conn.execute(
            "INSERT INTO raid_encounter_revisions "
            "(encounter_id, edited_at, edited_by, before_md, after_md, edit_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (enc_id, now, actor, prev_md, strategy_md, edit_note),
        )

    conn.execute(
        """
        UPDATE raid_encounters SET
            mob_name        = ?,
            position        = ?,
            strategy_md     = COALESCE(?, strategy_md),
            wiki_url        = ?,
            source          = ?,
            last_synced_at  = CASE WHEN ?=? THEN ? ELSE last_synced_at END,
            last_edited_at  = CASE WHEN ?<>? THEN ? ELSE last_edited_at END,
            last_edited_by  = CASE WHEN ?<>? THEN ? ELSE last_edited_by END
        WHERE id = ?
        """,
        (
            mob_name,
            position,
            strategy_md,
            wiki_url,
            source,
            source,
            SOURCE_SCRAPE,
            now,
            source,
            SOURCE_SCRAPE,
            now,
            source,
            SOURCE_SCRAPE,
            actor,
            enc_id,
        ),
    )
    conn.commit()
    return enc_id


# ---------------------------------------------------------------------------
# Read helpers (for the future web routes + the smoke tests)
# ---------------------------------------------------------------------------


_ZONE_SELECT_COLS = (
    "id, zone_name, zone_name_lower, expansion_short, wiki_url, "
    "access_md, background_md, overview_md, "
    "level_range, zdiff, lockout_min, lockout_max, "
    "source, last_synced_at, last_edited_at, last_edited_by"
)

_ENC_SELECT_COLS = (
    "id, raid_zone_id, mob_name, mob_name_lower, position, "
    "strategy_md, wiki_url, source, last_synced_at, last_edited_at, last_edited_by"
)


def find_zone_by_name(name: str, path: Path = DB_PATH) -> dict | None:
    """Look up a raid zone by name (case-insensitive)."""
    if not path.exists() or not name:
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_ZONE_SELECT_COLS} FROM raid_zones WHERE zone_name_lower = ?",
            (name.lower(),),
        ).fetchone()
        return dict(row) if row else None


def list_encounters_for_zone(zone_id: int, path: Path = DB_PATH) -> list[dict]:
    """All encounters in a raid zone, ordered by position then name."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_ENC_SELECT_COLS} FROM raid_encounters WHERE raid_zone_id = ? ORDER BY position, mob_name",
            (zone_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_zones_by_expansion(short: str, path: Path = DB_PATH) -> list[dict]:
    """All raid zones in an expansion."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_ZONE_SELECT_COLS} FROM raid_zones WHERE expansion_short = ? ORDER BY zone_name",
            (short,),
        ).fetchall()
        return [dict(r) for r in rows]


def encounter_revisions(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    """Full revision history for an encounter, newest first."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, encounter_id, edited_at, edited_by, "
            "before_md, after_md, edit_note "
            "FROM raid_encounter_revisions "
            "WHERE encounter_id = ? ORDER BY edited_at DESC, id DESC",
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def stats(path: Path = DB_PATH) -> dict:
    """Diagnostic — counts by table + source."""
    if not path.exists():
        return {}
    with sqlite3.connect(path) as conn:
        out = {
            "zones": conn.execute("SELECT COUNT(*) FROM raid_zones").fetchone()[0],
            "encounters": conn.execute("SELECT COUNT(*) FROM raid_encounters").fetchone()[0],
            "revisions": conn.execute("SELECT COUNT(*) FROM raid_encounter_revisions").fetchone()[0],
            "encounters_by_source": dict(conn.execute("SELECT source, COUNT(*) FROM raid_encounters GROUP BY source")),
            "zones_by_expansion": dict(
                conn.execute(
                    "SELECT expansion_short, COUNT(*) FROM raid_zones GROUP BY expansion_short ORDER BY 2 DESC"
                )
            ),
        }
        return out


# ---------------------------------------------------------------------------
# ACT Triggers + Spell Timers
# ---------------------------------------------------------------------------
#
# Column names mirror the XML attribute names (snake_case) so the
# serialisation layer is a near-1:1 mapping. `name_lower` on spell timers
# powers the case-insensitive `timer_name`-based linkage from triggers.

_ACT_TRIGGER_COLS = (
    "id, raid_encounter_id, position, label, notes, "
    "active, regex, sound_data, sound_type, "
    "category_restrict, category, "
    "timer, timer_name, tabbed, "
    "last_edited_at, last_edited_by, created_at"
)

_ACT_SPELL_TIMER_COLS = (
    "id, raid_encounter_id, name, name_lower, "
    "checked, timer_duration_s, only_master_ticks, restrict, absolute_, "
    "start_wav, warning_wav, warning_value, "
    "radial_display, modable, tooltip, fill_color, "
    "panel1, panel2, remove_value, category, restrict_category, "
    "last_edited_at, last_edited_by, created_at"
)


def list_act_triggers_for_encounter(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    """Every ACT trigger row for an encounter, ordered by position then id."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers WHERE raid_encounter_id = ? ORDER BY position, id",
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_act_trigger(trigger_id: int, path: Path = DB_PATH) -> dict | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_act_trigger(
    conn: sqlite3.Connection,
    *,
    trigger_id: int | None = None,
    raid_encounter_id: int,
    regex: str,
    position: int = 0,
    label: str | None = None,
    notes: str | None = None,
    active: bool = True,
    sound_data: str = "",
    sound_type: int = 3,
    category_restrict: bool = False,
    category: str | None = None,
    timer: bool = False,
    timer_name: str | None = None,
    tabbed: bool = False,
    edited_by: str | None = None,
) -> int:
    """Insert or update a single trigger row. Pass ``trigger_id`` to UPDATE,
    omit it to INSERT. Returns the row id either way.

    Stores the audit stamp via the ``edited_by`` argument so route callers
    don't have to reach into the schema themselves."""
    now = int(time.time())
    params = (
        raid_encounter_id, position, label, notes,
        int(bool(active)), regex, sound_data, int(sound_type),
        int(bool(category_restrict)), category,
        int(bool(timer)), timer_name, int(bool(tabbed)),
        now, edited_by,
    )  # fmt: skip

    if trigger_id is None:
        cur = conn.execute(
            """
            INSERT INTO act_triggers (
                raid_encounter_id, position, label, notes,
                active, regex, sound_data, sound_type,
                category_restrict, category,
                timer, timer_name, tabbed,
                last_edited_at, last_edited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    conn.execute(
        """
        UPDATE act_triggers SET
            raid_encounter_id = ?, position = ?, label = ?, notes = ?,
            active = ?, regex = ?, sound_data = ?, sound_type = ?,
            category_restrict = ?, category = ?,
            timer = ?, timer_name = ?, tabbed = ?,
            last_edited_at = ?, last_edited_by = ?
        WHERE id = ?
        """,
        params + (trigger_id,),
    )
    conn.commit()
    return trigger_id


def delete_act_trigger(conn: sqlite3.Connection, trigger_id: int) -> bool:
    """Delete a trigger by id. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM act_triggers WHERE id = ?", (trigger_id,))
    conn.commit()
    return cur.rowcount > 0


def list_act_spell_timers_for_encounter(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    """Every spell-timer row for an encounter, alphabetical by name."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers WHERE raid_encounter_id = ? ORDER BY name",
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_act_spell_timer(timer_id: int, path: Path = DB_PATH) -> dict | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers WHERE id = ?",
            (timer_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_act_spell_timer(
    conn: sqlite3.Connection,
    *,
    timer_id: int | None = None,
    raid_encounter_id: int,
    name: str,
    timer_duration_s: int,
    checked: bool = False,
    only_master_ticks: bool = False,
    restrict: bool = False,
    absolute_: bool = False,
    start_wav: str = "",
    warning_wav: str = "",
    warning_value: int = 10,
    radial_display: bool = False,
    modable: bool = False,
    tooltip: str = "",
    fill_color: int = -16776961,
    panel1: bool = True,
    panel2: bool = False,
    remove_value: int = -15,
    category: str | None = None,
    restrict_category: bool = False,
    edited_by: str | None = None,
) -> int:
    """Insert or update a spell-timer row. Pass ``timer_id`` to UPDATE,
    omit it to INSERT. ``(raid_encounter_id, name_lower)`` is UNIQUE — on
    insert collision the caller should pass ``timer_id`` of the existing
    row instead."""
    now = int(time.time())
    params = (
        raid_encounter_id, name, name.lower(),
        int(bool(checked)), int(timer_duration_s),
        int(bool(only_master_ticks)), int(bool(restrict)), int(bool(absolute_)),
        start_wav, warning_wav, int(warning_value),
        int(bool(radial_display)), int(bool(modable)), tooltip, int(fill_color),
        int(bool(panel1)), int(bool(panel2)), int(remove_value),
        category, int(bool(restrict_category)),
        now, edited_by,
    )  # fmt: skip

    if timer_id is None:
        cur = conn.execute(
            """
            INSERT INTO act_spell_timers (
                raid_encounter_id, name, name_lower,
                checked, timer_duration_s,
                only_master_ticks, restrict, absolute_,
                start_wav, warning_wav, warning_value,
                radial_display, modable, tooltip, fill_color,
                panel1, panel2, remove_value,
                category, restrict_category,
                last_edited_at, last_edited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    conn.execute(
        """
        UPDATE act_spell_timers SET
            raid_encounter_id = ?, name = ?, name_lower = ?,
            checked = ?, timer_duration_s = ?,
            only_master_ticks = ?, restrict = ?, absolute_ = ?,
            start_wav = ?, warning_wav = ?, warning_value = ?,
            radial_display = ?, modable = ?, tooltip = ?, fill_color = ?,
            panel1 = ?, panel2 = ?, remove_value = ?,
            category = ?, restrict_category = ?,
            last_edited_at = ?, last_edited_by = ?
        WHERE id = ?
        """,
        params + (timer_id,),
    )
    conn.commit()
    return timer_id


def delete_act_spell_timer(conn: sqlite3.Connection, timer_id: int) -> bool:
    """Delete a spell-timer by id. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM act_spell_timers WHERE id = ?", (timer_id,))
    conn.commit()
    return cur.rowcount > 0
