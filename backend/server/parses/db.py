"""
Normalized SQLite store for ingested ACT parses.

All behaviour lives on :class:`ParsesStore` (the catalogue convention —
see backend/db_catalogue.py): ``store.init_db()`` returns a connection
with WAL/foreign-keys enabled (schema + idempotent ``_MIGRATIONS`` applied
via the base template method); the conn-taking insert/lookup helpers are
staticmethods — callers batch several operations per connection.

Lives at `data/parses/parses.db` by default. Override with the
`DB_PARSES_PATH` env var.

Schema reflects the real columns ACT exports at AttackType depth — the
plugin's PayloadBuilder (in the EQ2LexiconACTPlugin repo) is the upstream
source-of-truth for column-name mappings on the wire.
"""

from __future__ import annotations

import sqlite3
from enum import IntEnum
from pathlib import Path

from backend.db_catalogue import BaseCatalogue
from backend.db_helpers import resolve_db_path
from backend.server.parses.models import AttackType, Combatant, CombatantSnapshot, DamageType, Encounter
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

# Reused for combatants with no resolved identity snapshot — stores NULLs.
_EMPTY_SNAPSHOT = CombatantSnapshot()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DB_PATH: Path = resolve_db_path("DB_PARSES_PATH", "parses", "parses.db")


# Schema (CREATE TABLE / INDEX) lives in db.sql; init_db runs each block.
# Back-compat aliases — keep tests + any external callers that imported
# _CREATE_* directly off this module working without an import-shape change.
_CREATE_ENCOUNTERS = _SQL["schema_encounters"]
_CREATE_COMBATANTS = _SQL["schema_combatants"]
_CREATE_DAMAGE_TYPES = _SQL["schema_damage_types"]
_CREATE_ATTACK_TYPES = _SQL["schema_attack_types"]
_CREATE_INGEST_LOG = _SQL["schema_ingest_log"]
_CREATE_TAMPER_REPORTS = _SQL["schema_tamper_reports"]
# `indexes_all` is one multi-statement block; split on semicolons to keep the
# legacy list shape that test fixtures iterate.
_CREATE_INDEXES = [s.strip() + ";" for s in _SQL["indexes_all"].split(";") if s.strip()]

# Idempotent ALTER migrations — each statement loaded from db.sql. init_db
# loops the list, swallowing OperationalError so re-runs on an up-to-date DB
# are no-ops. Order is significant: column-dependent migrations (e.g. an
# index on a new column) MUST come after the ADD COLUMN they depend on.
_MIGRATIONS: list[str] = [
    _SQL["alter_encounters_add_uploaded_by"],
    _SQL["alter_encounters_add_guild_name"],
    _SQL["alter_encounters_add_success_level"],
    _SQL["alter_combatants_add_level"],
    _SQL["alter_combatants_add_guild_name"],
    _SQL["alter_combatants_add_cls"],
    _SQL["alter_combatants_add_ilvl"],
    _SQL["alter_encounters_add_hidden_at"],
    _SQL["alter_combatants_add_is_player"],
    _SQL["alter_encounters_add_client_warnings"],
]


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


class ParsesStore(BaseCatalogue):
    """Read/write access to one parses.db file (uploaded ACT encounters).

    The catalogue convention (see backend/db_catalogue.py): the shared
    module-level ``store`` instance is the runtime entry point (consumers
    alias it ``parses_db``); the conn-taking helpers are staticmethods —
    callers batch several operations per connection/transaction. Tests
    construct ``ParsesStore(tmp_db)``.
    """

    FOREIGN_KEYS = True

    # parses.db predates the shared _meta table; rows carry their own
    # timestamps and litestream owns backup provenance.
    CREATE_META = False

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    # ---------------------------------------------------------------------------
    # DB management
    # ---------------------------------------------------------------------------

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_encounters"])
        conn.execute(_SQL["schema_combatants"])
        conn.execute(_SQL["schema_damage_types"])
        conn.execute(_SQL["schema_attack_types"])
        conn.execute(_SQL["schema_ingest_log"])
        conn.execute(_SQL["schema_tamper_reports"])
        self._apply_migrations(conn, _MIGRATIONS)
        self._migrate_attack_types_unique(conn)
        self._migrate_encounters_add_world(conn)
        self._migrate_ingest_log_add_world(conn)
        conn.executescript(_SQL["indexes_all"])

    @staticmethod
    def _migrate_attack_types_unique(conn: sqlite3.Connection) -> None:
        """Recreate attack_types if the legacy UNIQUE(combatant_id, attack_name)
        constraint is still in place. The natural key needs swing_type too —
        spells like Cleanse legitimately appear under both damage and heal."""
        rows = conn.execute(_SQL["migrate_check_attack_types_indexes"]).fetchall()
        target = ["combatant_id", "swing_type", "attack_name"]
        for (idx_name,) in rows:
            cols = [r[2] for r in conn.execute(_SQL["pragma_index_info"].format(idx_name=idx_name)).fetchall()]
            if cols == target:
                return  # already migrated
        # Commit any pending implicit transaction so `with conn:` can scope a
        # fresh atomic one around the table swap.
        conn.commit()
        with conn:
            conn.execute(
                _SQL["schema_attack_types"].replace(
                    "CREATE TABLE IF NOT EXISTS attack_types",
                    "CREATE TABLE attack_types_new",
                )
            )
            conn.execute(_SQL["migrate_attack_types_insert_into_new"])
            conn.execute(_SQL["migrate_attack_types_drop_old"])
            conn.execute(_SQL["migrate_attack_types_rename"])

    @staticmethod
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
        cols = [r[1] for r in conn.execute(_SQL["pragma_table_info_encounters"]).fetchall()]
        if "world" in cols:
            return  # already migrated

        conn.commit()
        conn.execute(_SQL["pragma_foreign_keys_off"])
        conn.execute(_SQL["pragma_legacy_alter_table_on"])
        try:
            with conn:
                conn.execute(_SQL["migrate_encounters_rename_old"])
                conn.execute(
                    _SQL["schema_encounters"].replace(
                        "CREATE TABLE IF NOT EXISTS encounters", "CREATE TABLE encounters"
                    )
                )
                # Copy all existing rows, backfilling world = 'Varsoon'.
                conn.execute(_SQL["migrate_encounters_copy_from_old"])
                conn.execute(_SQL["migrate_encounters_drop_old"])
        finally:
            conn.execute(_SQL["pragma_legacy_alter_table_off"])
            conn.execute(_SQL["pragma_foreign_keys_on"])

    @staticmethod
    def _migrate_ingest_log_add_world(conn: sqlite3.Connection) -> None:
        """Add `world` to ingest_log and change PK from act_encid alone to
        (world, act_encid).  Same rebuild pattern as encounters; guard on 'world'
        column presence."""
        cols = [r[1] for r in conn.execute(_SQL["pragma_table_info_ingest_log"]).fetchall()]
        if "world" in cols:
            return  # already migrated

        conn.commit()
        conn.execute(_SQL["pragma_foreign_keys_off"])
        conn.execute(_SQL["pragma_legacy_alter_table_on"])
        try:
            with conn:
                conn.execute(_SQL["migrate_ingest_log_rename_old"])
                conn.execute(
                    _SQL["schema_ingest_log"].replace(
                        "CREATE TABLE IF NOT EXISTS ingest_log", "CREATE TABLE ingest_log"
                    )
                )
                conn.execute(_SQL["migrate_ingest_log_copy_from_old"])
                conn.execute(_SQL["migrate_ingest_log_drop_old"])
        finally:
            conn.execute(_SQL["pragma_legacy_alter_table_off"])
            conn.execute(_SQL["pragma_foreign_keys_on"])

    # ---------------------------------------------------------------------------
    # Insert helpers
    # ---------------------------------------------------------------------------

    @staticmethod
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
        """Insert one encounter row. The column ↔ field mapping (incl. the
        ``encid → act_encid`` rename and the datetime → unix conversion) lives
        on :meth:`Encounter.as_db_params`; this function just threads in the
        per-call args and runs the SQL."""
        cur = conn.execute(
            _SQL["insert_encounter"],
            enc.as_db_params(
                world=world,
                source_dsn=source_dsn,
                ingested_at=ingested_at,
                uploaded_by=uploaded_by,
                guild_name=guild_name,
            ),
        )
        return int(cur.lastrowid or 0)

    @staticmethod
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
                _SQL["insert_combatant"],
                c.as_db_params(encounter_id=encounter_id, snapshot=snap),
            )
            name_to_id[c.name] = int(cur.lastrowid or 0)
        return name_to_id

    @staticmethod
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
                    _SQL["update_combatant_snapshot"],
                    (snap.level, snap.guild_name, snap.cls, snap.ilvl, encounter_id, name),
                )
                n += cur.rowcount
        return n

    @staticmethod
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
            _SQL["update_combatant_is_player"],
            [(1 if v else 0, k) for k, v in classification.items()],
        )

    @staticmethod
    def invalidate_is_player_cache_with_conn(conn: sqlite3.Connection) -> None:
        """Mark every combatant row for lazy re-classification on next read.
        Variant that accepts an existing connection (used by tests + by the
        rankings cache-invalidation hook to share the parses.db connection)."""
        conn.execute(_SQL["invalidate_is_player_cache"])

    def invalidate_is_player_cache(self) -> None:
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
        with sqlite3.connect(self.path) as conn:
            self.invalidate_is_player_cache_with_conn(conn)

    @staticmethod
    def insert_damage_types_bulk(
        conn: sqlite3.Connection,
        combatant_name_to_id: dict[str, int],
        damage_types: list[DamageType],
    ) -> int:
        """Bulk-insert damage_types rows. ``combatant_name_to_id`` resolves the
        natural-key reference in :class:`DamageType` to the FK we store; rows
        referencing an unknown combatant name are silently dropped."""
        rows = [
            dt.as_db_params(combatant_id=combatant_name_to_id[dt.combatant_name])
            for dt in damage_types
            if dt.combatant_name in combatant_name_to_id
        ]
        conn.executemany(_SQL["insert_damage_type"], rows)
        return len(rows)

    @staticmethod
    def insert_attack_types_bulk(
        conn: sqlite3.Connection,
        combatant_name_to_id: dict[str, int],
        attack_types: list[AttackType],
    ) -> int:
        """Bulk-insert attack_types rows. Same shape as
        :func:`insert_damage_types_bulk` — rows whose combatant_name can't be
        resolved against the encounter's combatants are silently dropped."""
        rows = [
            at.as_db_params(combatant_id=combatant_name_to_id[at.combatant_name])
            for at in attack_types
            if at.combatant_name in combatant_name_to_id
        ]
        conn.executemany(_SQL["insert_attack_type"], rows)
        return len(rows)

    @staticmethod
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
            _SQL["mark_ingested"],
            (world, act_encid, encounter_id, ingested_at, source_dsn),
        )

    # ---------------------------------------------------------------------------
    # Lookup helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def is_ingested(conn: sqlite3.Connection, act_encid: str, world: str = "Varsoon") -> bool:
        row = conn.execute(
            _SQL["check_is_ingested"],
            (world, act_encid),
        ).fetchone()
        return row is not None

    @staticmethod
    def find_encounter_by_act_encid(conn: sqlite3.Connection, act_encid: str, world: str = "Varsoon") -> dict | None:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            _SQL["find_encounter_by_act_encid"],
            (world, act_encid),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def recent_encounters(
        conn: sqlite3.Connection,
        limit: int = 20,
        zone: str | None = None,
        world: str = "Varsoon",
    ) -> list[dict]:
        conn.row_factory = sqlite3.Row
        if zone:
            rows = conn.execute(
                _SQL["recent_encounters_by_zone"],
                (world, zone, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                _SQL["recent_encounters_all"],
                (world, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_encounters_for_admin(
        conn: sqlite3.Connection,
        *,
        search: str | None = None,
        limit: int = 200,
        world: str | None = None,
        before: int | None = None,
    ) -> list[dict]:
        """All encounters INCLUDING hidden (soft-deleted) ones, newest first, for
        the admin sanitize view. Optional case-insensitive search over
        title / uploaded_by / guild_name. Includes a player_count and the hidden_at
        marker so an admin can spot a bogus parse even when it's hidden but still
        polluting the leaderboards.

        ``world`` scopes to a single EQ2 server; ``None`` returns all worlds
        (no longer recommended — pass the active server world in all call sites).
        ``before`` is the pagination cursor: only rows strictly older than that
        unix timestamp (pass the previous page's last started_at)."""
        conn.row_factory = sqlite3.Row
        clauses: list[str] = []
        params: list = []
        if world is not None:
            clauses.append("e.world = ?")
            params.append(world)
        if before is not None:
            clauses.append("e.started_at < ?")
            params.append(before)
        if search:
            like = f"%{search.lower()}%"
            clauses.append(
                "(LOWER(title) LIKE ? OR LOWER(IFNULL(uploaded_by, '')) LIKE ? OR LOWER(IFNULL(guild_name, '')) LIKE ?)"
            )
            params += [like, like, like]
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = _SQL["list_encounters_for_admin"].format(where=where)
        return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]

    @staticmethod
    def delete_encounter(conn: sqlite3.Connection, encounter_id: int) -> bool:
        """Delete one encounter. Returns True if a row was removed, False if not
        found. ON DELETE CASCADE handles combatants / damage_types / attack_types
        / ingest_log."""
        with conn:
            cur = conn.execute(_SQL["delete_encounter"], (encounter_id,))
        return cur.rowcount > 0

    @staticmethod
    def soft_delete_encounter(conn: sqlite3.Connection, encounter_id: int, hidden_at: int) -> bool:
        """Hide an encounter from the parses list without removing it, so any
        leaderboard entry sourced from it survives and its link still opens.
        Only acts on a currently-visible row; returns True if it flipped one."""
        with conn:
            cur = conn.execute(
                _SQL["soft_delete_encounter"],
                (hidden_at, encounter_id),
            )
        return cur.rowcount > 0

    @staticmethod
    def unhide_encounter(conn: sqlite3.Connection, encounter_id: int) -> bool:
        """Clear a soft-delete marker so a previously-hidden parse becomes visible
        again (used when its encounter is re-uploaded). Returns True if a hidden
        row was un-hidden."""
        with conn:
            cur = conn.execute(
                _SQL["unhide_encounter"],
                (encounter_id,),
            )
        return cur.rowcount > 0

    @staticmethod
    def set_encounter_guild_name(conn: sqlite3.Connection, encounter_id: int, guild_name: str | None) -> bool:
        """Set (or clear) the guild_name on an encounter row. Returns True if the row was updated."""
        with conn:
            cur = conn.execute(
                _SQL["set_encounter_guild_name"],
                (guild_name, encounter_id),
            )
        return cur.rowcount > 0

    @staticmethod
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
        sql = _SQL["find_encounters_by_filter"].format(where=("WHERE " + " AND ".join(clauses)))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    @staticmethod
    def get_combatants_for_encounter(conn: sqlite3.Connection, encounter_id: int) -> list[dict]:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _SQL["get_combatants_for_encounter"],
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_top_attacks_for_combatant(
        conn: sqlite3.Connection,
        combatant_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """Top damage abilities (excludes heals and the swing_type=100 rollup)."""
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(_DAMAGE_SWING_TYPES))
        rows = conn.execute(
            _SQL["get_top_attacks_by_swing_type"].format(placeholders=placeholders),
            (combatant_id, *_DAMAGE_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
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
            _SQL["get_top_attacks_by_swing_type"].format(placeholders=placeholders),
            (combatant_id, *_HEAL_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
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
            _SQL["get_top_cures"].format(placeholders=placeholders),
            (combatant_id, *_CURE_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
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
            _SQL["get_top_threats"].format(placeholders=placeholders),
            (combatant_id, *_THREAT_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_damage_types_for_combatant(
        conn: sqlite3.Connection,
        combatant_id: int,
    ) -> list[dict]:
        """All damage_types rows for a combatant, sorted by damage DESC."""
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _SQL["get_damage_types_for_combatant"],
            (combatant_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------------------
    # client_warnings (soft warnings on otherwise-successful uploads)
    # ---------------------------------------------------------------------------

    @staticmethod
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
            _SQL["set_encounter_client_warnings"],
            (payload, encounter_id),
        )

    # ---------------------------------------------------------------------------
    # tamper_reports (audit channel for blocked-from-leaderboard uploads)
    # ---------------------------------------------------------------------------

    @staticmethod
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
            _SQL["insert_tamper_report"],
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

    @staticmethod
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
        sql = _SQL["list_tamper_reports"].format(where=where)
        return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]

    @staticmethod
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
                _SQL["acknowledge_tamper_report"],
                (acknowledged_at, acknowledged_by, report_id),
            )
        return cur.rowcount > 0

    @staticmethod
    def acknowledge_tamper_reports(
        conn: sqlite3.Connection,
        report_ids: list[int],
        *,
        acknowledged_at: int,
        acknowledged_by: str,
    ) -> int:
        """Bulk one-way acknowledge. Only pending rows flip (already-ack'd
        and unknown ids are silently skipped); returns the flipped count."""
        if not report_ids:
            return 0
        placeholders = ",".join("?" * len(report_ids))
        with conn:
            cur = conn.execute(
                _SQL["acknowledge_tamper_reports_bulk"].format(placeholders=placeholders),
                [acknowledged_at, acknowledged_by, *report_ids],
            )
        return cur.rowcount

    @staticmethod
    def count_pending_tamper_reports(
        conn: sqlite3.Connection,
        world: str | None = None,
    ) -> int:
        """Cheap count of unack'd reports — used by the admin panel badge so
        the maintainer can see at a glance whether anything new needs review."""
        if world is None:
            row = conn.execute(_SQL["count_pending_tamper_reports"]).fetchone()
        else:
            row = conn.execute(
                _SQL["count_pending_tamper_reports_for_world"],
                (world,),
            ).fetchone()
        return int(row[0]) if row else 0


# The shared default instance — every runtime consumer goes through this.
store = ParsesStore()
