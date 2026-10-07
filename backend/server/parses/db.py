"""
Normalized Postgres store (``parses`` schema) for ingested ACT parses.

All behaviour lives on :class:`ParsesStore` (the catalogue convention —
see backend/db_catalogue.py): ``store.init_db()`` returns a pooled
connection scoped to the family schema (PgConnProxy — ``close()`` returns
it to the pool); the conn-taking insert/lookup helpers are staticmethods —
callers batch several operations per connection. Schema DDL is owned by
db/migrations/0002_parses.sql.

Schema reflects the real columns ACT exports at AttackType depth — the
plugin's PayloadBuilder (in the EQ2LexiconACTPlugin repo) is the upstream
source-of-truth for column-name mappings on the wire.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from backend.db_catalogue import PgCatalogue
from backend.server.parses.models import AttackType, Combatant, CombatantSnapshot, DamageType, Encounter
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

# Reused for combatants with no resolved identity snapshot — stores NULLs.
_EMPTY_SNAPSHOT = CombatantSnapshot()

#: Postgres schema the parses family lives in (db/migrations/0002_parses.sql).
SCHEMA = "parses"


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


_DAMAGE_SWING_TYPES = [int(SwingType.MELEE), int(SwingType.NONMELEE)]
_HEAL_SWING_TYPES = [int(SwingType.HEAL)]
_CURE_SWING_TYPES = [int(SwingType.CURE)]
_THREAT_SWING_TYPES = [int(SwingType.PROC)]  # callers should additionally filter type != 'All'


class ParsesStore(PgCatalogue):
    """Read/write access to the parses schema (uploaded ACT encounters).

    The catalogue convention (see backend/db_catalogue.py): the shared
    module-level ``store`` instance is the runtime entry point (consumers
    alias it ``parses_db``); the conn-taking helpers are staticmethods —
    callers batch several operations per connection/transaction. Tests
    construct ``ParsesStore(scratch_schema)`` or re-point ``store.schema``.
    """

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    # ---------------------------------------------------------------------------
    # Insert helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def insert_encounter(
        conn: Any,
        enc: Encounter,
        *,
        source_dsn: str,
        ingested_at: int,
        uploaded_by: str = "local",
        guild_name: str | None = None,
        world: str = "Varsoon",
        uploader_verified: bool = True,
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
                uploader_verified=uploader_verified,
            ),
        )
        row = cur.fetchone()
        return int(row["id"]) if row else 0

    @staticmethod
    def insert_combatants_bulk(
        conn: Any,
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
            row = cur.fetchone()
            name_to_id[c.name] = int(row["id"]) if row else 0
        return name_to_id

    @staticmethod
    def update_combatant_snapshots(
        conn: Any,
        encounter_id: int,
        snapshots: dict[str, CombatantSnapshot],
    ) -> int:
        """Fill in level/guild/class on already-inserted combatant rows once the
        (possibly slow) Census resolution finishes in the background. Matches by
        combatant name within the encounter. Returns rows updated."""
        if not snapshots:
            return 0
        n = 0
        for name, snap in snapshots.items():
            cur = conn.execute(
                _SQL["update_combatant_snapshot"],
                (snap.level, snap.guild_name, snap.cls, snap.ilvl, encounter_id, name),
            )
            n += cur.rowcount
        conn.commit()
        return n

    @staticmethod
    def update_combatant_is_player(conn: Any, classification: dict[int, bool]) -> None:
        """Bulk UPDATE the per-combatant is_player flag.

        Called from:
          * the ingest path, after the classifier runs against newly-inserted rows
          * the async snapshot fill, after cls fills in (which can flip stage 5)
          * the lazy-backfill helper in web/routes/parses/list.py

        No-op when ``classification`` is empty. Caller owns the connection
        and transaction scope."""
        if not classification:
            return
        cur = conn.cursor()
        cur.executemany(
            _SQL["update_combatant_is_player"],
            [(1 if v else 0, k) for k, v in classification.items()],
        )

    @staticmethod
    def invalidate_is_player_cache_with_conn(conn: Any) -> None:
        """Mark every combatant row for lazy re-classification on next read.
        Variant that accepts an existing connection (used by tests + by the
        rankings cache-invalidation hook to share the parses connection)."""
        conn.execute(_SQL["invalidate_is_player_cache"])

    def invalidate_is_player_cache(self) -> None:
        """Mark every combatant row for lazy re-classification on next read.
        Production caller (checks out its own connection).

        Called by web/routes/rankings.py:invalidate_zones_cache so that a
        curator zone-edit propagates to the existing parses without a
        separate backfill — the next read of each encounter re-classifies
        against the updated zone trees."""
        conn = self.init_db()
        try:
            self.invalidate_is_player_cache_with_conn(conn)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def insert_damage_types_bulk(
        conn: Any,
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
        cur = conn.cursor()
        cur.executemany(_SQL["insert_damage_type"], rows)
        return len(rows)

    @staticmethod
    def insert_attack_types_bulk(
        conn: Any,
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
        cur = conn.cursor()
        cur.executemany(_SQL["insert_attack_type"], rows)
        return len(rows)

    @staticmethod
    def mark_ingested(
        conn: Any,
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
    def is_ingested(conn: Any, act_encid: str, world: str = "Varsoon") -> bool:
        row = conn.execute(
            _SQL["check_is_ingested"],
            (world, act_encid),
        ).fetchone()
        return row is not None

    @staticmethod
    def find_encounter_by_act_encid(conn: Any, act_encid: str, world: str = "Varsoon") -> dict | None:
        row = conn.execute(
            _SQL["find_encounter_by_act_encid"],
            (world, act_encid),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def recent_encounters(
        conn: Any,
        limit: int = 20,
        zone: str | None = None,
        world: str = "Varsoon",
    ) -> list[dict]:
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
        conn: Any,
        *,
        search: str | None = None,
        limit: int = 200,
        world: str | None = None,
        before: int | None = None,
        hidden_only: bool = False,
    ) -> list[dict]:
        """All encounters INCLUDING hidden (soft-deleted) ones, newest first, for
        the admin sanitize view. Optional case-insensitive search over
        title / uploaded_by / guild_name. Includes a player_count and the hidden_at
        marker so an admin can spot a bogus parse even when it's hidden but still
        polluting the leaderboards.

        ``world`` scopes to a single EQ2 server; ``None`` returns all worlds
        (no longer recommended — pass the active server world in all call sites).
        ``before`` is the pagination cursor: only rows strictly older than that
        unix timestamp (pass the previous page's last started_at).
        ``hidden_only`` narrows to soft-deleted rows — the restore workflow
        after a mistaken guild-wide delete."""
        clauses: list[str] = []
        params: list = []
        if world is not None:
            clauses.append("e.world = %s")
            params.append(world)
        if hidden_only:
            clauses.append("e.hidden_at IS NOT NULL")
        if before is not None:
            clauses.append("e.started_at < %s")
            params.append(before)
        if search:
            like = f"%{search.lower()}%"
            # A numeric search (optionally "#123" — how ids render in the UI)
            # also matches the encounter id exactly, so an admin can jump from
            # a parse URL / rankings row straight to the row to sanitize.
            id_term = search.strip().lstrip("#")
            if id_term.isdigit():
                clauses.append(
                    "(e.id = %s OR LOWER(title) LIKE %s OR LOWER(COALESCE(uploaded_by, '')) LIKE %s"
                    " OR LOWER(COALESCE(guild_name, '')) LIKE %s)"
                )
                params += [int(id_term), like, like, like]
            else:
                clauses.append(
                    "(LOWER(title) LIKE %s OR LOWER(COALESCE(uploaded_by, '')) LIKE %s"
                    " OR LOWER(COALESCE(guild_name, '')) LIKE %s)"
                )
                params += [like, like, like]
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = _SQL["list_encounters_for_admin"].format(where=where)
        return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]

    @staticmethod
    def delete_encounter(conn: Any, encounter_id: int) -> bool:
        """Delete one encounter. Returns True if a row was removed, False if not
        found. ON DELETE CASCADE handles combatants / damage_types / attack_types
        / ingest_log."""
        cur = conn.execute(_SQL["delete_encounter"], (encounter_id,))
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def soft_delete_encounter(conn: Any, encounter_id: int, hidden_at: int, hidden_by: str | None = None) -> bool:
        """Hide an encounter from the parses list without removing it, so any
        leaderboard entry sourced from it survives and its link still opens.
        ``hidden_by`` records the actor's discord id for the admin view.
        Only acts on a currently-visible row; returns True if it flipped one."""
        cur = conn.execute(
            _SQL["soft_delete_encounter"],
            (hidden_at, hidden_by, encounter_id),
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def unhide_encounter(conn: Any, encounter_id: int) -> bool:
        """Clear a soft-delete marker so a previously-hidden parse becomes visible
        again (used when its encounter is re-uploaded). Returns True if a hidden
        row was un-hidden."""
        cur = conn.execute(
            _SQL["unhide_encounter"],
            (encounter_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def set_encounter_guild_name(conn: Any, encounter_id: int, guild_name: str | None) -> bool:
        """Set (or clear) the guild_name on an encounter row. Returns True if the row was updated."""
        cur = conn.execute(
            _SQL["set_encounter_guild_name"],
            (guild_name, encounter_id),
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def get_combatants_for_encounter(conn: Any, encounter_id: int) -> list[dict]:
        rows = conn.execute(
            _SQL["get_combatants_for_encounter"],
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_combatants_for_encounters(conn: Any, encounter_ids: list[int]) -> dict[int, list[dict]]:
        """Batched :meth:`get_combatants_for_encounter` — ONE ``= ANY`` query
        (the SQLite 500-id IN-list chunking is gone). Rows keep the
        per-encounter damage-DESC order."""
        out: dict[int, list[dict]] = {eid: [] for eid in encounter_ids}
        if not encounter_ids:
            return out
        rows = conn.execute(_SQL["get_combatants_for_encounters"], (list(encounter_ids),)).fetchall()
        for r in rows:
            out[r["encounter_id"]].append(dict(r))
        return out

    @staticmethod
    def get_top_attacks_for_combatant(
        conn: Any,
        combatant_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """Top damage abilities (excludes heals and the swing_type=100 rollup)."""
        rows = conn.execute(
            _SQL["get_top_attacks_by_swing_type"],
            (combatant_id, _DAMAGE_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_top_heals_for_combatant(
        conn: Any,
        combatant_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """Top heal abilities (swing_type=3). `damage` column = amount healed;
        `resist` column distinguishes regular 'Hitpoints' heals from
        'Absorption' wards."""
        rows = conn.execute(
            _SQL["get_top_attacks_by_swing_type"],
            (combatant_id, _HEAL_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_top_cures_for_combatant(
        conn: Any,
        combatant_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """Cure events (swing_type=20). The `damage` column is the count of
        detrimental effects removed; `hits` is how many times the cure was cast."""
        rows = conn.execute(
            _SQL["get_top_cures"],
            (combatant_id, _CURE_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_top_threats_for_combatant(
        conn: Any,
        combatant_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """Threat / buff-proc rows (swing_type=100, type != 'All'). For threat
        procs the `damage` column is the threat-increase value; `hits` is
        proc count."""
        rows = conn.execute(
            _SQL["get_top_threats"],
            (combatant_id, _THREAT_SWING_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_damage_types_for_combatant(
        conn: Any,
        combatant_id: int,
    ) -> list[dict]:
        """All damage_types rows for a combatant, sorted by damage DESC."""
        rows = conn.execute(
            _SQL["get_damage_types_for_combatant"],
            (combatant_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------------------
    # Tiered detail retention (cleanup sweep)
    # ---------------------------------------------------------------------------

    @staticmethod
    def select_detail_prune_candidates(conn: Any, *, older_than: int, limit: int = 500) -> list[dict]:
        """Encounters older than ``older_than`` whose breakdown rows are still
        present (detail_pruned_at IS NULL), oldest first. The sweep classifies
        each row's zone into its retention tier and prunes the overdue subset."""
        rows = conn.execute(_SQL["select_detail_prune_candidates"], (older_than, limit)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def prune_encounter_detail(conn: Any, encounter_ids: list[int], *, pruned_at: int) -> None:
        """Drop attack_types + damage_types for the given encounters and stamp
        detail_pruned_at (encounters + combatants stay forever). One
        transaction; caller commits."""
        if not encounter_ids:
            return
        ids = list(encounter_ids)
        conn.execute(_SQL["prune_detail_attack_types"], (ids,))
        conn.execute(_SQL["prune_detail_damage_types"], (ids,))
        conn.execute(_SQL["mark_detail_pruned"], (pruned_at, ids))

    # ---------------------------------------------------------------------------
    # client_warnings (soft warnings on otherwise-successful uploads)
    # ---------------------------------------------------------------------------

    @staticmethod
    def set_encounter_client_warnings(
        conn: Any,
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
        conn: Any,
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
        row = cur.fetchone()
        return int(row["id"]) if row else 0

    @staticmethod
    def list_tamper_reports(
        conn: Any,
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
            clauses.append("world = %s")
            params.append(world)
        if reason is not None:
            clauses.append("reason = %s")
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
        sql = _SQL["list_tamper_reports"].format(where=where)
        return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]

    @staticmethod
    def acknowledge_tamper_report(
        conn: Any,
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
        cur = conn.execute(
            _SQL["acknowledge_tamper_report"],
            (acknowledged_at, acknowledged_by, report_id),
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def acknowledge_tamper_reports(
        conn: Any,
        report_ids: list[int],
        *,
        acknowledged_at: int,
        acknowledged_by: str,
    ) -> int:
        """Bulk one-way acknowledge. Only pending rows flip (already-ack'd
        and unknown ids are silently skipped); returns the flipped count."""
        if not report_ids:
            return 0
        cur = conn.execute(
            _SQL["acknowledge_tamper_reports_bulk"],
            (acknowledged_at, acknowledged_by, list(report_ids)),
        )
        conn.commit()
        return cur.rowcount

    @staticmethod
    def acknowledge_all_pending_tamper_reports(
        conn: Any,
        world: str,
        *,
        acknowledged_at: int,
        acknowledged_by: str,
    ) -> int:
        """Ack every pending report for a world in one statement — the spam
        cleanup path (a single hammering uploader can create far more rows
        than the 500-id batch endpoint can clear). Returns the flipped count."""
        cur = conn.execute(
            _SQL["acknowledge_all_pending_tamper_reports"],
            (acknowledged_at, acknowledged_by, world),
        )
        conn.commit()
        return cur.rowcount

    @staticmethod
    def delete_acknowledged_tamper_reports(conn: Any, world: str) -> int:
        """Hard-delete already-acknowledged reports for a world to reclaim
        space after a spam flood. Reviewed rows only — pending ones are never
        touched. Returns the deleted count."""
        cur = conn.execute(_SQL["delete_acknowledged_tamper_reports"], (world,))
        conn.commit()
        return cur.rowcount

    @staticmethod
    def count_pending_tamper_reports(
        conn: Any,
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
        return int(row["n"]) if row else 0


# The shared default instance — every runtime consumer goes through this.
store = ParsesStore()
