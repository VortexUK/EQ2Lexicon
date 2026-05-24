"""
Normalized SQLite store for ingested ACT parses.

Mirrors the layout pattern of `census/recipes_db.py`:
  * `_CREATE_*` SQL constants
  * `init_db(path)` returns a connection with WAL/foreign-keys enabled
  * idempotent `_MIGRATIONS` list for future schema bumps
  * thin sync helpers for insert / lookup

Lives at `data/parses/parses.db` by default. Override with the
`PARSES_DB_PATH` env var.

Schema reflects the real columns ACT exports at AttackType depth — see
parses/act_reader.py for the source-side column-name mapping.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC
from pathlib import Path

from parses.models import AttackType, Combatant, DamageType, Encounter

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    env = os.getenv("PARSES_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "parses" / "parses.db"


DB_PATH: Path = _db_path()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_ENCOUNTERS = """
CREATE TABLE IF NOT EXISTS encounters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    act_encid       TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    zone            TEXT,
    started_at      INTEGER NOT NULL,        -- unix seconds, UTC
    ended_at        INTEGER NOT NULL,
    duration_s      INTEGER NOT NULL,
    total_damage    INTEGER NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    kills           INTEGER NOT NULL DEFAULT 0,
    deaths          INTEGER NOT NULL DEFAULT 0,
    -- ACT's GetEncounterSuccessLevel(): 0=unknown, 1=win, 2=loss, 3=mixed.
    -- Used by /parses to colour the encounter title green/red.
    success_level   INTEGER NOT NULL DEFAULT 0,
    source_dsn      TEXT    NOT NULL,
    uploaded_by     TEXT    NOT NULL DEFAULT 'local',
    guild_name      TEXT,
    ingested_at     INTEGER NOT NULL
);
"""

_CREATE_COMBATANTS = """
CREATE TABLE IF NOT EXISTS combatants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id    INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    ally            INTEGER NOT NULL DEFAULT 0,   -- 0/1 (ACT's 'T'/'F')
    started_at      INTEGER NOT NULL DEFAULT 0,
    ended_at        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    damage          INTEGER NOT NULL DEFAULT 0,
    damage_perc     REAL    NOT NULL DEFAULT 0,
    kills           INTEGER NOT NULL DEFAULT 0,
    healed          INTEGER NOT NULL DEFAULT 0,
    healed_perc     REAL    NOT NULL DEFAULT 0,
    crit_heals      INTEGER NOT NULL DEFAULT 0,
    heals           INTEGER NOT NULL DEFAULT 0,
    cure_dispels    INTEGER NOT NULL DEFAULT 0,
    power_drain     INTEGER NOT NULL DEFAULT 0,
    power_replenish INTEGER NOT NULL DEFAULT 0,
    dps             REAL    NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    enchps          REAL    NOT NULL DEFAULT 0,
    hits            INTEGER NOT NULL DEFAULT 0,
    crit_hits       INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    misses          INTEGER NOT NULL DEFAULT 0,
    swings          INTEGER NOT NULL DEFAULT 0,
    heals_taken     INTEGER NOT NULL DEFAULT 0,
    damage_taken    INTEGER NOT NULL DEFAULT 0,
    deaths          INTEGER NOT NULL DEFAULT 0,
    to_hit          REAL    NOT NULL DEFAULT 0,
    crit_dam_perc   REAL    NOT NULL DEFAULT 0,
    crit_heal_perc  REAL    NOT NULL DEFAULT 0,
    crit_types      TEXT,
    threat_str      TEXT,
    threat_delta    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    UNIQUE (encounter_id, name)
);
"""

_CREATE_DAMAGE_TYPES = """
CREATE TABLE IF NOT EXISTS damage_types (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    combatant_id    INTEGER NOT NULL,
    grouping_label  TEXT,
    damage_type     TEXT    NOT NULL,
    started_at      INTEGER NOT NULL DEFAULT 0,
    ended_at        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    damage          INTEGER NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    char_dps        REAL    NOT NULL DEFAULT 0,
    dps             REAL    NOT NULL DEFAULT 0,
    average         REAL    NOT NULL DEFAULT 0,
    median          INTEGER NOT NULL DEFAULT 0,
    min_hit         INTEGER NOT NULL DEFAULT 0,
    max_hit         INTEGER NOT NULL DEFAULT 0,
    hits            INTEGER NOT NULL DEFAULT 0,
    crit_hits       INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    misses          INTEGER NOT NULL DEFAULT 0,
    swings          INTEGER NOT NULL DEFAULT 0,
    to_hit          REAL    NOT NULL DEFAULT 0,
    average_delay   REAL    NOT NULL DEFAULT 0,
    crit_perc       REAL    NOT NULL DEFAULT 0,
    crit_types      TEXT,
    FOREIGN KEY (combatant_id) REFERENCES combatants(id) ON DELETE CASCADE,
    UNIQUE (combatant_id, damage_type)
);
"""

_CREATE_ATTACK_TYPES = """
CREATE TABLE IF NOT EXISTS attack_types (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    combatant_id    INTEGER NOT NULL,
    victim          TEXT,
    swing_type      INTEGER NOT NULL DEFAULT 0,
    attack_name     TEXT    NOT NULL,
    started_at      INTEGER NOT NULL DEFAULT 0,
    ended_at        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    damage          INTEGER NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    char_dps        REAL    NOT NULL DEFAULT 0,
    dps             REAL    NOT NULL DEFAULT 0,
    average         REAL    NOT NULL DEFAULT 0,
    median          INTEGER NOT NULL DEFAULT 0,
    min_hit         INTEGER NOT NULL DEFAULT 0,
    max_hit         INTEGER NOT NULL DEFAULT 0,
    resist          TEXT,
    hits            INTEGER NOT NULL DEFAULT 0,
    crit_hits       INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    misses          INTEGER NOT NULL DEFAULT 0,
    swings          INTEGER NOT NULL DEFAULT 0,
    to_hit          REAL    NOT NULL DEFAULT 0,
    average_delay   REAL    NOT NULL DEFAULT 0,
    crit_perc       REAL    NOT NULL DEFAULT 0,
    crit_types      TEXT,
    FOREIGN KEY (combatant_id) REFERENCES combatants(id) ON DELETE CASCADE,
    UNIQUE (combatant_id, swing_type, attack_name)
);
"""

_CREATE_INGEST_LOG = """
CREATE TABLE IF NOT EXISTS ingest_log (
    act_encid       TEXT    PRIMARY KEY,
    encounter_id    INTEGER NOT NULL,
    ingested_at     INTEGER NOT NULL,
    source_dsn      TEXT    NOT NULL,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_encounters_started_desc  ON encounters (started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_encounters_zone          ON encounters (zone);",
    "CREATE INDEX IF NOT EXISTS idx_encounters_uploaded_by   ON encounters (uploaded_by, started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_encounter     ON combatants (encounter_id);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_name          ON combatants (name);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_ally          ON combatants (encounter_id, ally);",
    "CREATE INDEX IF NOT EXISTS idx_damage_types_combatant   ON damage_types (combatant_id);",
    "CREATE INDEX IF NOT EXISTS idx_attack_types_combatant   ON attack_types (combatant_id);",
    "CREATE INDEX IF NOT EXISTS idx_attack_types_damage_desc ON attack_types (combatant_id, damage DESC);",
]

# Append idempotent ALTER TABLE statements here when the schema evolves —
# `init_db` swallows OperationalError so re-applying on an up-to-date DB is
# a no-op.
_MIGRATIONS: list[str] = [
    # Added when the /parses UI started grouping by uploader. Existing rows
    # default to 'local' (the local-only-ingest era).
    "ALTER TABLE encounters ADD COLUMN uploaded_by TEXT NOT NULL DEFAULT 'local'",
    # Guild attribution stamped on each encounter at ingest time. NULL means
    # 'unresolved' (either uploader='local', Census lookup failed, or the
    # character isn't currently in a guild). Pre-existing rows stay NULL
    # until backfilled via `ingest.py --backfill-guilds`.
    "ALTER TABLE encounters ADD COLUMN guild_name TEXT",
    # Win/loss flag from ACT's GetEncounterSuccessLevel(). 0 for pre-existing
    # rows where the uploader didn't supply it.
    "ALTER TABLE encounters ADD COLUMN success_level INTEGER NOT NULL DEFAULT 0",
]


# ---------------------------------------------------------------------------
# DB management
# ---------------------------------------------------------------------------


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables/indexes if missing. Returns an open connection."""
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(_CREATE_ENCOUNTERS)
    conn.execute(_CREATE_COMBATANTS)
    conn.execute(_CREATE_DAMAGE_TYPES)
    conn.execute(_CREATE_ATTACK_TYPES)
    conn.execute(_CREATE_INGEST_LOG)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    _migrate_attack_types_unique(conn)
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()
    return conn


def _migrate_attack_types_unique(conn: sqlite3.Connection) -> None:
    """Recreate attack_types if the legacy UNIQUE(combatant_id, attack_name)
    constraint is still in place. The natural key needs swing_type too —
    spells like Cleanse legitimately appear under both damage and heal."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='attack_types' "
        "AND name LIKE 'sqlite_autoindex_%'"
    ).fetchall()
    target = ["combatant_id", "swing_type", "attack_name"]
    for (idx_name,) in rows:
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info({idx_name})").fetchall()]
        if cols == target:
            return  # already migrated
    # Commit any pending implicit transaction so `with conn:` can scope a
    # fresh atomic one around the table swap.
    conn.commit()
    with conn:
        conn.execute(
            _CREATE_ATTACK_TYPES.replace(
                "CREATE TABLE IF NOT EXISTS attack_types",
                "CREATE TABLE attack_types_new",
            )
        )
        conn.execute("INSERT INTO attack_types_new SELECT * FROM attack_types")
        conn.execute("DROP TABLE attack_types")
        conn.execute("ALTER TABLE attack_types_new RENAME TO attack_types")


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


def _to_unix(dt) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        return int(dt.replace(tzinfo=UTC).timestamp())
    return int(dt.timestamp())


def insert_encounter(
    conn: sqlite3.Connection,
    enc: Encounter,
    *,
    source_dsn: str,
    ingested_at: int,
    uploaded_by: str = "local",
    guild_name: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO encounters (
            act_encid, title, zone,
            started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, success_level,
            source_dsn, uploaded_by, guild_name, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            enc.encid,
            enc.title,
            enc.zone,
            _to_unix(enc.started_at),
            _to_unix(enc.ended_at),
            enc.duration_s,
            enc.total_damage,
            enc.encdps,
            enc.kills,
            enc.deaths,
            enc.success_level,
            source_dsn,
            uploaded_by,
            guild_name,
            ingested_at,
        ),
    )
    return int(cur.lastrowid or 0)


def insert_combatants_bulk(
    conn: sqlite3.Connection,
    encounter_id: int,
    combatants: list[Combatant],
) -> dict[str, int]:
    name_to_id: dict[str, int] = {}
    for c in combatants:
        cur = conn.execute(
            """
            INSERT INTO combatants (
                encounter_id, name, ally,
                started_at, ended_at, duration_s,
                damage, damage_perc, kills,
                healed, healed_perc, crit_heals, heals, cure_dispels,
                power_drain, power_replenish,
                dps, encdps, enchps,
                hits, crit_hits, blocked, misses, swings,
                heals_taken, damage_taken, deaths,
                to_hit, crit_dam_perc, crit_heal_perc, crit_types,
                threat_str, threat_delta
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                encounter_id,
                c.name,
                1 if c.ally else 0,
                _to_unix(c.started_at),
                _to_unix(c.ended_at),
                c.duration_s,
                c.damage,
                c.damage_perc,
                c.kills,
                c.healed,
                c.healed_perc,
                c.crit_heals,
                c.heals,
                c.cure_dispels,
                c.power_drain,
                c.power_replenish,
                c.dps,
                c.encdps,
                c.enchps,
                c.hits,
                c.crit_hits,
                c.blocked,
                c.misses,
                c.swings,
                c.heals_taken,
                c.damage_taken,
                c.deaths,
                c.to_hit,
                c.crit_dam_perc,
                c.crit_heal_perc,
                c.crit_types,
                c.threat_str,
                c.threat_delta,
            ),
        )
        name_to_id[c.name] = int(cur.lastrowid or 0)
    return name_to_id


def insert_damage_types_bulk(
    conn: sqlite3.Connection,
    combatant_name_to_id: dict[str, int],
    damage_types: list[DamageType],
) -> int:
    rows = [
        (
            combatant_name_to_id[dt.combatant_name],
            dt.grouping_label,
            dt.damage_type,
            _to_unix(dt.started_at),
            _to_unix(dt.ended_at),
            dt.duration_s,
            dt.damage,
            dt.encdps,
            dt.char_dps,
            dt.dps,
            dt.average,
            dt.median,
            dt.min_hit,
            dt.max_hit,
            dt.hits,
            dt.crit_hits,
            dt.blocked,
            dt.misses,
            dt.swings,
            dt.to_hit,
            dt.average_delay,
            dt.crit_perc,
            dt.crit_types,
        )
        for dt in damage_types
        if dt.combatant_name in combatant_name_to_id
    ]
    conn.executemany(
        """
        INSERT INTO damage_types (
            combatant_id, grouping_label, damage_type,
            started_at, ended_at, duration_s,
            damage, encdps, char_dps, dps,
            average, median, min_hit, max_hit,
            hits, crit_hits, blocked, misses, swings,
            to_hit, average_delay, crit_perc, crit_types
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def insert_attack_types_bulk(
    conn: sqlite3.Connection,
    combatant_name_to_id: dict[str, int],
    attack_types: list[AttackType],
) -> int:
    rows = [
        (
            combatant_name_to_id[at.combatant_name],
            at.victim,
            at.swing_type,
            at.attack_name,
            _to_unix(at.started_at),
            _to_unix(at.ended_at),
            at.duration_s,
            at.damage,
            at.encdps,
            at.char_dps,
            at.dps,
            at.average,
            at.median,
            at.min_hit,
            at.max_hit,
            at.resist,
            at.hits,
            at.crit_hits,
            at.blocked,
            at.misses,
            at.swings,
            at.to_hit,
            at.average_delay,
            at.crit_perc,
            at.crit_types,
        )
        for at in attack_types
        if at.combatant_name in combatant_name_to_id
    ]
    conn.executemany(
        """
        INSERT INTO attack_types (
            combatant_id, victim, swing_type, attack_name,
            started_at, ended_at, duration_s,
            damage, encdps, char_dps, dps,
            average, median, min_hit, max_hit, resist,
            hits, crit_hits, blocked, misses, swings,
            to_hit, average_delay, crit_perc, crit_types
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def mark_ingested(
    conn: sqlite3.Connection,
    act_encid: str,
    encounter_id: int,
    *,
    source_dsn: str,
    ingested_at: int,
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log (act_encid, encounter_id, ingested_at, source_dsn)
        VALUES (?, ?, ?, ?)
        """,
        (act_encid, encounter_id, ingested_at, source_dsn),
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def is_ingested(conn: sqlite3.Connection, act_encid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ingest_log WHERE act_encid = ? LIMIT 1",
        (act_encid,),
    ).fetchone()
    return row is not None


def find_encounter_by_act_encid(conn: sqlite3.Connection, act_encid: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM encounters WHERE act_encid = ? LIMIT 1",
        (act_encid,),
    ).fetchone()
    return dict(row) if row else None


def recent_encounters(
    conn: sqlite3.Connection,
    limit: int = 20,
    zone: str | None = None,
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    if zone:
        rows = conn.execute(
            """
            SELECT * FROM encounters
            WHERE zone = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (zone, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM encounters ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_encounter(conn: sqlite3.Connection, encounter_id: int) -> bool:
    """Delete one encounter. Returns True if a row was removed, False if not
    found. ON DELETE CASCADE handles combatants / damage_types / attack_types
    / ingest_log."""
    with conn:
        cur = conn.execute("DELETE FROM encounters WHERE id = ?", (encounter_id,))
    return cur.rowcount > 0


def delete_encounters_by_filter(
    conn: sqlite3.Connection,
    *,
    guild_name: str,
    zone: str | None = None,
    date: str | None = None,  # 'YYYY-MM-DD' in the server's local timezone
    uploaded_by: str | None = None,
) -> int:
    """Bulk delete encounters matching the filter. `guild_name` is mandatory
    so we can never accidentally delete across guilds. Returns the row count
    removed. Cascades to children."""
    if not guild_name:
        raise ValueError("guild_name is required")
    clauses = ["guild_name = ?"]
    params: list = [guild_name]
    if zone:
        clauses.append("zone = ?")
        params.append(zone)
    if uploaded_by:
        clauses.append("uploaded_by = ?")
        params.append(uploaded_by)
    if date:
        # SQLite has no native YYYY-MM-DD-on-unix-seconds helper but `date(?,
        # 'unixepoch', 'localtime')` does the right thing. Matches what
        # ParsesPage groups on (fmtLocalDate, server clock).
        clauses.append("date(started_at, 'unixepoch', 'localtime') = ?")
        params.append(date)
    sql = f"DELETE FROM encounters WHERE {' AND '.join(clauses)}"
    with conn:
        cur = conn.execute(sql, params)
    return cur.rowcount


def get_combatants_for_encounter(conn: sqlite3.Connection, encounter_id: int) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM combatants WHERE encounter_id = ? ORDER BY damage DESC",
        (encounter_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ACT swing_type semantics confirmed against real EQ2 data:
#   1   = melee auto-attack
#   2   = skill/spell damage
#   3   = heal events (resist column: 'Hitpoints' regular heal, 'Absorption' ward)
#   20  = cures (resist='relieves'; the `damage` column is the number of
#         detrimental effects removed)
#   100 + type='All'  = aggregate rollup (filtered out at ingest)
#   100 + type!='All' = threat / buff procs (resist='Increase' for threat
#                       boosters like 'Undeniable Malice')
# ACT writes everything as 'AttackType' rows at depth 4 — we split by
# swing_type at query so each category gets its own UI tab.
_DAMAGE_SWING_TYPES = (1, 2)
_HEAL_SWING_TYPES = (3,)
_CURE_SWING_TYPES = (20,)
_THREAT_SWING_TYPES = (100,)  # callers should additionally filter type != 'All'


def get_top_attacks_for_combatant(
    conn: sqlite3.Connection,
    combatant_id: int,
    limit: int = 10,
) -> list[dict]:
    """Top damage abilities (excludes heals and the swing_type=100 rollup)."""
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_DAMAGE_SWING_TYPES))
    rows = conn.execute(
        f"""
        SELECT * FROM attack_types
        WHERE combatant_id = ? AND swing_type IN ({placeholders})
        ORDER BY damage DESC
        LIMIT ?
        """,
        (combatant_id, *_DAMAGE_SWING_TYPES, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_top_heals_for_combatant(
    conn: sqlite3.Connection,
    combatant_id: int,
    limit: int = 10,
) -> list[dict]:
    """Top heal abilities (swing_type=3). `damage` column = amount healed;
    `resist` column distinguishes regular 'Hitpoints' heals from
    'Absorption' wards."""
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_HEAL_SWING_TYPES))
    rows = conn.execute(
        f"""
        SELECT * FROM attack_types
        WHERE combatant_id = ? AND swing_type IN ({placeholders})
        ORDER BY damage DESC
        LIMIT ?
        """,
        (combatant_id, *_HEAL_SWING_TYPES, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_top_cures_for_combatant(
    conn: sqlite3.Connection,
    combatant_id: int,
    limit: int = 10,
) -> list[dict]:
    """Cure events (swing_type=20). The `damage` column is the count of
    detrimental effects removed; `hits` is how many times the cure was cast."""
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_CURE_SWING_TYPES))
    rows = conn.execute(
        f"""
        SELECT * FROM attack_types
        WHERE combatant_id = ? AND swing_type IN ({placeholders})
        ORDER BY hits DESC, damage DESC
        LIMIT ?
        """,
        (combatant_id, *_CURE_SWING_TYPES, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_top_threats_for_combatant(
    conn: sqlite3.Connection,
    combatant_id: int,
    limit: int = 10,
) -> list[dict]:
    """Threat / buff-proc rows (swing_type=100, type != 'All'). For threat
    procs the `damage` column is the threat-increase value; `hits` is
    proc count."""
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_THREAT_SWING_TYPES))
    rows = conn.execute(
        f"""
        SELECT * FROM attack_types
        WHERE combatant_id = ?
          AND swing_type IN ({placeholders})
          AND attack_name <> 'All'
        ORDER BY damage DESC
        LIMIT ?
        """,
        (combatant_id, *_THREAT_SWING_TYPES, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_damage_types_for_combatant(
    conn: sqlite3.Connection,
    combatant_id: int,
) -> list[dict]:
    """All damage_types rows for a combatant, sorted by damage DESC."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM damage_types
        WHERE combatant_id = ?
        ORDER BY damage DESC
        """,
        (combatant_id,),
    ).fetchall()
    return [dict(r) for r in rows]
