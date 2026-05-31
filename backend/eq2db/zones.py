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

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from backend.db_helpers import resolve_db_path
from backend.eq2db import _meta as _meta_db
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DB_PATH: Path = resolve_db_path("DB_ZONES_PATH", "zones", "zones.db")


# Schema (CREATE TABLE / INDEX) lives in zones.sql; init_db runs each block.
# Column-list fragment for find_* queries is loaded from zones.sql at module import.
_SELECT_COLS = _SQL["select_zone_cols"]


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
    _meta_db.create_table(conn)
    conn.execute(_SQL["schema_zones"])
    conn.execute(_SQL["schema_zone_types"])
    conn.execute(_SQL["schema_zone_aliases"])
    conn.execute(_SQL["schema_zone_encounters"])
    conn.execute(_SQL["schema_zone_encounter_mobs"])
    conn.execute(_SQL["schema_featured_raid_expansions"])
    conn.execute(_SQL["schema_featured_raid_zones"])
    conn.execute(_SQL["schema_featured_raid_categories"])
    # Migration: zone categories + position for drag-reorder.
    # Idempotent — already-applied schemas raise OperationalError on the
    # duplicate-column attempt, which we swallow.
    for stmt in (_SQL["migrate_add_featured_position"], _SQL["migrate_add_featured_category"]):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    # Migration: drop the pre-v2 zone_bosses table if it lingers from
    # an older DB build. No need to preserve data — bosses are always
    # rebuilt from the curated source file.
    conn.execute(_SQL["drop_legacy_zone_bosses"])
    conn.executescript(_SQL["indexes_all"])
    # One-time data normalization (idempotent): legacy `encounter_name`
    # values were the comma-joined display of every mob in the encounter
    # ("Ire, Malevolence"). The web roster editor treats encounter_name
    # as the PRIMARY mob's name (kept in sync with the mob at
    # position 0). Rewrite any comma-containing row to its position-0
    # mob name; rows without any mobs are left untouched.
    # NOTE: Not version-gated — this UPDATE is cheap (only touches rows
    # with commas in the name) and must remain idempotent across multiple
    # init_db calls (see test_init_db_normalizes_comma_joined_encounter_name).
    conn.execute(_SQL["normalise_comma_joined_encounter_names"])
    # One-time data normalization (idempotent): strip the wiki-import
    # " (Zone)" disambiguator suffix from zone names (e.g. "Kurn's Tower
    # (Zone)" → "Kurn's Tower"). EQ2i uses the parenthetical to
    # disambiguate a wiki article from the in-game zone of the same name;
    # the in-game logs and our UI both use the bare name. The old name
    # is also inserted as an alias so anything historically referencing
    # the parenthesised form still resolves via find_by_name. Idempotent:
    # subsequent runs match zero rows (LIKE filter no longer hits the
    # already-cleaned names).
    conn.execute(_SQL["normalise_paren_zone_to_alias"])
    conn.execute(_SQL["normalise_strip_paren_zone"])
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
            conn.execute(_SQL["upsert_zone"], row)
            zone_id = conn.execute(_SQL["select_zone_id_by_name"], (z["name"],)).fetchone()[0]

            # Reset and repopulate types + aliases for this zone. Cheaper
            # than diffing on every rebuild.
            conn.execute(_SQL["delete_zone_types_for_zone"], (zone_id,))
            types = z["classification"].get("types") or []
            if types:
                conn.executemany(
                    _SQL["insert_zone_type"],
                    [(zone_id, t) for t in types],
                )

            conn.execute(_SQL["delete_zone_aliases_for_zone"], (zone_id,))
            aliases = z.get("aliases") or []
            if aliases:
                conn.executemany(
                    _SQL["insert_zone_alias"],
                    [(a, a.lower(), zone_id) for a in aliases],
                )
            n += 1
    return n


def zone_count(conn: sqlite3.Connection) -> int:
    return conn.execute(_SQL["count_zones"]).fetchone()[0]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def _hydrate_zone(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Convert a zones row + sub-queries for types/aliases/bosses into a dict.

    The ``bosses`` array is now built via ``ZoneEncounter._list_for_zone_id``
    + ``.to_dict(with_mob_ids=True)``: same SQL, same shape, but the build
    sequence lives in one place. (Pre-refactor the encounter-and-mob
    hydration was inlined here AND in ``list_bosses_for_zone``, which made
    drift inevitable.)"""
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
    d["types"] = [r[0] for r in conn.execute(_SQL["list_types_for_zone"], (d["id"],))]
    d["aliases"] = [r[0] for r in conn.execute(_SQL["list_aliases_for_zone"], (d["id"],))]
    d["bosses"] = [enc.to_dict(with_mob_ids=True) for enc in ZoneEncounter._list_for_zone_id(conn, d["id"])]
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
            _SQL["find_zone_by_name_lower"].format(cols=_SELECT_COLS),
            (name.lower(),),
        ).fetchone()
        if row is None:
            alias_row = conn.execute(
                _SQL["find_zone_id_by_alias"],
                (name.lower(),),
            ).fetchone()
            if alias_row is None:
                return None
            row = conn.execute(
                _SQL["find_zone_by_id"].format(cols=_SELECT_COLS),
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
                _SQL["list_zones_by_expansion_typed"].format(cols=_SELECT_COLS),
                (short, type_filter),
            ).fetchall()
        else:
            rows = conn.execute(
                _SQL["list_zones_by_expansion"].format(cols=_SELECT_COLS),
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
            _SQL["list_zones_by_event"].format(cols=_SELECT_COLS),
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
            _SQL["list_zones_by_type"].format(cols=_SELECT_COLS),
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

    Back-compat shim: delegates to ``ZoneEncounter.replace_all_for_zone``."""
    return ZoneEncounter.replace_all_for_zone(conn, zone_id, encounters)


def list_bosses_for_zone(zone_name: str, path: Path = DB_PATH) -> list[dict]:
    """All raid encounters in a zone (looked up by canonical name OR alias).

    Back-compat shim: delegates to ``ZoneEncounter.list_for_zone_name`` and
    converts the typed results to the legacy dict shape (mob ids included
    — the editor frontend targets individual mobs)."""
    return [enc.to_dict(with_mob_ids=True) for enc in ZoneEncounter.list_for_zone_name(zone_name, path=path)]


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
            _SQL["list_zones_by_boss"].format(cols=_SELECT_COLS),
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
            rows = conn.execute(_SQL["list_distinct_expansions"]).fetchall()
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
        return dict(conn.execute(_SQL["expansion_counts"]))


# ---------------------------------------------------------------------------
# Editable encounter helpers
# ---------------------------------------------------------------------------


def _zone_name_and_expansion(zone_id: int, path: Path) -> tuple[str | None, str | None]:
    """Canonical zone name + expansion for the raids_db mirror."""
    with sqlite3.connect(path) as conn:
        r = conn.execute(_SQL["select_zone_name_and_expansion"], (zone_id,)).fetchone()
        return (r[0], r[1]) if r else (None, None)


def _mirror_primary_rename_in_raids_db(zone_id: int, old_name: str, new_name: str, path: Path) -> None:
    """Rename a raids_db.raid_encounters row keyed by (zone_name, old_name) →
    new_name, if it exists. Looks up zone_name from the parent encounter.
    Used by both ZoneEncounter.update() and ZoneEncounterMob.rename()."""
    if old_name == new_name:
        return
    zone_name, _exp = _zone_name_and_expansion(zone_id, path)
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


# Sentinel used by ZoneEncounter.update() to distinguish "leave unchanged"
# from "explicitly set to None". `stage = None` should clear the stage;
# `stage` omitted entirely should keep whatever was there.
_UNSET: object = object()


@dataclass(frozen=True)
class ZoneEncounterMob:
    """One mob within a raid encounter. Position 0 is the canonical primary
    (its name is mirrored to ``zone_encounters.encounter_name``); 1..N are
    sibling mobs in group encounters (e.g. 4-mob raid trains)."""

    id: int
    encounter_id: int
    mob_name: str
    position: int

    @classmethod
    def _from_row(cls, row: sqlite3.Row, *, encounter_id: int | None = None) -> ZoneEncounterMob:
        """Build from a sqlite3.Row. ``encounter_id`` is taken from the row
        when present, otherwise from the caller (e.g. when the SELECT
        doesn't bring it back because the caller already knows it)."""
        keys = row.keys()
        eid = row["encounter_id"] if "encounter_id" in keys else (encounter_id if encounter_id is not None else 0)
        return cls(id=row["id"], encounter_id=eid, mob_name=row["mob_name"], position=row["position"])

    def to_dict(self) -> dict:
        """Legacy {id, mob_name, position} shape that pre-model callers expect."""
        return {"id": self.id, "mob_name": self.mob_name, "position": self.position}

    @classmethod
    def add_to_encounter(
        cls,
        encounter_id: int,
        *,
        mob_name: str,
        make_primary: bool = False,
        path: Path = DB_PATH,
    ) -> ZoneEncounterMob:
        """Add a mob to an encounter. By default appends as a sibling at the
        next available position. With ``make_primary=True``, shifts every
        existing mob down by 1 and inserts the new mob at position 0, then
        updates the parent encounter_name to the new primary (mirrored to
        raids_db)."""
        old_primary_name: str | None = None
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            with conn:
                if make_primary:
                    # Capture the current primary's name BEFORE the shift,
                    # so we know what to rename in raids_db.
                    primary = conn.execute(_SQL["select_primary_mob_name"], (encounter_id,)).fetchone()
                    if primary is not None:
                        old_primary_name = primary["mob_name"]
                    # Two-phase shift of existing mobs down by 1.
                    conn.execute(_SQL["shift_mobs_negative"], (encounter_id,))
                    conn.execute(_SQL["shift_mobs_back_positive"], (encounter_id,))
                    cur = conn.execute(
                        _SQL["insert_encounter_mob_primary"],
                        (encounter_id, mob_name, mob_name.lower()),
                    )
                    new_id = cur.lastrowid
                    conn.execute(_SQL["update_encounter_name"], (mob_name, encounter_id))
                else:
                    next_pos = conn.execute(_SQL["max_mob_position_for_encounter"], (encounter_id,)).fetchone()[0]
                    cur = conn.execute(
                        _SQL["insert_encounter_mob"],
                        (encounter_id, mob_name, mob_name.lower(), next_pos),
                    )
                    new_id = cur.lastrowid
            row = conn.execute(_SQL["select_mob_by_id"], (new_id,)).fetchone()
        if make_primary and old_primary_name is not None:
            _mirror_primary_rename_in_raids_db(_encounter_zone_id(encounter_id, path), old_primary_name, mob_name, path)
        return cls._from_row(row, encounter_id=encounter_id)

    @classmethod
    def find_by_id(cls, mob_id: int, path: Path = DB_PATH) -> ZoneEncounterMob | None:
        """Single-mob fetch by id. Returns None if not found."""
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL["select_mob_for_update"], (mob_id,)).fetchone()
        return (
            cls(id=mob_id, encounter_id=row["encounter_id"], mob_name=row["mob_name"], position=row["position"])
            if row
            else None
        )

    @classmethod
    def list_for_encounter(cls, encounter_id: int, path: Path = DB_PATH) -> list[ZoneEncounterMob]:
        """All mobs for an encounter, ordered by position."""
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                cls._from_row(r, encounter_id=encounter_id)
                for r in conn.execute(_SQL["list_mobs_for_encounter_asc"], (encounter_id,))
            ]

    def rename(self, new_mob_name: str, path: Path = DB_PATH) -> ZoneEncounterMob:
        """Rename. If this mob is at position 0 (the primary), also updates
        the parent encounter_name so the two stay in sync, and mirrors the
        rename onto raids_db.raid_encounters (if a row exists there).
        Returns the renamed instance."""
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            with conn:
                conn.execute(_SQL["update_mob_name"], (new_mob_name, new_mob_name.lower(), self.id))
                if self.position == 0:
                    conn.execute(_SQL["update_encounter_name"], (new_mob_name, self.encounter_id))
        if self.position == 0:
            _mirror_primary_rename_in_raids_db(
                _encounter_zone_id(self.encounter_id, path), self.mob_name, new_mob_name, path
            )
        return ZoneEncounterMob(
            id=self.id, encounter_id=self.encounter_id, mob_name=new_mob_name, position=self.position
        )

    def promote_to_primary(self, path: Path = DB_PATH) -> ZoneEncounterMob:
        """Swap this mob (a sibling) with the current primary (position 0).
        No-op if already primary. Updates the parent encounter_name and
        mirrors the rename onto raids_db. Returns the promoted instance."""
        if self.position == 0:
            return self
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            primary = conn.execute(_SQL["select_primary_mob_id_and_name"], (self.encounter_id,)).fetchone()
            if primary is None:
                # Shouldn't happen if invariants hold, but defensively: just
                # move this mob to position 0 with no swap.
                with conn:
                    conn.execute(_SQL["update_mob_position_to_zero"], (self.id,))
                    conn.execute(_SQL["update_encounter_name"], (self.mob_name, self.encounter_id))
                return ZoneEncounterMob(id=self.id, encounter_id=self.encounter_id, mob_name=self.mob_name, position=0)
            old_primary_name = primary["mob_name"]
            with conn:
                # Park the old primary at -1 (sentinel), promote the sibling
                # to 0, then move the old primary into the sibling's old slot.
                conn.execute(_SQL["update_mob_position_to_neg_one"], (primary["id"],))
                conn.execute(_SQL["update_mob_position_to_zero"], (self.id,))
                conn.execute(_SQL["update_mob_position"], (self.position, primary["id"]))
                conn.execute(_SQL["update_encounter_name"], (self.mob_name, self.encounter_id))
        _mirror_primary_rename_in_raids_db(
            _encounter_zone_id(self.encounter_id, path), old_primary_name, self.mob_name, path
        )
        return ZoneEncounterMob(id=self.id, encounter_id=self.encounter_id, mob_name=self.mob_name, position=0)

    def delete(self, path: Path = DB_PATH) -> bool:
        """Delete this mob. Refuses with ValueError when it's the only mob
        in the encounter (an encounter needs ≥ 1 mob) or when it's the
        primary while siblings exist (caller must promote a sibling first).
        Returns False if the row is no longer present, True on successful
        delete."""
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            row = conn.execute(_SQL["select_mob_encounter_position"], (self.id,)).fetchone()
            if row is None:
                return False
            total = conn.execute(_SQL["count_mobs_for_encounter"], (row["encounter_id"],)).fetchone()[0]
            if total <= 1:
                raise ValueError("cannot delete the last mob of an encounter")
            if row["position"] == 0:
                raise ValueError(
                    "cannot delete the primary mob while siblings exist; promote a sibling to primary first"
                )
            conn.execute(_SQL["delete_mob_by_id"], (self.id,))
            conn.commit()
            return True


def _encounter_zone_id(encounter_id: int, path: Path) -> int:
    """Cheap zone_id lookup off an encounter — used by the raids-db mirror
    paths to resolve zone_name without re-reading the full encounter row."""
    with sqlite3.connect(path) as conn:
        row = conn.execute(_SQL["select_encounter_zone_id"], (encounter_id,)).fetchone()
        return int(row[0]) if row else 0


@dataclass(frozen=True)
class ZoneEncounter:
    """A raid encounter within a zone (one boss or a 2-4-mob group). The
    canonical name (``encounter_name``) is always the position-0 mob's name;
    rename operations on the primary mob keep the two in sync."""

    id: int
    zone_id: int
    encounter_name: str
    position: int
    stage: str | None
    wiki_url: str | None
    mobs: list[ZoneEncounterMob]

    @classmethod
    def _from_row(cls, conn: sqlite3.Connection, row: sqlite3.Row) -> ZoneEncounter:
        """Build from an encounter row + a fresh mob fetch using the open
        connection. Mobs come back with their real ids so downstream code
        can mutate them directly via ``ZoneEncounterMob`` methods."""
        mobs = [
            ZoneEncounterMob(
                id=r["id"],
                encounter_id=row["id"],
                mob_name=r["mob_name"],
                position=r["position"],
            )
            for r in conn.execute(_SQL["list_mobs_for_encounter"], (row["id"],))
        ]
        return cls(
            id=row["id"],
            zone_id=row["zone_id"],
            encounter_name=row["encounter_name"],
            position=row["position"],
            stage=row["stage"],
            wiki_url=row["wiki_url"],
            mobs=mobs,
        )

    def to_dict(self, *, with_mob_ids: bool = False) -> dict:
        """``{id, zone_id, encounter_name, position, stage, wiki_url, mobs}``
        shape that pre-model callers (routes) expect.

        ``with_mob_ids=True`` produces the hydrate-a-whole-zone shape
        (``mobs[]`` contains ``id``) — needed by the editor frontend so it
        can target individual mob rows for rename/promote/delete. Encounter
        CRUD callers leave it ``False`` because the encounter is the unit
        being mutated and the mob ids are irrelevant to those responses."""
        mob_shape = (
            (lambda m: {"id": m.id, "mob_name": m.mob_name, "position": m.position})
            if with_mob_ids
            else (lambda m: {"mob_name": m.mob_name, "position": m.position})
        )
        return {
            "id": self.id,
            "zone_id": self.zone_id,
            "encounter_name": self.encounter_name,
            "position": self.position,
            "stage": self.stage,
            "wiki_url": self.wiki_url,
            "mobs": [mob_shape(m) for m in self.mobs],
        }

    @classmethod
    def find_by_id(cls, encounter_id: int, path: Path = DB_PATH) -> ZoneEncounter | None:
        """Single-encounter fetch by id, with mobs. None if not found."""
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL["select_encounter_by_id"], (encounter_id,)).fetchone()
            return cls._from_row(conn, row) if row else None

    @classmethod
    def _list_for_zone_id(cls, conn: sqlite3.Connection, zone_id: int) -> list[ZoneEncounter]:
        """List all encounters for a zone using an open connection. Internal
        helper for ``list_for_zone_name`` and ``_hydrate_zone`` — they both
        already hold a connection and shouldn't open a fresh one per call.
        ``conn.row_factory`` must be ``sqlite3.Row`` (the caller's
        responsibility — every call site sets it)."""
        return [cls._from_row(conn, r) for r in conn.execute(_SQL["list_encounters_for_zone"], (zone_id,)).fetchall()]

    @classmethod
    def list_for_zone_name(cls, zone_name: str, path: Path = DB_PATH) -> list[ZoneEncounter]:
        """All raid encounters in a zone, resolving the zone by canonical
        name OR alias. Empty list if zone unknown or has no encounters."""
        if not path.exists() or not zone_name:
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL["select_zone_id_by_name_lower"], (zone_name.lower(),)).fetchone()
            if row is None:
                row = conn.execute(
                    _SQL["find_zone_id_by_alias_aliased"],
                    (zone_name.lower(),),
                ).fetchone()
            if row is None:
                return []
            return cls._list_for_zone_id(conn, row["id"])

    @classmethod
    def replace_all_for_zone(cls, conn: sqlite3.Connection, zone_id: int, encounters: list[dict]) -> int:
        """Bulk-replace every encounter (and its mobs) in a zone. Atomic per
        zone. Re-runnable: wipes both child tables so removed encounters
        and removed group mobs disappear cleanly. Returns the number of
        *encounters* written (not individual mobs).

        Takes an open ``conn`` rather than a path because the build script
        wraps multiple zones in a single outer transaction.

        Each input dict shape::

            {
                "encounter_name": "Adkar Vyx",
                "position": int,                  # order within the zone
                "stage": str | None,              # "Wing 1", "First Floor"
                "wiki_url": str | None,
                "mobs": [                         # one entry per individual mob
                    {"mob_name": "Adkar Vyx", "position": 0},
                    ...
                ],
            }
        """
        conn.execute(_SQL["delete_encounters_for_zone"], (zone_id,))
        if not encounters:
            return 0
        for enc in encounters:
            cur = conn.execute(
                _SQL["insert_encounter"],
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
                _SQL["insert_encounter_mob"],
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

    @classmethod
    def add_to_zone(
        cls,
        zone_id: int,
        *,
        primary_mob: str,
        position: int | None = None,
        stage: str | None = None,
        wiki_url: str | None = None,
        path: Path = DB_PATH,
    ) -> ZoneEncounter:
        """Append a new encounter to a zone with a single primary mob at
        position 0. If ``position`` is None, appends after the current max;
        if provided, inserts at that slot — caller is responsible for it
        being free (UNIQUE(zone_id, position) raises otherwise)."""
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            if position is None:
                row = conn.execute(_SQL["max_encounter_position_for_zone"], (zone_id,)).fetchone()
                position = int(row["p"])
            cur = conn.execute(_SQL["insert_encounter"], (zone_id, primary_mob, position, stage, wiki_url))
            enc_id = cur.lastrowid
            conn.execute(_SQL["insert_encounter_mob_primary"], (enc_id, primary_mob, primary_mob.lower()))
            conn.commit()
            row = conn.execute(_SQL["select_encounter_by_id"], (enc_id,)).fetchone()
            return cls._from_row(conn, row)

    def update(
        self,
        *,
        primary_mob: str | None = None,
        stage: str | None = _UNSET,  # type: ignore[assignment]
        wiki_url: str | None = _UNSET,  # type: ignore[assignment]
        path: Path = DB_PATH,
    ) -> ZoneEncounter:
        """Edit encounter metadata. When ``primary_mob`` is given, also
        renames the position-0 mob in zone_encounter_mobs and mirrors the
        rename onto raids_db.raid_encounters (if a row exists there).
        Returns the updated instance."""
        new_name = primary_mob if primary_mob is not None else self.encounter_name
        new_stage = self.stage if stage is _UNSET else stage
        new_wiki = self.wiki_url if wiki_url is _UNSET else wiki_url
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(_SQL["update_encounter_meta"], (new_name, new_stage, new_wiki, self.id))
            if primary_mob is not None:
                conn.execute(
                    _SQL["update_encounter_mob_primary_rename"],
                    (primary_mob, primary_mob.lower(), self.id),
                )
            conn.commit()
            row = conn.execute(_SQL["select_encounter_by_id"], (self.id,)).fetchone()
            result = ZoneEncounter._from_row(conn, row)
        if primary_mob is not None:
            _mirror_primary_rename_in_raids_db(self.zone_id, self.encounter_name, primary_mob, path)
        return result

    def delete(self, path: Path = DB_PATH) -> bool:
        """Delete this encounter. Cascades zone_encounter_mobs via FK and
        the matching raids_db row (which itself cascades triggers / timers
        / strategies). Returns True if a row was deleted."""
        zone_name, _exp = _zone_name_and_expansion(self.zone_id, path)
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cur = conn.execute(_SQL["delete_encounter_by_id"], (self.id,))
            conn.commit()
            if cur.rowcount == 0:
                return False
        if zone_name is not None:
            from backend.eq2db import raids as _raids_db

            with _raids_db.init_db() as rconn:
                _raids_db.delete_raid_encounter_by_zone_mob(rconn, zone_name=zone_name, mob_name=self.encounter_name)
                rconn.commit()
        return True

    @staticmethod
    def reorder_in_zone(zone_id: int, ordered_ids: list[int], path: Path = DB_PATH) -> None:
        """Atomically renumber the zone's encounters to 1..N matching the
        given order. ``ordered_ids`` MUST be a complete permutation of the
        zone's current encounter ids — raises ValueError otherwise. The
        two-phase write (negative sentinels then 1..N) is needed because
        UNIQUE(zone_id, position) would otherwise reject mid-update
        collisions. Mirrors the new positions onto raids_db rows."""
        if len(ordered_ids) != len(set(ordered_ids)):
            raise ValueError("ordered_ids contains duplicates")
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            current = {
                r["id"]: (r["encounter_name"], r["position"])
                for r in conn.execute(_SQL["list_zone_encounter_positions"], (zone_id,))
            }
            if set(ordered_ids) != set(current.keys()):
                missing = set(current.keys()) - set(ordered_ids)
                extra = set(ordered_ids) - set(current.keys())
                raise ValueError(
                    f"reorder_in_zone: not a permutation of zone {zone_id}'s "
                    f"encounters (missing={sorted(missing)}, extra={sorted(extra)})"
                )
            zone_row = conn.execute(_SQL["select_zone_name_by_id"], (zone_id,)).fetchone()
            zone_name = zone_row["name"] if zone_row else None
            with conn:  # single transaction
                # Two-phase write to dodge the UNIQUE(zone_id, position)
                # collision on mid-update overlap: negative sentinels first,
                # then 1..N.
                for tmp_neg, enc_id in enumerate(ordered_ids, start=1):
                    conn.execute(_SQL["update_encounter_position"], (-tmp_neg, enc_id))
                for new_pos, enc_id in enumerate(ordered_ids, start=1):
                    conn.execute(_SQL["update_encounter_position"], (new_pos, enc_id))
        if zone_name is None:
            return
        # Mirror onto raids_db: for each encounter whose primary mob has a
        # raid_encounters row, update its position. Lookup uses the CURRENT
        # encounter_name (which is the primary mob name post-normalization).
        from backend.eq2db import raids as _raids_db

        with _raids_db.init_db() as rconn:
            for new_pos, enc_id in enumerate(ordered_ids, start=1):
                name, _old_pos = current[enc_id]
                _raids_db.update_raid_encounter_if_exists(rconn, zone_name=zone_name, mob_name=name, position=new_pos)
            rconn.commit()


# ---------------------------------------------------------------------------
# Back-compat free-function shims
# ---------------------------------------------------------------------------
# Routes + tests call these directly. They delegate to the model methods
# above + convert the result to the legacy dict shape. Once all callers
# migrate to the model, these can be deleted.


def _row_to_encounter(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Legacy shim. New code should use ``ZoneEncounter._from_row().to_dict()``."""
    return ZoneEncounter._from_row(conn, row).to_dict()


def add_encounter(
    zone_id: int,
    *,
    primary_mob: str,
    position: int | None = None,
    stage: str | None = None,
    wiki_url: str | None = None,
    path: Path = DB_PATH,
) -> dict:
    return ZoneEncounter.add_to_zone(
        zone_id, primary_mob=primary_mob, position=position, stage=stage, wiki_url=wiki_url, path=path
    ).to_dict()


def update_encounter(
    encounter_id: int,
    *,
    primary_mob: str | None = None,
    stage: str | None = _UNSET,  # type: ignore[assignment]
    wiki_url: str | None = _UNSET,  # type: ignore[assignment]
    path: Path = DB_PATH,
) -> dict:
    enc = ZoneEncounter.find_by_id(encounter_id, path=path)
    if enc is None:
        raise LookupError(f"zone_encounter {encounter_id} not found")
    return enc.update(primary_mob=primary_mob, stage=stage, wiki_url=wiki_url, path=path).to_dict()


def reorder_encounters(zone_id: int, ordered_encounter_ids: list[int], path: Path = DB_PATH) -> None:
    ZoneEncounter.reorder_in_zone(zone_id, ordered_encounter_ids, path=path)


def list_mobs(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    return [m.to_dict() for m in ZoneEncounterMob.list_for_encounter(encounter_id, path=path)]


def add_mob(encounter_id: int, *, mob_name: str, make_primary: bool = False, path: Path = DB_PATH) -> dict:
    return ZoneEncounterMob.add_to_encounter(
        encounter_id, mob_name=mob_name, make_primary=make_primary, path=path
    ).to_dict()


def update_mob(mob_id: int, *, mob_name: str, path: Path = DB_PATH) -> dict:
    mob = ZoneEncounterMob.find_by_id(mob_id, path=path)
    if mob is None:
        raise LookupError(f"zone_encounter_mob {mob_id} not found")
    return mob.rename(mob_name, path=path).to_dict()


def promote_mob(mob_id: int, path: Path = DB_PATH) -> dict:
    mob = ZoneEncounterMob.find_by_id(mob_id, path=path)
    if mob is None:
        raise LookupError(f"zone_encounter_mob {mob_id} not found")
    return mob.promote_to_primary(path=path).to_dict()


def delete_mob(mob_id: int, path: Path = DB_PATH) -> bool:
    mob = ZoneEncounterMob.find_by_id(mob_id, path=path)
    if mob is None:
        return False
    return mob.delete(path=path)


def delete_encounter(encounter_id: int, path: Path = DB_PATH) -> bool:
    enc = ZoneEncounter.find_by_id(encounter_id, path=path)
    if enc is None:
        return False
    return enc.delete(path=path)


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
            _SQL["find_zone_by_name_lower"].format(cols=_SELECT_COLS),
            (zone_name.lower(),),
        ).fetchone()
        if row is None:
            alias_row = conn.execute(
                _SQL["find_zone_id_by_alias"],
                (zone_name.lower(),),
            ).fetchone()
            if alias_row is None:
                return None
            row = conn.execute(
                _SQL["find_zone_by_id"].format(cols=_SELECT_COLS),
                (alias_row[0],),
            ).fetchone()
            if row is None:
                return None
        conn.execute(
            _SQL["insert_zone_type_or_ignore"],
            (row["id"], type_token),
        )
        conn.commit()
        return _hydrate_zone(conn, row)


# ---------------------------------------------------------------------------
# FeaturedRaidExpansion / FeaturedRaidZone / FeaturedRaidCategory models
# ---------------------------------------------------------------------------
# Active-record CRUD for the /raids page admin curation: which expansions
# are surfaced, which raid zones within each, and which named "lane"
# (category) each zone is dragged into. The featured-raid trio is the only
# part of zones.db the maintainer actively edits via the admin UI; every
# other table is rebuilt from the curated JSON source.
#
# `_dedup_expansion_rows` factors a quirk: the JOIN-based listing queries
# can return multiple rows per (expansion_short) when the same expansion
# has zone rows differing in name/year (data drift from wiki re-ingests).
# Both list paths collapse by `short`, keeping the first (newest-year-first
# courtesy of ORDER BY in the SQL).


def _dedup_expansion_rows(rows: list[sqlite3.Row]) -> list[FeaturedRaidExpansion]:
    seen: set[str] = set()
    result: list[FeaturedRaidExpansion] = []
    for r in rows:
        short = r["short"]
        if short in seen:
            continue
        seen.add(short)
        result.append(FeaturedRaidExpansion(expansion_short=short, name=r["name"], year=r["year"]))
    return result


@dataclass(frozen=True)
class FeaturedRaidExpansion:
    """An expansion surfaced on the /raids page. Active-record over the
    ``featured_raid_expansions`` table — but with an implicit-membership
    twist: an expansion is treated as "featured" if either it has a row
    in the table OR any of its zones appear in ``featured_raid_zones``
    (so adding a zone alone is enough to surface the expansion)."""

    expansion_short: str
    name: str
    year: int | None

    def to_dict(self) -> dict:
        """Legacy ``{short, name, year}`` shape that the routes return."""
        return {"short": self.expansion_short, "name": self.name, "year": self.year}

    @classmethod
    def list_active(cls, path: Path = DB_PATH) -> list[FeaturedRaidExpansion]:
        """Featured expansions (explicit + implicit-via-zones), newest first."""
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(_SQL["list_featured_raid_expansions"]).fetchall()
        return _dedup_expansion_rows(rows)

    @classmethod
    def list_available(cls, path: Path = DB_PATH) -> list[FeaturedRaidExpansion]:
        """Expansions in zones.db NOT yet featured (neither explicit row nor
        any zone of theirs featured). The admin 'Add expansion' picker."""
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(_SQL["list_available_raid_expansions"]).fetchall()
        return _dedup_expansion_rows(rows)

    @classmethod
    def create(cls, expansion_short: str, path: Path = DB_PATH) -> FeaturedRaidExpansion | None:
        """Mark an expansion as featured. Validates that the expansion is
        known to zones.db (returns None otherwise — route layer maps to
        404). Idempotent for already-featured expansions: returns the
        same instance both on fresh-insert and on already-featured."""
        if not path.exists() or not expansion_short:
            return None
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            meta = conn.execute(_SQL["select_expansion_name_year"], (expansion_short,)).fetchone()
            if meta is None:
                return None
            conn.execute(_SQL["insert_featured_raid_expansion"], (expansion_short,))
            conn.commit()
        return cls(expansion_short=expansion_short, name=meta["name"], year=meta["year"])

    def remove(self, path: Path = DB_PATH) -> bool:
        """Remove from featured AND cascade-remove this expansion's
        featured raid zones. Preserves the underlying zone_encounters
        data — just hides everything from /raids until re-added.

        Returns True if the featured_raid_expansions row was removed,
        False if there was nothing to remove. Cascaded zone deletions
        don't influence the return value."""
        if not path.exists():
            return False
        with sqlite3.connect(path) as conn:
            with conn:
                conn.execute(
                    _SQL["remove_featured_raid_zones_in_expansion"],
                    (self.expansion_short,),
                )
                cur = conn.execute(
                    _SQL["delete_featured_raid_expansion"],
                    (self.expansion_short,),
                )
                return cur.rowcount > 0


@dataclass(frozen=True)
class FeaturedRaidZone:
    """A raid zone surfaced under a featured expansion. Models the
    ``(zone_id, position, category)`` row that lives in
    ``featured_raid_zones`` plus enough zone-side metadata
    (``expansion_short``, ``zone_name``) to act on it without re-fetching.

    The full hydrated zone shape (types/aliases/bosses) is produced by
    the route shims via ``_hydrate_zone`` — keeping that join out of the
    model means a FeaturedRaidZone is cheap to construct and the
    expensive hydration only happens at the route boundary."""

    zone_id: int
    zone_name: str
    expansion_short: str
    position: int
    category: str | None

    @classmethod
    def list_for_expansion(cls, expansion_short: str, path: Path = DB_PATH) -> list[FeaturedRaidZone]:
        """All featured raid zones for an expansion, sorted by
        (category, position). NULL categories sort first (SQLite default),
        which lands the implicit Uncategorised lane at the top."""
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                _SQL["list_featured_raid_zones"].format(cols=_SELECT_COLS),
                (expansion_short,),
            ).fetchall()
            return [
                cls(
                    zone_id=r["id"],
                    zone_name=r["name"],
                    expansion_short=r["expansion_short"],
                    position=r["featured_position"],
                    category=r["featured_category"],
                )
                for r in rows
            ]

    @classmethod
    def add(cls, zone_name: str, path: Path = DB_PATH) -> FeaturedRaidZone | None:
        """Mark a raid zone as featured. Validates that the zone exists
        AND is tagged raid_x4 or raid_x2 — we don't want random zones
        surfacing on /raids just because admin typed a name. Lands in
        the implicit Uncategorised lane (category=NULL) at MAX+1 so the
        new entry shows at the bottom; admin drags into a named lane
        afterwards. Returns the new instance, or None on validation
        failure (route maps to 400)."""
        if not path.exists() or not zone_name:
            return None
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            zone = conn.execute(
                _SQL["find_zone_by_name_lower"].format(cols=_SELECT_COLS),
                (zone_name.lower(),),
            ).fetchone()
            if zone is None:
                return None
            is_raid = conn.execute(_SQL["check_zone_is_raid"], (zone["id"],)).fetchone()
            if not is_raid:
                return None
            max_pos_row = conn.execute(
                _SQL["max_featured_position_uncategorised"],
                (zone["expansion_short"],),
            ).fetchone()
            new_position = (max_pos_row[0] if max_pos_row else -1) + 1
            conn.execute(
                _SQL["insert_featured_raid_zone_uncategorised"],
                (zone["id"], new_position),
            )
            conn.commit()
            return cls(
                zone_id=zone["id"],
                zone_name=zone["name"],
                expansion_short=zone["expansion_short"],
                position=new_position,
                category=None,
            )

    def remove(self, path: Path = DB_PATH) -> bool:
        """Remove from featured. Preserves zone_encounters boss data so
        re-adding restores the lane. Returns True if a row was removed."""
        if not path.exists():
            return False
        with sqlite3.connect(path) as conn:
            cur = conn.execute(
                _SQL["delete_featured_raid_zone_by_name"],
                (self.zone_name.lower(),),
            )
            conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def reorder_in_expansion(expansion_short: str, ordering: list[dict], path: Path = DB_PATH) -> bool:
        """Atomically rewrite category + position for every zone in ``ordering``.

        Each entry: ``{"name": str, "category": str | None, "position": int}``.

        Uses the same two-phase shift pattern as ``ZoneEncounter.reorder_in_zone``
        (negative sentinels first, then final values) so any transient UNIQUE /
        ordering collision is impossible mid-update.

        Auto-creates missing ``featured_raid_categories`` rows at MAX+1 for
        any category name that appears in ``ordering`` but isn't tracked
        yet — this is how a fresh-typed lane name becomes a draggable lane
        header on the next render.

        Returns False if any zone in ``ordering`` isn't currently featured
        in this expansion (route layer maps to 400)."""
        if not path.exists():
            return False
        with sqlite3.connect(path) as conn:
            with conn:
                # Validate every zone — surfaces typos / stale clients early.
                zone_ids: dict[str, int] = {}
                for entry in ordering:
                    row = conn.execute(
                        _SQL["find_featured_zone_id_in_expansion"],
                        (entry["name"].lower(), expansion_short),
                    ).fetchone()
                    if not row:
                        return False
                    zone_ids[entry["name"]] = row[0]
                # Auto-create missing categories at end of the existing run.
                seen_categories = {e["category"] for e in ordering if e.get("category")}
                if seen_categories:
                    max_pos_row = conn.execute(
                        _SQL["max_category_position"],
                        (expansion_short,),
                    ).fetchone()
                    next_pos = (max_pos_row[0] if max_pos_row else -1) + 1
                    for cat in seen_categories:
                        cur = conn.execute(
                            _SQL["insert_featured_raid_category_or_ignore"],
                            (expansion_short, cat, next_pos),
                        )
                        if cur.rowcount:
                            next_pos += 1
                # Two-phase write.
                for i, entry in enumerate(ordering):
                    conn.execute(
                        _SQL["update_featured_raid_zone_position_and_category"],
                        (-(i + 1), entry.get("category"), zone_ids[entry["name"]]),
                    )
                for entry in ordering:
                    conn.execute(
                        _SQL["update_featured_raid_zone_position"],
                        (entry["position"], zone_ids[entry["name"]]),
                    )
                return True


@dataclass(frozen=True)
class FeaturedRaidCategory:
    """A named lane within a featured expansion (e.g. "Tier 1", "Bonus
    raids"). The implicit Uncategorised lane (``category IS NULL`` on
    zones) is NOT modelled here — the frontend always pins it at the top
    and there's no row for it."""

    expansion_short: str
    name: str
    position: int

    def to_dict(self) -> dict:
        """Legacy ``{name, position}`` shape. ``expansion_short`` is
        contextual (the caller already knew it to make the list call)
        and is not surfaced."""
        return {"name": self.name, "position": self.position}

    @classmethod
    def list_for_expansion(cls, expansion_short: str, path: Path = DB_PATH) -> list[FeaturedRaidCategory]:
        """Admin-defined categories in saved order."""
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                _SQL["list_featured_raid_categories"],
                (expansion_short,),
            ).fetchall()
            return [cls(expansion_short=expansion_short, name=r["name"], position=r["position"]) for r in rows]

    @classmethod
    def create(cls, expansion_short: str, name: str, path: Path = DB_PATH) -> FeaturedRaidCategory | None:
        """Create an empty category lane at MAX+1 position. Returns None
        if a category by this name already exists for the expansion."""
        if not path.exists():
            return None
        with sqlite3.connect(path) as conn:
            existing = conn.execute(
                _SQL["check_featured_raid_category_exists"],
                (expansion_short, name),
            ).fetchone()
            if existing:
                return None
            max_pos = conn.execute(
                _SQL["max_category_position"],
                (expansion_short,),
            ).fetchone()
            new_pos = (max_pos[0] if max_pos else -1) + 1
            conn.execute(
                _SQL["insert_featured_raid_category"],
                (expansion_short, name, new_pos),
            )
            conn.commit()
            return cls(expansion_short=expansion_short, name=name, position=new_pos)

    def delete(self, path: Path = DB_PATH) -> bool:
        """Delete this category. Zones currently in it have their category
        set to NULL (move to Uncategorised). Returns True if a row was
        deleted."""
        if not path.exists():
            return False
        with sqlite3.connect(path) as conn:
            with conn:
                conn.execute(
                    _SQL["move_featured_zones_to_null_category"],
                    (self.name, self.expansion_short),
                )
                cur = conn.execute(
                    _SQL["delete_featured_raid_category"],
                    (self.expansion_short, self.name),
                )
                return cur.rowcount > 0

    @staticmethod
    def reorder_in_expansion(expansion_short: str, ordering: list[dict], path: Path = DB_PATH) -> bool:
        """Atomic two-phase position rewrite for category lanes.

        Each entry: ``{"name": str, "position": int}``. Returns False if
        any name in ``ordering`` isn't a category in this expansion
        (route layer maps to 400)."""
        if not path.exists():
            return False
        with sqlite3.connect(path) as conn:
            with conn:
                for entry in ordering:
                    row = conn.execute(
                        _SQL["check_featured_raid_category_exists"],
                        (expansion_short, entry["name"]),
                    ).fetchone()
                    if not row:
                        return False
                # Two-phase write: temp negatives, then final positions.
                for i, entry in enumerate(ordering):
                    conn.execute(
                        _SQL["update_featured_raid_category_position"],
                        (-(i + 1), expansion_short, entry["name"]),
                    )
                for entry in ordering:
                    conn.execute(
                        _SQL["update_featured_raid_category_position"],
                        (entry["position"], expansion_short, entry["name"]),
                    )
                return True


# ---------------------------------------------------------------------------
# Back-compat free-function shims (featured-raid trio)
# ---------------------------------------------------------------------------
# Routes call these directly with the legacy dict / bool / None contracts.
# They delegate to the model methods above. Once the route layer migrates
# to the models, these can be deleted.


def list_featured_raid_expansions(path: Path = DB_PATH) -> list[dict]:
    return [e.to_dict() for e in FeaturedRaidExpansion.list_active(path=path)]


def list_available_raid_expansions(path: Path = DB_PATH) -> list[dict]:
    return [e.to_dict() for e in FeaturedRaidExpansion.list_available(path=path)]


def add_featured_raid_expansion(expansion_short: str, path: Path = DB_PATH) -> bool:
    return FeaturedRaidExpansion.create(expansion_short, path=path) is not None


def remove_featured_raid_expansion(expansion_short: str, path: Path = DB_PATH) -> bool:
    # The legacy contract is "key-only delete"; we don't go through
    # find_by_short because the model.remove() only needs expansion_short
    # and the stub instance carries that.
    return FeaturedRaidExpansion(expansion_short=expansion_short, name="", year=None).remove(path=path)


def list_featured_raid_zones(expansion_short: str, path: Path = DB_PATH) -> list[dict]:
    # Routes need the full hydrated zone shape (types/aliases/bosses)
    # PLUS the featuring metadata (position + category). The model only
    # carries the featuring metadata, so the shim joins via _hydrate_zone.
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _SQL["list_featured_raid_zones"].format(cols=_SELECT_COLS),
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
    # Returns hydrated zone dicts (the 'Add raid zone' admin picker shows
    # full zone info). Not exposed via the FeaturedRaidZone model because
    # the returned shape is Zone, not FeaturedRaidZone — they're not yet
    # featured.
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _SQL["list_available_raid_zones"].format(cols=_SELECT_COLS),
            (expansion_short,),
        ).fetchall()
        return [_hydrate_zone(conn, r) for r in rows]


def add_featured_raid_zone(zone_name: str, path: Path = DB_PATH) -> dict | None:
    # Legacy contract: returns hydrated zone dict (NOT the FeaturedRaidZone
    # shape). Re-hydrate by name after the model add() succeeds.
    fz = FeaturedRaidZone.add(zone_name, path=path)
    if fz is None:
        return None
    return find_by_name(fz.zone_name, path=path)


def remove_featured_raid_zone(zone_name: str, path: Path = DB_PATH) -> bool:
    # Stub instance with name only — remove() uses .zone_name as the
    # delete key; other fields are unused.
    return FeaturedRaidZone(zone_id=0, zone_name=zone_name, expansion_short="", position=0, category=None).remove(
        path=path
    )


def reorder_featured_raid_zones(expansion_short: str, ordering: list[dict], path: Path = DB_PATH) -> bool:
    return FeaturedRaidZone.reorder_in_expansion(expansion_short, ordering, path=path)


def list_featured_raid_categories(expansion_short: str, path: Path = DB_PATH) -> list[dict]:
    return [c.to_dict() for c in FeaturedRaidCategory.list_for_expansion(expansion_short, path=path)]


def create_featured_raid_category(expansion_short: str, name: str, path: Path = DB_PATH) -> bool:
    return FeaturedRaidCategory.create(expansion_short, name, path=path) is not None


def delete_featured_raid_category(expansion_short: str, name: str, path: Path = DB_PATH) -> bool:
    # Stub instance — delete() only needs expansion_short + name as keys.
    return FeaturedRaidCategory(expansion_short=expansion_short, name=name, position=0).delete(path=path)


def reorder_featured_raid_categories(expansion_short: str, ordering: list[dict], path: Path = DB_PATH) -> bool:
    return FeaturedRaidCategory.reorder_in_expansion(expansion_short, ordering, path=path)


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
            _SQL["find_zone_by_name_lower"].format(cols=_SELECT_COLS),
            (zone_name.lower(),),
        ).fetchone()
        if row is None:
            alias_row = conn.execute(
                _SQL["find_zone_id_by_alias"],
                (zone_name.lower(),),
            ).fetchone()
            if alias_row is None:
                return None
            row = conn.execute(
                _SQL["find_zone_by_id"].format(cols=_SELECT_COLS),
                (alias_row[0],),
            ).fetchone()
            if row is None:
                return None
        conn.execute(
            _SQL["delete_zone_type"],
            (row["id"], type_token),
        )
        conn.commit()
        return _hydrate_zone(conn, row)
