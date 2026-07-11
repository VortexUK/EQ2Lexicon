"""
Local SQLite catalogue of EverQuest 2 raid strategies.

Companion to ``backend/eq2db/zones.py`` — zones.db is read-only reference
data rebuilt from JSON; this DB accumulates strategy content sourced
initially from the EQ2 wiki (EQ2i / Fandom) and then progressively
hand-edited by guild officers.

Scope (deliberate): Vanilla through Rise of Kunark only. Picked to
align with the TLE-server content cycle. Live-expansion strategies are
out of scope for the moment.

Schema (six tables):

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
  * **act_triggers** / **act_spell_timers** — per-encounter ACT trigger
                                and spell-timer rows (the /act editor).
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

All behaviour lives on :class:`RaidCatalogue` (the eq2db data-interface
convention — see AACatalogue / SpellCatalogue): path-based reads are
instance methods; the conn-taking write helpers (upserts, mirrors, ACT
trigger/timer writes) are staticmethods on the same class so consumers
import ONE name — the shared ``catalogue`` instance. The former
``raids_act`` re-export layer is folded into the class.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from backend.db_catalogue import BaseCatalogue
from backend.db_helpers import resolve_db_path
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DB_PATH: Path = resolve_db_path("DB_RAIDS_PATH", "raids", "raids.db")


# Source provenance tokens for the `source` columns. Centralised so
# typos don't silently produce a third category.
SOURCE_SCRAPE = "eq2i_scrape"
SOURCE_MANUAL = "manual"
SOURCE_PARSE = "parse_data"

VALID_SOURCES: frozenset[str] = frozenset({SOURCE_SCRAPE, SOURCE_MANUAL, SOURCE_PARSE})


# Schema (CREATE TABLE / INDEX) lives in raids.sql; init_db runs each block.

# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Column lists
# ---------------------------------------------------------------------------

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


class RaidCatalogue(BaseCatalogue):
    """Read (and build) access to one raids.db file.

    The eq2db data-interface convention (see AACatalogue / SpellCatalogue):
    the DB path lives on the instance; the shared module-level ``catalogue``
    is the runtime entry point, and tests construct ``RaidCatalogue(tmp_db)``.
    Path-based reads are instance methods; write helpers take an open conn
    (callers batch several writes per transaction) and are staticmethods.

    The provenance tokens are mirrored as class attributes so consumers
    holding the catalogue can write ``raids_db.SOURCE_MANUAL``.
    """

    SOURCE_SCRAPE = SOURCE_SCRAPE
    SOURCE_MANUAL = SOURCE_MANUAL
    SOURCE_PARSE = SOURCE_PARSE
    VALID_SOURCES = VALID_SOURCES

    # ON DELETE CASCADE on revisions/encounters only fires with the
    # per-connection FK pragma.
    FOREIGN_KEYS = True

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_raid_zones"])
        conn.execute(_SQL["schema_raid_zone_revisions"])
        conn.execute(_SQL["schema_raid_encounters"])
        conn.execute(_SQL["schema_raid_encounter_revisions"])
        conn.execute(_SQL["schema_act_triggers"])
        conn.execute(_SQL["schema_act_spell_timers"])
        conn.executescript(_SQL["indexes_all"])

    # ── Write helpers (take an open conn — callers own the transaction) ──────

    @staticmethod
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

        existing = conn.execute(_SQL["select_zone_by_name"], (zone_name,)).fetchone()

        if existing and source == SOURCE_SCRAPE and existing[1] == SOURCE_MANUAL:
            # Re-scrape against a human-edited row: refresh the wiki-owned
            # metadata but leave the markdown blobs + source flag alone. The
            # revision history (encounters only) doesn't apply at the zone
            # level for now; future raid_zone_revisions table is the right
            # home for tracking these.
            conn.execute(
                _SQL["update_zone_wiki_fields"],
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

        # COALESCE on every nullable column so a caller that passes a column as
        # None means "don't touch", not "clobber to NULL". The historical default
        # (excluded.col) clobbered existing data — e.g. _write_strategy_sync calls
        # upsert_raid_zone(... source=MANUAL) to auto-create the zone parent when
        # a curator edits a boss strategy, passing overview_md=None (default).
        # On ON CONFLICT that nulled the curator's existing overview_md. Reported
        # by user "I am STILL losing raid zone overviews" — every encounter-
        # strategy edit silently wiped the zone overview.
        # If a caller genuinely wants to clear a column, they should use a
        # targeted UPDATE (see _update_overview_sync) — that's the right code
        # path for destructive writes.
        conn.execute(
            _SQL["upsert_zone"],
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
        row = conn.execute(_SQL["select_zone_id_by_name"], (zone_name,)).fetchone()
        return int(row[0])

    @staticmethod
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
            _SQL["select_encounter_by_zone_mob"],
            (raid_zone_id, mob_name.lower()),
        ).fetchone()

        if existing is None:
            cur = conn.execute(
                _SQL["insert_encounter"],
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
                    _SQL["insert_encounter_revision"],
                    (new_id, now, actor, None, strategy_md, edit_note or "initial scrape"),
                )
            conn.commit()
            return new_id

        enc_id, prev_md = int(existing[0]), existing[1]
        # On re-scrape: only update fields that the scraper authoritatively
        # owns (wiki_url, position, last_synced_at). Don't clobber a
        # human-edited strategy_md with a fresh scrape — that's what
        # SOURCE_MANUAL exists to protect.
        if source == SOURCE_SCRAPE:
            current_source = conn.execute(_SQL["select_encounter_source"], (enc_id,)).fetchone()[0]
            if current_source == SOURCE_MANUAL:
                # Refresh sync timestamp + url/position only, leave strategy alone.
                conn.execute(
                    _SQL["update_encounter_url_position_synced"],
                    (wiki_url, position, now, enc_id),
                )
                conn.commit()
                return enc_id

        # Strategy actually changing? Record a revision before the update.
        if strategy_md is not None and strategy_md != prev_md:
            conn.execute(
                _SQL["insert_encounter_revision"],
                (enc_id, now, actor, prev_md, strategy_md, edit_note),
            )

        conn.execute(
            _SQL["update_encounter"],
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

    # ── zones_db mirror helpers (no self-commit — callers commit) ────────────

    @staticmethod
    def rename_raid_encounter_if_exists(
        conn: sqlite3.Connection,
        *,
        zone_name: str,
        old_mob_name: str,
        new_mob_name: str,
    ) -> bool:
        """If a raid_encounters row matches (zone_name, old_mob_name) case-insensitively,
        rename its mob_name + mob_name_lower and bump last_edited_at. No-op
        otherwise. Returns True if a row was updated."""
        cur = conn.execute(
            _SQL["rename_encounter_by_zone_mob"],
            (new_mob_name, new_mob_name.lower(), zone_name.lower(), old_mob_name.lower()),
        )
        return cur.rowcount > 0

    @staticmethod
    def update_raid_encounter_if_exists(
        conn: sqlite3.Connection,
        *,
        zone_name: str,
        mob_name: str,
        position: int,
    ) -> bool:
        """Update only the position on the raid_encounters row found by
        (zone_name, mob_name) — used by reorder to mirror the new position.
        No-op if no matching row. Returns True if updated.

        (Renames are a separate operation; this helper deliberately takes only
        `position` so the rename and reorder mirrors stay distinct call sites.)"""
        cur = conn.execute(
            _SQL["update_encounter_position_by_zone_mob"],
            (position, zone_name.lower(), mob_name.lower()),
        )
        return cur.rowcount > 0

    @staticmethod
    def delete_raid_encounter_by_zone_mob(conn: sqlite3.Connection, *, zone_name: str, mob_name: str) -> bool:
        """Delete a raid_encounters row by its (zone_name, mob_name) lookup.
        CASCADEs to triggers, spell timers, strategy revisions via the FK.
        Returns True if a row was deleted."""
        cur = conn.execute(
            _SQL["delete_encounter_by_zone_mob"],
            (zone_name.lower(), mob_name.lower()),
        )
        return cur.rowcount > 0

    # ── Read helpers (path-based) ────────────────────────────────────────────

    def encounter_revisions(self, encounter_id: int) -> list[dict]:
        """Full revision history for an encounter, newest first."""
        return [dict(r) for r in self._fetchall(_SQL["list_encounter_revisions"], (encounter_id,))]

    def list_zone_revisions(self, zone_id: int) -> list[dict]:
        """All revision rows for a zone's overview, newest first.
        Each row: {id, edited_at, edited_by, before_md, after_md, edit_note}."""
        return [dict(r) for r in self._fetchall(_SQL["list_zone_revisions"], (zone_id,))]

    # ── ACT trigger helpers (formerly backend/eq2db/raids_act.py) ────────────

    def list_act_triggers_for_encounter(self, encounter_id: int) -> list[dict]:
        """Every ACT trigger row for an encounter, ordered by position then id."""
        rows = self._fetchall(
            f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers WHERE raid_encounter_id = ? ORDER BY position, id",
            (encounter_id,),
        )
        return [dict(r) for r in rows]

    def get_act_trigger(self, trigger_id: int) -> dict | None:
        row = self._fetchone(f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers WHERE id = ?", (trigger_id,))
        return dict(row) if row else None

    @staticmethod
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

    @staticmethod
    def delete_act_trigger(conn: sqlite3.Connection, trigger_id: int) -> bool:
        """Delete a trigger by id. Returns True if a row was removed."""
        cur = conn.execute("DELETE FROM act_triggers WHERE id = ?", (trigger_id,))
        conn.commit()
        return cur.rowcount > 0

    # ── ACT spell-timer helpers (formerly backend/eq2db/raids_act.py) ────────

    def list_act_spell_timers_for_encounter(self, encounter_id: int) -> list[dict]:
        """Every spell-timer row for an encounter, alphabetical by name."""
        rows = self._fetchall(
            f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers WHERE raid_encounter_id = ? ORDER BY name",
            (encounter_id,),
        )
        return [dict(r) for r in rows]

    def get_act_spell_timer(self, timer_id: int) -> dict | None:
        row = self._fetchone(f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers WHERE id = ?", (timer_id,))
        return dict(row) if row else None

    @staticmethod
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

    @staticmethod
    def delete_act_spell_timer(conn: sqlite3.Connection, timer_id: int) -> bool:
        """Delete a spell-timer by id. Returns True if a row was removed."""
        cur = conn.execute("DELETE FROM act_spell_timers WHERE id = ?", (timer_id,))
        conn.commit()
        return cur.rowcount > 0


# The shared default instance — every runtime consumer goes through this.
catalogue = RaidCatalogue()
