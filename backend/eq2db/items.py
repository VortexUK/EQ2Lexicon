"""
Local SQLite mirror of the Census /item/ collection.

All behaviour lives on :class:`ItemCatalogue` (the eq2db data-interface
convention — see AACatalogue / SpellCatalogue): DB lookups are instance
methods (the async ``find_by_name`` / ``find_by_id`` pair included); the
pure item-domain helpers (compute_class_label, extract_item_stats,
extract_effect_stats, item_to_row) are staticmethods on the same class so
consumers import ONE name — the shared ``catalogue`` instance. Module
level holds only types (GearRow), constants (DB_PATH, SERVER_MAX_LEVEL),
and the instance.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, NamedTuple

import aiosqlite

from backend.census._coerce import coerce_int as _coerce_int
from backend.census._coerce import coerce_str_or_none as _coerce_str
from backend.census.item_level import compute_ilvl
from backend.db_helpers import like_escape, resolve_db_path
from backend.eq2db._catalogue import BaseCatalogue
from backend.eq2db.classes import catalogue as _classes
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

_log = logging.getLogger(__name__)


DB_PATH = resolve_db_path("DB_ITEMS_PATH", "items", "items.db")


def _resolve_max_level() -> int | None:
    """
    SERVER_MAX_LEVEL env var caps item lookups by name to items usable at or
    below this level (e.g. 70 for an Echoes of Faydwer TLE).
    Unset → no level filtering.
    """
    import os

    v = os.getenv("SERVER_MAX_LEVEL")
    return int(v) if v else None


SERVER_MAX_LEVEL: int | None = _resolve_max_level()

# ---------------------------------------------------------------------------
# EQ2 class-group label helper
# ---------------------------------------------------------------------------
# Class-group membership is OWNED by the committed classes.db, accessed via
# backend.eq2db.classes.catalogue (archetype_groups / subclass_groups /
# crafter_names). Census API item rows use lowercase
# class-name keys ({"guardian": {...}}), so the tables below lowercase the
# canonical TitleCase names from the DB rows.
#
# DO NOT redefine class groupings here — edit the row in classes.db.

_CRAFTERS: frozenset[str] = frozenset(name.lower() for name in _classes.crafter_names())
_ALL_ADVENTURERS: frozenset[str] = frozenset(n.lower() for _, members in _classes.archetype_groups() for n in members)

# Groups checked in priority order: full archetypes first ("All Fighters"…),
# then subclasses ("All Warriors"…). The algorithm in compute_class_label
# removes matched classes from `remaining` as it goes, so a complete archetype
# is consumed before its constituent subclasses are tested.
_ARCHETYPES: list[tuple[str, frozenset[str]]] = [
    (f"All {archetype}s", frozenset(n.lower() for n in members)) for archetype, members in _classes.archetype_groups()
] + [(f"All {subclass}s", frozenset(n.lower() for n in members)) for subclass, members in _classes.subclass_groups()]


# Schema (CREATE TABLE / INDEX) lives in items.sql; init_db runs each block.

# Columns added after initial schema — used by init_db() to migrate existing DBs
_MIGRATIONS = [
    ("visible", "INTEGER DEFAULT 1"),
    ("typeinfo_name", "TEXT"),
    ("classes_json", "TEXT"),
    ("physical_damage_absorption", "INTEGER"),
    ("class_label", "TEXT"),
    ("class_count", "INTEGER"),
    ("tier_display", "TEXT"),
    ("skill_type", "TEXT"),
    ("spell_target", "TEXT"),
    ("spell_range", "TEXT"),
    ("spell_power_cost", "INTEGER"),
    ("spell_resistability", "TEXT"),
    ("flag_pvp", "INTEGER DEFAULT 0"),
    ("classification_list", "TEXT"),
    ("ilvl", "REAL"),
]

# ---------------------------------------------------------------------------
# Effect-based stat extraction
# ---------------------------------------------------------------------------
# Some stats in EQ2 are not in the `modifiers` dict but are expressed as
# human-readable effect lines, e.g.:
#   "Increases Attack Speed of caster by 25.0"
#
# Each entry is (compiled_regex, canonical_stat_name).  The regex must have
# exactly one capture group that captures the numeric value.
#
# The stat name must match a key in STAT_MAP / an entry in item_stats so the
# existing search machinery works unchanged.

_EFFECT_STAT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "Increases Attack Speed of caster by 25.0"
    # "Increases Attack Speed of the caster by 25.0"
    (re.compile(r"Attack Speed of .+? by ([\d.]+)"), "Haste"),
]

# Bump this string whenever _EFFECT_STAT_PATTERNS changes.
# init_db() stores it in _meta; backfill only runs when stored value differs.
_EFFECT_STATS_VERSION = "1"

_PVP_STAT_PREFIXES = ("pvp",)

# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Generic field coercers (Census JSON → column values)
# ---------------------------------------------------------------------------


def _flag(flags: dict, key: str) -> int:
    val = flags.get(key)
    if isinstance(val, dict):
        val = val.get("value", 0)
    return 1 if val in (1, True, "1", 1.0) else 0


def _str_field(item: dict, key: str) -> str | None:
    """Stripped string or None — shared Census coercion semantics
    (backend.census._coerce), applied to a dict field."""
    return _coerce_str(item.get(key))


def _int_field(v: Any) -> int | None:
    """coerce_int with 0 treated as NULL (quest IDs etc.)."""
    return _coerce_int(v) or None


# Keeps 0 — exactly the shared coercer.
_int_field_zero = _coerce_int


class GearRow(NamedTuple):
    ilvl: float | None
    wield_style: str | None
    level: int | None  # level_to_use, for adorn-bonus calc
    tier_display: str | None  # for adorn-bonus calc


class ItemCatalogue(BaseCatalogue):
    """Read (and build) access to one items.db file.

    The eq2db data-interface convention (see AACatalogue / SpellCatalogue):
    the DB path lives on the instance; the shared module-level ``catalogue``
    is the runtime entry point, and tests construct ``ItemCatalogue(tmp_db)``.
    The pure item-domain helpers are staticmethods here so the class is the
    one interface for everything item-shaped.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_items"])
        # Migrate existing DBs: add any columns introduced after initial creation.
        # Must run BEFORE index creation so new indexes on new columns don't fail.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        for col_name, col_def in _MIGRATIONS:
            if col_name not in existing_cols:
                conn.execute(_SQL["migration_add_column"].format(col=col_name, coltype=col_def))
        conn.executescript(_SQL["indexes_items"])
        # Stats side-table
        conn.execute(_SQL["schema_item_stats"])
        conn.executescript(_SQL["indexes_item_stats"])

    def _post_init(self, conn: sqlite3.Connection) -> None:
        # Backfill flag_pvp for items that predate this column.
        # Uses LOWER(raw_json) LIKE '%pvp%' — catches both pvp stats and effect text.
        # Safe to run every startup; is a no-op once all rows are set.
        self._backfill_pvp_flag(conn)
        # Backfill effect-derived stats (Haste etc.) for items that predate this
        # feature.  Version-gated — only runs once per _EFFECT_STATS_VERSION.
        self._backfill_effect_stats(conn)
        # Backfill classification_list for rows that predate this column.
        # Uses json_extract to pull the array out of raw_json; safe to re-run.
        self._backfill_classification_list(conn)

    # ── Pure helpers (no DB access — statics so the class is the ONE interface) ──

    @staticmethod
    def compute_class_label(classes: dict | None) -> str | None:
        """
        Return a human-readable class restriction label.

        Rules:
        - Any set that covers all 26 adventure classes (with or without crafters)
          → "All Classes"
        - Full archetype groups are collapsed: "All Fighters", "All Priests", etc.
        - Partial archetypes + individual classes are listed by display name.
        - None / empty → None
        """
        if not classes or not isinstance(classes, dict):
            return None

        keys = frozenset(classes.keys())
        adv = keys & _ALL_ADVENTURERS

        # All 26 adventure classes present (crafters optional) → "All Classes"
        if adv >= _ALL_ADVENTURERS:
            return "All Classes"

        parts: list[str] = []
        remaining = set(adv)

        for label, group in _ARCHETYPES:
            if remaining >= group:
                parts.append(label)
                remaining -= group

        # Any leftover individual classes
        for key in sorted(remaining):
            entry = classes.get(key)
            display = entry.get("displayname", key.title()) if isinstance(entry, dict) else key.title()
            parts.append(display)

        # Crafter-only items (no adventure classes matched at all)
        if not parts:
            crafter_keys = keys & _CRAFTERS
            if crafter_keys:
                return "Crafters"

        return " / ".join(parts) if parts else None

    @staticmethod
    def extract_item_stats(raw: dict) -> dict[str, float]:
        """
        Return a mapping of canonical stat display-name → value extracted from the
        Census ``modifiers`` dict stored in raw_json.

        Multiple API tag names that resolve to the same display name (e.g.
        ``arcane``/``elemental``/``noxious`` → "Resistances") keep only the first
        non-zero value encountered.
        """
        # Import lazily to avoid circular import at module level
        from backend.census.constants import STAT_MAP  # noqa: PLC0415

        modifiers = raw.get("modifiers") or {}
        result: dict[str, float] = {}
        for tag, mod in modifiers.items():
            if not isinstance(mod, dict):
                continue
            key = tag.lower()
            mapping = STAT_MAP.get(key)
            if mapping:
                display_name = mapping[0]
            else:
                api_dn = (mod.get("displayname") or "").strip()
                if api_dn.lower() == "all":
                    display_name = "Ability Mod"
                elif len(api_dn) > 3:
                    display_name = api_dn
                else:
                    continue  # no usable name → skip
            value = float(mod.get("value") or 0)
            if value and display_name not in result:
                result[display_name] = value
        return result

    @staticmethod
    def extract_effect_stats(raw: dict) -> dict[str, float]:
        """Return a mapping of canonical stat name → value parsed from effect_list.

        Complements extract_item_stats (which only reads the ``modifiers`` dict).
        Only extracts stats listed in _EFFECT_STAT_PATTERNS.  When both a modifier
        and an effect line exist for the same stat the modifier value takes
        precedence (callers use INSERT OR IGNORE for these rows).
        """
        result: dict[str, float] = {}
        for eff in raw.get("effect_list") or []:
            if not isinstance(eff, dict):
                continue
            desc = str(eff.get("description") or "")
            for pattern, stat_name in _EFFECT_STAT_PATTERNS:
                if stat_name in result:
                    continue  # already captured; keep first occurrence
                m = pattern.search(desc)
                if m:
                    try:
                        result[stat_name] = float(m.group(1))
                    except (ValueError, IndexError):
                        pass
        return result

    @staticmethod
    def _is_pvp_item(item: dict) -> int:
        """Return 1 if the item is PvP-specific, 0 otherwise.

        Detection strategy (either condition is sufficient):
        1. Has a stat whose Census name starts with 'pvp' (pvptoughness, pvplethality,
           pvpcriticalmitigation, etc.).
        2. The raw item JSON contains the substring 'pvp' (case-insensitive), which
           catches effect restrictions like 'Must be engaged in pvp combat'.
        """
        # Check stat modifiers. Census ships `modifiers` as a dict keyed by stat name
        # (e.g. {"pvptoughness": {...}}); older/alternate shapes are a list of dicts.
        for mod_list_key in ("modifiers", "stat_list", "stats"):
            coll = item.get(mod_list_key)
            if isinstance(coll, dict):
                names = [str(k).lower() for k in coll]
            elif isinstance(coll, list):
                names = [str(mod.get("name") or mod.get("stat") or "").lower() for mod in coll if isinstance(mod, dict)]
            else:
                continue
            if any(name.startswith(p) for name in names for p in _PVP_STAT_PREFIXES):
                return 1
        # Check raw JSON text (catches effects + any other pvp references)
        raw = json.dumps(item).lower()
        if "pvp" in raw:
            return 1
        return 0

    @staticmethod
    def item_to_row(item: dict) -> dict:
        """Convert a raw Census API item dict to a flat DB row dict."""
        typeinfo = item.get("typeinfo") or {}
        flags = item.get("flags") or {}
        slot_list = item.get("slot_list") or []
        extended = item.get("_extended") or {}
        reqskill = item.get("requiredskill")
        if not isinstance(reqskill, dict):
            reqskill = {}

        discovered = (extended.get("discovered") or {}).get("timestamp")
        aq = _int_field(item.get("associatedquest"))
        autoq = _int_field(item.get("autoquest"))

        tier_display = _str_field(item, "tier") or "COMMON"
        ilvl = compute_ilvl(
            level_to_use=_int_field_zero(item.get("leveltouse")),
            tier_display=tier_display,
            potency=ItemCatalogue.extract_item_stats(item).get("Potency", 0.0),
            item_type=_str_field(item, "type"),
            two_handed=typeinfo.get("wieldstyle") == "Two-Handed",
        )

        return {
            "id": item.get("id"),
            "displayname": str(item.get("displayname") or ""),
            "displayname_lower": str(item.get("displayname") or "").lower(),
            "gamelink": _str_field(item, "gamelink"),
            "description": _str_field(item, "description"),
            "last_update": _int_field_zero(item.get("last_update")),
            "tier": _str_field(item, "tier"),
            "tierid": _int_field_zero(item.get("tierid")),
            "tier_display": tier_display,
            "type": _str_field(item, "type"),
            "typeid": _int_field_zero(item.get("typeid")),
            "item_level": _int_field_zero(item.get("itemlevel")),
            "level_to_use": _int_field_zero(item.get("leveltouse")),
            "planar_level": _int_field_zero(item.get("planar_level")),
            "ilvl": ilvl,
            "icon_id": _int_field_zero(item.get("iconid")),
            "max_stack_size": _int_field_zero(item.get("maxstacksize")),
            "slot": slot_list[0].get("name") if slot_list else None,
            "armor_class_min": _int_field_zero(typeinfo.get("minarmorclass")),
            "armor_class_max": _int_field_zero(typeinfo.get("maxarmorclass")),
            "damage_min": _int_field_zero(typeinfo.get("mindamage")),
            "damage_max": _int_field_zero(typeinfo.get("maxdamage")),
            "damage_base": _int_field_zero(typeinfo.get("damage")),
            "damage_type": _str_field(typeinfo, "damagetype"),
            "damage_type_id": _int_field_zero(typeinfo.get("damagetypeid")),
            "damage_rating": typeinfo.get("damagerating"),
            "delay": typeinfo.get("delay"),
            "wield_style": _str_field(typeinfo, "wieldstyle"),
            "spell_name": _str_field(typeinfo, "spellname"),
            "spell_tier_id": _int_field_zero(typeinfo.get("tier")),
            "spell_cast_time": typeinfo.get("spellcasttime"),
            "spell_recast_time": typeinfo.get("spellrecasttime"),
            "spell_duration": typeinfo.get("spellduration"),
            "weapon_range_min": typeinfo.get("minrange"),
            "weapon_range_max": typeinfo.get("range"),
            "food_duration": _str_field(typeinfo, "duration"),
            "food_satiation": _str_field(typeinfo, "satiation"),
            "food_level": _int_field_zero(typeinfo.get("foodlevel")),
            "adornment_color": _str_field(typeinfo, "color"),
            "container_slots": _int_field_zero(typeinfo.get("slots")),
            "status_reduction": _int_field_zero(typeinfo.get("statusreduction")),
            "max_charges": _int_field_zero(item.get("maxcharges")),
            "setbonus_name": (item.get("setbonus_info") or {}).get("displayname"),
            "unique_equip_group": (item.get("unique_equipment_group") or {}).get("text"),
            "unique_equip_wearable_count": _int_field_zero(
                (item.get("unique_equipment_group") or {}).get("wearable_count")
            ),
            "unique_equip_prestige": 1 if (item.get("unique_equipment_group") or {}).get("prestige") == "true" else 0,
            "required_skill_name": reqskill.get("text"),
            "required_skill_min": _int_field_zero(reqskill.get("min_skill")),
            "associated_quest": aq,
            "autoquest": autoq,
            "first_discovered": _int_field_zero(discovered),
            "visible": _int_field_zero(item.get("visible")),
            "typeinfo_name": _str_field(typeinfo, "name"),
            "classes_json": json.dumps(typeinfo["classes"]) if typeinfo.get("classes") is not None else None,
            "physical_damage_absorption": _int_field_zero(typeinfo.get("physicaldamageabsorption")),
            "class_label": ItemCatalogue.compute_class_label(typeinfo.get("classes")),
            "class_count": len(typeinfo["classes"]) if typeinfo.get("classes") else None,
            "skill_type": _str_field(typeinfo, "skilltype"),
            "spell_target": _str_field(typeinfo, "spelltarget"),
            "spell_range": _str_field(typeinfo, "spellrange"),
            "spell_power_cost": _int_field_zero(typeinfo.get("spellpowercost")),
            "spell_resistability": _str_field(typeinfo, "resistability"),
            "flag_heirloom": _flag(flags, "heirloom"),
            "flag_lore": _flag(flags, "lore"),
            "flag_lore_equip": _flag(flags, "lore-equip"),
            "flag_no_trade": _flag(flags, "notrade"),
            "flag_no_value": _flag(flags, "novalue"),
            "flag_no_zone": _flag(flags, "nozone"),
            "flag_prestige": _flag(flags, "prestige"),
            "flag_relic": _flag(flags, "relic"),
            "flag_attunable": _flag(flags, "attunable"),
            "flag_ornate": _flag(flags, "ornate"),
            "flag_refined": _flag(flags, "refined"),
            "flag_infusable": _flag(flags, "infusable"),
            "flag_indestructible": _flag(flags, "indestructible"),
            "flag_pvp": ItemCatalogue._is_pvp_item(item),
            "raw_json": json.dumps(item),
            "classification_list": json.dumps(item.get("classification_list") or []),
        }

    # ── Startup backfills (run by init_db against an open conn) ──────────────

    @staticmethod
    def _backfill_classification_list(conn: sqlite3.Connection) -> None:
        """Populate classification_list for rows that predate the column.

        Uses SQLite's json_extract to pull the array straight out of raw_json.
        Rows that already have a non-NULL value are left untouched, so this is
        a cheap no-op after the first successful run.
        """
        conn.execute(_SQL["backfill_classification_list"])
        conn.commit()

    @staticmethod
    def _backfill_pvp_flag(conn: sqlite3.Connection) -> None:
        """Set flag_pvp=1 on any existing item whose raw_json mentions 'pvp'.

        Only touches rows where flag_pvp IS NULL or 0 and raw_json contains the
        string, so it runs quickly after the first pass (nearly all rows are 0).
        Guarded by a version key so the full table scan only happens once.
        """
        if get_meta(conn, "pvp_backfill_version") == "1":
            return  # already done
        conn.execute(_SQL["backfill_pvp_flag"])
        set_meta(conn, "pvp_backfill_version", "1")
        conn.commit()

    @staticmethod
    def _backfill_effect_stats(conn: sqlite3.Connection) -> None:
        """Parse effect_list from raw_json and populate effect-based stats in item_stats.

        Uses a version key in _meta so the full table scan only happens once per
        _EFFECT_STATS_VERSION.  Bump _EFFECT_STATS_VERSION when new patterns are
        added to _EFFECT_STAT_PATTERNS to trigger a re-run.

        Effect stats are inserted with OR IGNORE so existing modifier-derived
        values are never overwritten.
        """
        stored_version = get_meta(conn, "effect_stats_version")
        if stored_version == _EFFECT_STATS_VERSION:
            return  # already up to date

        # Narrow the scan using a keyword hint from the patterns so we don't have
        # to JSON-decode every row.  Build one LIKE filter per pattern.
        # For now "Attack Speed" covers all patterns in _EFFECT_STAT_PATTERNS.
        keyword_hints = ["attack speed"]  # lowercase; extend when patterns grow

        conditions = " OR ".join("LOWER(raw_json) LIKE ?" for _ in keyword_hints)
        rows = conn.execute(
            _SQL["select_raw_json_by_keyword"].format(conditions=conditions),
            [f"%{kw}%" for kw in keyword_hints],
        ).fetchall()

        stat_rows: list[tuple] = []
        for item_id, raw_json_str in rows:
            try:
                raw = json.loads(raw_json_str)
            except Exception as exc:
                _log.warning("[items-db] Failed to parse effect_stats JSON for item_id=%s: %s", item_id, exc)
                continue
            for stat_name, value in ItemCatalogue.extract_effect_stats(raw).items():
                stat_rows.append((item_id, stat_name, value))

        if stat_rows:
            conn.executemany(_SQL["insert_item_stat_ignore"], stat_rows)

        set_meta(conn, "effect_stats_version", _EFFECT_STATS_VERSION)
        conn.commit()

    # ── Build (scripts/download_items.py) ────────────────────────────────────

    def upsert_items(self, items: list[dict], conn: sqlite3.Connection) -> int:
        """Upsert a batch of raw Census item dicts. Returns number inserted/replaced."""
        rows = [self.item_to_row(item) for item in items]
        conn.executemany(_SQL["upsert"], rows)
        # Maintain item_stats side-table.
        # Modifier stats (from `modifiers` dict) are inserted first with OR REPLACE.
        # Effect stats (parsed from effect_list text) are inserted second with OR IGNORE
        # so that modifier values always win when both are present.
        mod_stat_rows: list[tuple] = []
        effect_stat_rows: list[tuple] = []
        for item in items:
            item_id = item.get("id")
            if item_id is None:
                continue
            for stat_name, value in self.extract_item_stats(item).items():
                mod_stat_rows.append((item_id, stat_name, value))
            for stat_name, value in self.extract_effect_stats(item).items():
                effect_stat_rows.append((item_id, stat_name, value))
        if mod_stat_rows:
            conn.executemany(_SQL["insert_item_stat_replace"], mod_stat_rows)
        if effect_stat_rows:
            conn.executemany(_SQL["insert_item_stat_ignore"], effect_stat_rows)
        conn.commit()
        return len(rows)

    def item_count(self, conn: sqlite3.Connection) -> int:
        return conn.execute(_SQL["count"]).fetchone()[0]

    # ── Lookups ──────────────────────────────────────────────────────────────

    def gear_for_ids(self, ids: list[int]) -> dict[int, GearRow]:
        """Return {item_id: GearRow} for the given ids (read-only).

        Covers both worn items (use ``ilvl``/``wield_style``) and adornments (use
        ``level``/``tier_display`` for the adorn bonus) in one query. Ids missing
        from the DB are absent from the result; non-gear items have ``ilvl=None``.
        Returns {} if the DB doesn't exist yet (graceful when items.db hasn't been
        provisioned)."""
        if not ids or not self.path.exists():
            return {}
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                _SQL["gear_for_ids"].format(placeholders=placeholders),
                ids,
            )
            return {row[0]: GearRow(row[1], row[2], row[3], row[4]) for row in rows}
        finally:
            conn.close()

    async def find_by_name(self, name: str) -> dict | None:
        """Return raw Census JSON dict for the closest name match, or None."""
        if not self.path.exists():
            return None
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async def _best(where_clause: str, params: tuple) -> aiosqlite.Row | None:
                """
                Return the best matching row given a WHERE clause + params.

                When SERVER_MAX_LEVEL is set:
                  1. Try items with level_to_use <= max (or no level requirement).
                     Order: highest level first, then best tier, then most recent.
                  2. If nothing qualifies, fall back to the highest-level item overall
                     (so the user at least gets something rather than nothing).
                When SERVER_MAX_LEVEL is not set:
                  Order by tierid DESC, last_update DESC (original behaviour).
                """
                if SERVER_MAX_LEVEL is not None:
                    # Phase 1: valid for current expansion
                    async with db.execute(
                        _SQL["find_by_name_level_capped"].format(where=where_clause),
                        params + (SERVER_MAX_LEVEL,),
                    ) as cur:
                        row = await cur.fetchone()
                    if row:
                        return row
                    # Phase 2: nothing valid — return highest-level item anyway
                    async with db.execute(
                        _SQL["find_by_name_any_level"].format(where=where_clause),
                        params,
                    ) as cur:
                        return await cur.fetchone()
                else:
                    async with db.execute(
                        _SQL["find_by_name_no_max_level"].format(where=where_clause),
                        params,
                    ) as cur:
                        return await cur.fetchone()

            # Exact match first
            row = await _best("displayname_lower = ?", (name.lower(),))
            if row:
                return json.loads(row["raw_json"])
            # LIKE fallback — escape user input so '%' / '_' in a literal name
            # can't silently broaden the match or force a table scan.
            row = await _best(
                "displayname_lower LIKE ? ESCAPE '\\'",
                (f"%{like_escape(name.lower())}%",),
            )
            return json.loads(row["raw_json"]) if row else None

    async def find_by_id(self, item_id: int) -> dict | None:
        """Return raw Census JSON dict for the given item ID, or None."""
        if not self.path.exists():
            return None
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SQL["find_by_id_raw_json"], (item_id,)) as cur:
                row = await cur.fetchone()
            return json.loads(row["raw_json"]) if row else None


# The shared default instance — every runtime consumer goes through this.
catalogue = ItemCatalogue()
