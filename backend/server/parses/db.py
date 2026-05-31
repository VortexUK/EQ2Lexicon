"""
Normalized SQLite store for ingested ACT parses.

Mirrors the layout pattern of `census/recipes_db.py`:
  * `_CREATE_*` SQL constants
  * `init_db(path)` returns a connection with WAL/foreign-keys enabled
  * idempotent `_MIGRATIONS` list for future schema bumps
  * thin sync helpers for insert / lookup

Lives at `data/parses/parses.db` by default. Override with the
`DB_PARSES_PATH` env var.

Schema reflects the real columns ACT exports at AttackType depth — the
plugin's PayloadBuilder (in the EQ2LexiconACTPlugin repo) is the upstream
source-of-truth for column-name mappings on the wire.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC
from enum import IntEnum
from pathlib import Path

from backend.server.parses.models import AttackType, Combatant, CombatantSnapshot, DamageType, Encounter

# Reused for combatants with no resolved identity snapshot — stores NULLs.
_EMPTY_SNAPSHOT = CombatantSnapshot()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    env = os.getenv("DB_PARSES_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data" / "parses" / "parses.db"


DB_PATH: Path = _db_path()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_ENCOUNTERS = """
CREATE TABLE IF NOT EXISTS encounters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    world           TEXT    NOT NULL DEFAULT 'Varsoon',
    act_encid       TEXT    NOT NULL,
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
    ingested_at     INTEGER NOT NULL,
    -- Soft-delete marker (unix seconds). NULL = visible. Set when a boss-kill
    -- parse is "deleted" so the leaderboard entry + its link survive while the
    -- row is hidden from the /parses list. Hard purge removes the row entirely.
    hidden_at       INTEGER,
    UNIQUE (world, act_encid)
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
    -- Identity snapshot frozen at ingest (resolved via character_cache).
    -- NULL for pets/NPCs and players we couldn't resolve at upload time.
    level           INTEGER,
    guild_name      TEXT,
    cls             TEXT,
    ilvl            REAL,
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
    world           TEXT    NOT NULL DEFAULT 'Varsoon',
    act_encid       TEXT    NOT NULL,
    encounter_id    INTEGER NOT NULL,
    ingested_at     INTEGER NOT NULL,
    source_dsn      TEXT    NOT NULL,
    PRIMARY KEY (world, act_encid),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
);
"""

# Audit table for parses the plugin refused to send to the leaderboard
# because a tamper heuristic tripped. Populated by POST /api/parses/tamper-report
# (see web/routes/parses/tamper_report.py). Deliberately NOT joined to the
# encounters table — these rows MUST NEVER appear on public leaderboards;
# admins read them via /api/admin/tamper-reports to see what users were
# attempting to upload.
#
# Reason codes emitted by the plugin today:
#   * "title_enemy_mismatch"     — heuristic for ACT's right-click rename
#   * "stale_encounter"          — EndTime > 1h ago (almost certainly an import)
#   * "recent_import_activity"   — user was in ACT's import UI within 30s
# Stored as free-form TEXT so a plugin update can add new codes without a
# server schema bump — old admins still see the report, the reason text is
# just an opaque token until the admin UI is updated to recognise it.
#
# `payload_json` keeps the full body so a heuristic that fires today can be
# re-examined later (false-positive review, threshold tuning). Capped server-
# side at the same 10 MB ceiling as /ingest to keep a hostile plugin from
# filling the DB.
_CREATE_TAMPER_REPORTS = """
CREATE TABLE IF NOT EXISTS tamper_reports (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    world                   TEXT    NOT NULL DEFAULT 'Varsoon',
    act_encid               TEXT    NOT NULL,
    title                   TEXT    NOT NULL,
    zone                    TEXT,
    started_at              INTEGER NOT NULL,   -- unix seconds, UTC
    ended_at                INTEGER NOT NULL,
    duration_s              INTEGER NOT NULL,
    total_damage            INTEGER NOT NULL DEFAULT 0,
    encdps                  REAL    NOT NULL DEFAULT 0,
    -- One of "title_enemy_mismatch" / "stale_encounter" /
    -- "recent_import_activity" (plus any future codes the plugin adds).
    -- Stored verbatim from the X-Lexicon-Tamper-Reason header.
    reason                  TEXT    NOT NULL,
    reported_at             INTEGER NOT NULL,
    -- Uploader identity. logger_name is the EQ2 character name from the
    -- payload; discord_id/name come from resolving the Bearer token.
    -- These default to "" when the token resolution couldn't surface a
    -- friendly name (kept distinct from NULL so a missing column never
    -- silently maps to None).
    uploader_logger_name    TEXT    NOT NULL DEFAULT '',
    uploader_discord_id     TEXT    NOT NULL DEFAULT '',
    uploader_discord_name   TEXT    NOT NULL DEFAULT '',
    guild_name              TEXT,
    payload_json            TEXT    NOT NULL,
    -- NULL = unacknowledged (pending admin review). Set when an admin
    -- clicks the "Acknowledge" button in the panel.
    acknowledged_at         INTEGER,
    acknowledged_by         TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_encounters_started_desc  ON encounters (started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_encounters_zone          ON encounters (zone);",
    "CREATE INDEX IF NOT EXISTS idx_encounters_world         ON encounters (world, started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_encounters_uploaded_by   ON encounters (uploaded_by, started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_encounter     ON combatants (encounter_id);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_name          ON combatants (name);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_ally          ON combatants (encounter_id, ally);",
    "CREATE INDEX IF NOT EXISTS idx_damage_types_combatant   ON damage_types (combatant_id);",
    "CREATE INDEX IF NOT EXISTS idx_attack_types_combatant   ON attack_types (combatant_id);",
    "CREATE INDEX IF NOT EXISTS idx_attack_types_damage_desc ON attack_types (combatant_id, damage DESC);",
    "CREATE INDEX IF NOT EXISTS idx_combatants_encounter_is_player ON combatants (encounter_id, is_player);",
    # Tamper reports: admin view defaults to pending-only, so an unack
    # partial index is the hot path. The reporter index supports the
    # /api/admin/users → "this user has N tamper reports" lookup we'll
    # likely want when the audit panel grows.
    "CREATE INDEX IF NOT EXISTS idx_tamper_reports_unack ON tamper_reports (reported_at DESC) WHERE acknowledged_at IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_tamper_reports_reporter ON tamper_reports (uploader_discord_id, reported_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_tamper_reports_world_reported ON tamper_reports (world, reported_at DESC);",
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
    # Per-combatant identity snapshot frozen at ingest time (resolved from the
    # character_cache). Pre-existing rows stay NULL — the parse page falls back
    # to the live /api/characters/lookup for those.
    "ALTER TABLE combatants ADD COLUMN level INTEGER",
    "ALTER TABLE combatants ADD COLUMN guild_name TEXT",
    "ALTER TABLE combatants ADD COLUMN cls TEXT",
    "ALTER TABLE combatants ADD COLUMN ilvl REAL",
    # Soft-delete marker for parses. Pre-existing rows are visible (NULL).
    "ALTER TABLE encounters ADD COLUMN hidden_at INTEGER",
    # Phase-1 pet-detection pipeline: is_player flag (per-combatant) is the
    # authoritative player/pet signal. DEFAULT NULL = the lazy-backfill
    # sentinel; pre-existing rows get classified on first read of their
    # parent encounter (see web/routes/parses/list.py:_ensure_classified).
    "ALTER TABLE combatants ADD COLUMN is_player INTEGER DEFAULT NULL",
    # Soft warnings the plugin attaches to an otherwise-successful upload —
    # currently just "folder_hint_mismatch" (ACT's per-encounter
    # HistoryRecord.FolderHint disagreed with the detected logger_server).
    # Stored as a JSON-encoded list of strings; NULL = no warnings on this
    # parse (the plugin omits the key entirely when there's nothing to flag,
    # so NULL is the resting state for non-tampered uploads).
    # Surfaced via /api/admin/parses so the admin table can show a ⚠ chip;
    # NOT shown on the public /parses list — it's audit signal, not user-facing.
    "ALTER TABLE encounters ADD COLUMN client_warnings TEXT",
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
    conn.execute(_CREATE_TAMPER_REPORTS)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    _migrate_attack_types_unique(conn)
    _migrate_encounters_add_world(conn)
    _migrate_ingest_log_add_world(conn)
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


def _migrate_encounters_add_world(conn: sqlite3.Connection) -> None:
    """Add the `world` column to encounters and change the uniqueness key from
    the single-column UNIQUE on act_encid to UNIQUE(world, act_encid).

    SQLite can't ALTER a uniqueness constraint in place, so we use the standard
    table-rebuild pattern:  rename old → create new → INSERT … SELECT → drop old.

    Guard: skip if the `world` column already exists (idempotent).
    FK safety: combatants.encounter_id references encounters(id). The rebuild
    preserves all `id` values via an explicit column list (including the
    original INTEGER PRIMARY KEY AUTOINCREMENT sequence), so child rows remain
    valid after the swap. PRAGMA foreign_keys is turned OFF for the duration of
    the rebuild so SQLite does not object while encounters_old is the target;
    it is re-enabled immediately after."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(encounters)").fetchall()]
    if "world" in cols:
        return  # already migrated

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute("PRAGMA legacy_alter_table = ON;")
    try:
        with conn:
            conn.execute("ALTER TABLE encounters RENAME TO encounters_old")
            conn.execute(_CREATE_ENCOUNTERS.replace("CREATE TABLE IF NOT EXISTS encounters", "CREATE TABLE encounters"))
            # Copy all existing rows, backfilling world = 'Varsoon'.
            conn.execute(
                """
                INSERT INTO encounters (
                    id, world, act_encid, title, zone,
                    started_at, ended_at, duration_s,
                    total_damage, encdps, kills, deaths, success_level,
                    source_dsn, uploaded_by, guild_name, ingested_at, hidden_at
                )
                SELECT
                    id, 'Varsoon', act_encid, title, zone,
                    started_at, ended_at, duration_s,
                    total_damage, encdps, kills, deaths, success_level,
                    source_dsn, uploaded_by, guild_name, ingested_at, hidden_at
                FROM encounters_old
                """
            )
            conn.execute("DROP TABLE encounters_old")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF;")
        conn.execute("PRAGMA foreign_keys = ON;")


def _migrate_ingest_log_add_world(conn: sqlite3.Connection) -> None:
    """Add `world` to ingest_log and change PK from act_encid alone to
    (world, act_encid).  Same rebuild pattern as encounters; guard on 'world'
    column presence."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ingest_log)").fetchall()]
    if "world" in cols:
        return  # already migrated

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute("PRAGMA legacy_alter_table = ON;")
    try:
        with conn:
            conn.execute("ALTER TABLE ingest_log RENAME TO ingest_log_old")
            conn.execute(_CREATE_INGEST_LOG.replace("CREATE TABLE IF NOT EXISTS ingest_log", "CREATE TABLE ingest_log"))
            conn.execute(
                """
                INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn)
                SELECT 'Varsoon', act_encid, encounter_id, ingested_at, source_dsn
                FROM ingest_log_old
                """
            )
            conn.execute("DROP TABLE ingest_log_old")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF;")
        conn.execute("PRAGMA foreign_keys = ON;")


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
    world: str = "Varsoon",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO encounters (
            world, act_encid, title, zone,
            started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, success_level,
            source_dsn, uploaded_by, guild_name, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            world,
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
    snapshots: dict[str, CombatantSnapshot] | None = None,
) -> dict[str, int]:
    """Insert combatant rows. ``snapshots`` (name → CombatantSnapshot) carries
    the level/guild/class frozen at ingest time; missing names store NULLs."""
    snap_by_lower = {k.lower(): v for k, v in (snapshots or {}).items()}
    name_to_id: dict[str, int] = {}
    for c in combatants:
        snap = snap_by_lower.get(c.name.lower(), _EMPTY_SNAPSHOT)
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
                threat_str, threat_delta,
                level, guild_name, cls, ilvl
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
                ?, ?,
                ?, ?, ?, ?
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
                snap.level,
                snap.guild_name,
                snap.cls,
                snap.ilvl,
            ),
        )
        name_to_id[c.name] = int(cur.lastrowid or 0)
    return name_to_id


def update_combatant_snapshots(
    conn: sqlite3.Connection,
    encounter_id: int,
    snapshots: dict[str, CombatantSnapshot],
) -> int:
    """Fill in level/guild/class on already-inserted combatant rows once the
    (possibly slow) Census resolution finishes in the background. Matches by
    combatant name within the encounter. Returns rows updated."""
    if not snapshots:
        return 0
    n = 0
    with conn:
        for name, snap in snapshots.items():
            cur = conn.execute(
                "UPDATE combatants SET level = ?, guild_name = ?, cls = ?, ilvl = ? WHERE encounter_id = ? AND name = ?",
                (snap.level, snap.guild_name, snap.cls, snap.ilvl, encounter_id, name),
            )
            n += cur.rowcount
    return n


def update_combatant_is_player(conn: sqlite3.Connection, classification: dict[int, bool]) -> None:
    """Bulk UPDATE the per-combatant is_player flag.

    Called from:
      * the ingest path, after the classifier runs against newly-inserted rows
      * the async snapshot fill, after cls fills in (which can flip stage 5)
      * the lazy-backfill helper in web/routes/parses/list.py

    No-op when ``classification`` is empty. Caller owns the connection
    and transaction scope."""
    if not classification:
        return
    conn.executemany(
        "UPDATE combatants SET is_player = ? WHERE id = ?",
        [(1 if v else 0, k) for k, v in classification.items()],
    )


def invalidate_is_player_cache_with_conn(conn: sqlite3.Connection) -> None:
    """Mark every combatant row for lazy re-classification on next read.
    Variant that accepts an existing connection (used by tests + by the
    rankings cache-invalidation hook to share the parses.db connection)."""
    conn.execute("UPDATE combatants SET is_player = NULL")


def invalidate_is_player_cache(path: Path = DB_PATH) -> None:
    """Mark every combatant row for lazy re-classification on next read.
    Production caller (opens its own connection).

    Called by web/routes/rankings.py:invalidate_zones_cache so that a
    curator zone-edit propagates to the existing parses without a
    separate backfill — the next read of each encounter re-classifies
    against the updated zone trees.

    Brute-force table-wide invalidation is fine at current data size
    (test parses only as of 2026-05-30). If the parses corpus grows past
    ~10k encounters and this becomes painful, swap for a per-zone-targeted
    invalidation using an is_player_computed_at timestamp."""
    with sqlite3.connect(path) as conn:
        invalidate_is_player_cache_with_conn(conn)


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
    world: str = "Varsoon",
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn)
        VALUES (?, ?, ?, ?, ?)
        """,
        (world, act_encid, encounter_id, ingested_at, source_dsn),
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def is_ingested(conn: sqlite3.Connection, act_encid: str, world: str = "Varsoon") -> bool:
    row = conn.execute(
        "SELECT 1 FROM ingest_log WHERE world = ? AND act_encid = ? LIMIT 1",
        (world, act_encid),
    ).fetchone()
    return row is not None


def find_encounter_by_act_encid(conn: sqlite3.Connection, act_encid: str, world: str = "Varsoon") -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM encounters WHERE world = ? AND act_encid = ? LIMIT 1",
        (world, act_encid),
    ).fetchone()
    return dict(row) if row else None


def recent_encounters(
    conn: sqlite3.Connection,
    limit: int = 20,
    zone: str | None = None,
    world: str = "Varsoon",
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    if zone:
        rows = conn.execute(
            """
            SELECT * FROM encounters
            WHERE world = ? AND zone = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (world, zone, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM encounters WHERE world = ? ORDER BY started_at DESC LIMIT ?",
            (world, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_encounters_for_admin(
    conn: sqlite3.Connection,
    *,
    search: str | None = None,
    limit: int = 200,
    world: str | None = None,
) -> list[dict]:
    """All encounters INCLUDING hidden (soft-deleted) ones, newest first, for
    the admin sanitize view. Optional case-insensitive search over
    title / uploaded_by / guild_name. Includes a player_count and the hidden_at
    marker so an admin can spot a bogus parse even when it's hidden but still
    polluting the leaderboards.

    ``world`` scopes to a single EQ2 server; ``None`` returns all worlds
    (no longer recommended — pass the active server world in all call sites)."""
    conn.row_factory = sqlite3.Row
    clauses: list[str] = []
    params: list = []
    if world is not None:
        clauses.append("e.world = ?")
        params.append(world)
    if search:
        like = f"%{search.lower()}%"
        clauses.append(
            "(LOWER(title) LIKE ? OR LOWER(IFNULL(uploaded_by, '')) LIKE ? OR LOWER(IFNULL(guild_name, '')) LIKE ?)"
        )
        params += [like, like, like]
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT e.id, e.title, e.zone, e.guild_name, e.uploaded_by, e.started_at,
               e.duration_s, e.success_level, e.hidden_at, e.client_warnings,
               (SELECT COUNT(*) FROM combatants c
                  WHERE c.encounter_id = e.id AND c.ally = 1
                    AND c.name != '' AND c.name != 'Unknown'
                    AND instr(c.name, ' ') = 0) AS player_count
        FROM encounters e
        {where}
        ORDER BY e.started_at DESC
        LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]


def delete_encounter(conn: sqlite3.Connection, encounter_id: int) -> bool:
    """Delete one encounter. Returns True if a row was removed, False if not
    found. ON DELETE CASCADE handles combatants / damage_types / attack_types
    / ingest_log."""
    with conn:
        cur = conn.execute("DELETE FROM encounters WHERE id = ?", (encounter_id,))
    return cur.rowcount > 0


def soft_delete_encounter(conn: sqlite3.Connection, encounter_id: int, hidden_at: int) -> bool:
    """Hide an encounter from the parses list without removing it, so any
    leaderboard entry sourced from it survives and its link still opens.
    Only acts on a currently-visible row; returns True if it flipped one."""
    with conn:
        cur = conn.execute(
            "UPDATE encounters SET hidden_at = ? WHERE id = ? AND hidden_at IS NULL",
            (hidden_at, encounter_id),
        )
    return cur.rowcount > 0


def unhide_encounter(conn: sqlite3.Connection, encounter_id: int) -> bool:
    """Clear a soft-delete marker so a previously-hidden parse becomes visible
    again (used when its encounter is re-uploaded). Returns True if a hidden
    row was un-hidden."""
    with conn:
        cur = conn.execute(
            "UPDATE encounters SET hidden_at = NULL WHERE id = ? AND hidden_at IS NOT NULL",
            (encounter_id,),
        )
    return cur.rowcount > 0


def set_encounter_guild_name(conn: sqlite3.Connection, encounter_id: int, guild_name: str | None) -> bool:
    """Set (or clear) the guild_name on an encounter row. Returns True if the row was updated."""
    with conn:
        cur = conn.execute(
            "UPDATE encounters SET guild_name = ? WHERE id = ?",
            (guild_name, encounter_id),
        )
    return cur.rowcount > 0


def find_encounters_by_filter(
    conn: sqlite3.Connection,
    *,
    guild_name: str,
    zone: str | None = None,
    date: str | None = None,
    uploaded_by: str | None = None,
    world: str | None = None,
) -> list[dict]:
    """Return (id, title, guild_name, source_dsn) for encounters matching the
    same filter `delete_encounters_by_filter` uses — so the route can decide
    soft-vs-hard delete per row. `guild_name` is mandatory.

    Pass `world` to restrict results to a single server (used by the bulk-
    delete route to enforce per-server isolation)."""
    if not guild_name:
        raise ValueError("guild_name is required")
    clauses = ["guild_name = ?"]
    params: list = [guild_name]
    if world:
        clauses.append("world = ?")
        params.append(world)
    if zone:
        clauses.append("zone = ?")
        params.append(zone)
    if uploaded_by:
        clauses.append("uploaded_by = ?")
        params.append(uploaded_by)
    if date:
        clauses.append("date(started_at, 'unixepoch', 'localtime') = ?")
        params.append(date)
    conn.row_factory = sqlite3.Row
    sql = f"SELECT id, title, guild_name, source_dsn FROM encounters WHERE {' AND '.join(clauses)}"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


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


class SwingType(IntEnum):
    """ACT swingtype column values — confirmed against ACT's attacktype_table
    column semantics (see the comment block immediately above this class
    for the full enumeration)."""

    MELEE = 1
    NONMELEE = 2
    HEAL = 3
    CURE = 20
    PROC = 100


_DAMAGE_SWING_TYPES = (SwingType.MELEE, SwingType.NONMELEE)
_HEAL_SWING_TYPES = (SwingType.HEAL,)
_CURE_SWING_TYPES = (SwingType.CURE,)
_THREAT_SWING_TYPES = (SwingType.PROC,)  # callers should additionally filter type != 'All'


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


# ---------------------------------------------------------------------------
# client_warnings (soft warnings on otherwise-successful uploads)
# ---------------------------------------------------------------------------


def set_encounter_client_warnings(
    conn: sqlite3.Connection,
    encounter_id: int,
    warnings_json: str | None,
) -> None:
    """Set (or clear) the client_warnings JSON blob on an encounter.

    Pass ``None`` (or an empty string) when the plugin didn't send any
    warnings — the column stays NULL, which is the "no warnings" sentinel
    the admin table uses to decide whether to render the ⚠ chip.

    Caller is responsible for serialising the list of warning strings to
    JSON BEFORE calling this (we keep the storage layer string-typed so a
    test can drop arbitrary text in and we don't ship a JSON dependency
    at the schema layer).
    """
    payload = warnings_json if warnings_json else None
    conn.execute(
        "UPDATE encounters SET client_warnings = ? WHERE id = ?",
        (payload, encounter_id),
    )


# ---------------------------------------------------------------------------
# tamper_reports (audit channel for blocked-from-leaderboard uploads)
# ---------------------------------------------------------------------------


def insert_tamper_report(
    conn: sqlite3.Connection,
    *,
    world: str,
    act_encid: str,
    title: str,
    zone: str | None,
    started_at: int,
    ended_at: int,
    duration_s: int,
    total_damage: int,
    encdps: float,
    reason: str,
    reported_at: int,
    uploader_logger_name: str,
    uploader_discord_id: str,
    uploader_discord_name: str,
    guild_name: str | None,
    payload_json: str,
) -> int:
    """Insert a tamper report. Returns the new row id.

    No idempotency — the plugin fires one tamper report per blocked
    encounter, and a user retrying (e.g. via right-click → Upload after
    an auto-skip) deserves a second row showing the second attempt. The
    encid is preserved in the column so admins can correlate retries by
    (world, act_encid) at query time.
    """
    cur = conn.execute(
        """
        INSERT INTO tamper_reports (
            world, act_encid, title, zone,
            started_at, ended_at, duration_s,
            total_damage, encdps,
            reason, reported_at,
            uploader_logger_name, uploader_discord_id, uploader_discord_name,
            guild_name, payload_json
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?
        )
        """,
        (
            world,
            act_encid,
            title,
            zone,
            started_at,
            ended_at,
            duration_s,
            total_damage,
            encdps,
            reason,
            reported_at,
            uploader_logger_name,
            uploader_discord_id,
            uploader_discord_name,
            guild_name,
            payload_json,
        ),
    )
    return int(cur.lastrowid or 0)


def list_tamper_reports(
    conn: sqlite3.Connection,
    *,
    world: str | None = None,
    reason: str | None = None,
    status: str = "pending",
    limit: int = 200,
) -> list[dict]:
    """Read tamper reports for the admin panel.

    ``status`` is one of:
      * "pending"  — acknowledged_at IS NULL (default — the admin's working set)
      * "ack"      — acknowledged_at IS NOT NULL
      * "all"      — both

    Returns rows newest-first. ``payload_json`` is included verbatim so the
    admin UI can drill in if they want the full evidence; the listing
    callers typically render a summary row and let an admin click in for
    detail.
    """
    clauses: list[str] = []
    params: list = []
    if world is not None:
        clauses.append("world = ?")
        params.append(world)
    if reason is not None:
        clauses.append("reason = ?")
        params.append(reason)
    if status == "pending":
        clauses.append("acknowledged_at IS NULL")
    elif status == "ack":
        clauses.append("acknowledged_at IS NOT NULL")
    elif status == "all":
        pass  # no extra filter
    else:
        raise ValueError(f"unknown status {status!r}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn.row_factory = sqlite3.Row
    sql = f"""
        SELECT id, world, act_encid, title, zone,
               started_at, ended_at, duration_s,
               total_damage, encdps,
               reason, reported_at,
               uploader_logger_name, uploader_discord_id, uploader_discord_name,
               guild_name, payload_json,
               acknowledged_at, acknowledged_by
        FROM tamper_reports
        {where}
        ORDER BY reported_at DESC
        LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]


def acknowledge_tamper_report(
    conn: sqlite3.Connection,
    report_id: int,
    *,
    acknowledged_at: int,
    acknowledged_by: str,
) -> bool:
    """Mark a tamper report as reviewed. Returns True if a pending row was
    flipped; False if the id doesn't exist OR was already acknowledged.

    Acknowledge is one-way — there's no "unacknowledge". If an admin
    wants to revisit, they can read the row via the ``status="ack"`` or
    ``status="all"`` listing.
    """
    with conn:
        cur = conn.execute(
            """
            UPDATE tamper_reports
               SET acknowledged_at = ?, acknowledged_by = ?
             WHERE id = ? AND acknowledged_at IS NULL
            """,
            (acknowledged_at, acknowledged_by, report_id),
        )
    return cur.rowcount > 0


def count_pending_tamper_reports(
    conn: sqlite3.Connection,
    world: str | None = None,
) -> int:
    """Cheap count of unack'd reports — used by the admin panel badge so
    the maintainer can see at a glance whether anything new needs review."""
    if world is None:
        row = conn.execute("SELECT COUNT(*) FROM tamper_reports WHERE acknowledged_at IS NULL").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM tamper_reports WHERE world = ? AND acknowledged_at IS NULL",
            (world,),
        ).fetchone()
    return int(row[0]) if row else 0
