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

import sqlite3
import time
from pathlib import Path

from backend.db_helpers import resolve_db_path
from backend.eq2db import _meta as _meta_db
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


# ---------------------------------------------------------------------------
# Schema (CREATE TABLE / INDEX) lives in raids.sql; init_db runs each block.

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
    _meta_db.create_table(conn)
    conn.execute(_SQL["schema_raid_zones"])
    conn.execute(_SQL["schema_raid_zone_revisions"])
    conn.execute(_SQL["schema_raid_encounters"])
    conn.execute(_SQL["schema_raid_encounter_revisions"])
    conn.execute(_SQL["schema_act_triggers"])
    conn.execute(_SQL["schema_act_spell_timers"])
    conn.executescript(_SQL["indexes_all"])
    conn.commit()
    return conn


# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401

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


# ---------------------------------------------------------------------------
# zones_db mirror helpers
# ---------------------------------------------------------------------------
# These helpers operate on a connection passed in (consistent with the
# existing module style); they do NOT commit themselves — callers commit.


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


def delete_raid_encounter_by_zone_mob(conn: sqlite3.Connection, *, zone_name: str, mob_name: str) -> bool:
    """Delete a raid_encounters row by its (zone_name, mob_name) lookup.
    CASCADEs to triggers, spell timers, strategy revisions via the FK.
    Returns True if a row was deleted."""
    cur = conn.execute(
        _SQL["delete_encounter_by_zone_mob"],
        (zone_name.lower(), mob_name.lower()),
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Read helpers (for the future web routes + the smoke tests)
# ---------------------------------------------------------------------------


_ZONE_SELECT_COLS = _SQL["select_zone_cols"]
_ENC_SELECT_COLS = _SQL["select_encounter_cols"]


def find_zone_by_name(name: str, path: Path = DB_PATH) -> dict | None:
    """Look up a raid zone by name (case-insensitive)."""
    if not path.exists() or not name:
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            _SQL["find_zone_by_name_ci"].format(cols=_ZONE_SELECT_COLS),
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
            _SQL["list_encounters_for_zone"].format(cols=_ENC_SELECT_COLS),
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
            _SQL["list_zones_by_expansion"].format(cols=_ZONE_SELECT_COLS),
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
            _SQL["list_encounter_revisions"],
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_zone_revisions(zone_id: int, path: Path = DB_PATH) -> list[dict]:
    """All revision rows for a zone's overview, newest first.
    Each row: {id, edited_at, edited_by, before_md, after_md, edit_note}."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _SQL["list_zone_revisions"],
            (zone_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def stats(path: Path = DB_PATH) -> dict:
    """Diagnostic — counts by table + source."""
    if not path.exists():
        return {}
    with sqlite3.connect(path) as conn:
        out = {
            "zones": conn.execute(_SQL["stats_zones_count"]).fetchone()[0],
            "encounters": conn.execute(_SQL["stats_encounters_count"]).fetchone()[0],
            "revisions": conn.execute(_SQL["stats_revisions_count"]).fetchone()[0],
            "encounters_by_source": dict(conn.execute(_SQL["stats_encounters_by_source"])),
            "zones_by_expansion": dict(conn.execute(_SQL["stats_zones_by_expansion"])),
        }
        return out


# ---------------------------------------------------------------------------
# ACT Triggers + Spell Timers — re-exported from census.raids_act_db
# ---------------------------------------------------------------------------
#
# The ACT trigger + spell-timer helpers have been extracted to
# census/raids_act_db.py (BE-055). They are re-exported here so that all
# existing callers (``from census import raids_db; raids_db.X``) continue
# to work without changes during Phase 2c migration.

from backend.eq2db.raids_act import (  # noqa: E402,F401
    delete_act_spell_timer,
    delete_act_trigger,
    get_act_spell_timer,
    get_act_trigger,
    list_act_spell_timers_for_encounter,
    list_act_triggers_for_encounter,
    upsert_act_spell_timer,
    upsert_act_trigger,
)
