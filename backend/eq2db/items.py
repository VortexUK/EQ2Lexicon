from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, NamedTuple

import aiosqlite

from backend.census.item_level import compute_ilvl
from backend.eq2db.classes import (
    ARCHETYPE_GROUPS as _ARCHETYPE_GROUPS_SRC,
)
from backend.eq2db.classes import (
    CRAFTER_NAMES as _CRAFTER_NAMES_TITLE,
)
from backend.eq2db.classes import (
    SUBCLASS_GROUPS as _SUBCLASS_GROUPS_SRC,
)

_log = logging.getLogger(__name__)


def _resolve_db_path() -> Path:
    """
    DB path, in priority order:
    1. DB_ITEMS_PATH env var  (set this on Railway to point at the volume)
    2. Default: <repo_root>/data/items/items.db
    """
    import os

    env = os.getenv("DB_ITEMS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data" / "items" / "items.db"


DB_PATH = _resolve_db_path()


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
# the derived constants exported by backend.eq2db.classes (ARCHETYPE_GROUPS +
# SUBCLASS_GROUPS + CRAFTER_NAMES). Census API item rows use lowercase
# class-name keys ({"guardian": {...}}), so the tables below lowercase the
# canonical TitleCase names from the DB rows.
#
# DO NOT redefine class groupings here — edit the row in classes.db.

_CRAFTERS: frozenset[str] = frozenset(name.lower() for name in _CRAFTER_NAMES_TITLE)
_ALL_ADVENTURERS: frozenset[str] = frozenset(n.lower() for _, members in _ARCHETYPE_GROUPS_SRC for n in members)

# Groups checked in priority order: full archetypes first ("All Fighters"…),
# then subclasses ("All Warriors"…). The algorithm in compute_class_label
# removes matched classes from `remaining` as it goes, so a complete archetype
# is consumed before its constituent subclasses are tested.
_ARCHETYPES: list[tuple[str, frozenset[str]]] = [
    (f"All {archetype}s", frozenset(n.lower() for n in members)) for archetype, members in _ARCHETYPE_GROUPS_SRC
] + [(f"All {subclass}s", frozenset(n.lower() for n in members)) for subclass, members in _SUBCLASS_GROUPS_SRC]


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


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_META = """
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS items (
    -- Identity
    id                   INTEGER PRIMARY KEY,
    displayname          TEXT NOT NULL,
    displayname_lower    TEXT NOT NULL,
    gamelink             TEXT,
    description          TEXT,
    last_update          INTEGER,

    -- Quality / classification
    tier                 TEXT,
    tierid               INTEGER,
    type                 TEXT,
    typeid               INTEGER,
    item_level           INTEGER,
    level_to_use         INTEGER,
    planar_level         INTEGER,
    ilvl                 REAL,          -- WoW-style item level; NULL for non-gear
    icon_id              INTEGER,
    max_stack_size       INTEGER,

    -- Primary slot (slot_list[0].name for quick filtering)
    slot                 TEXT,

    -- Armor
    armor_class_min      INTEGER,
    armor_class_max      INTEGER,

    -- Weapon (from typeinfo)
    damage_min           INTEGER,
    damage_max           INTEGER,
    damage_base          INTEGER,
    damage_type          TEXT,
    damage_type_id       INTEGER,
    damage_rating        REAL,
    delay                REAL,
    wield_style          TEXT,

    -- Spell scroll / ability (from typeinfo)
    spell_name           TEXT,
    spell_tier_id        INTEGER,
    spell_cast_time      REAL,
    spell_recast_time    REAL,
    spell_duration       REAL,

    -- Ranged weapon
    weapon_range_min     REAL,
    weapon_range_max     REAL,

    -- Food / drink / consumable (from typeinfo)
    food_duration        TEXT,
    food_satiation       TEXT,
    food_level           INTEGER,

    -- Adornment (from typeinfo)
    adornment_color      TEXT,

    -- Container / house item (from typeinfo)
    container_slots      INTEGER,
    status_reduction     INTEGER,

    -- Charges
    max_charges          INTEGER,    -- -1 = unlimited

    -- Requirements
    required_skill_name  TEXT,
    required_skill_min   INTEGER,

    -- Set bonus
    setbonus_name        TEXT,

    -- Unique equipment group (prestige slot-limit sets)
    unique_equip_group         TEXT,
    unique_equip_wearable_count INTEGER,
    unique_equip_prestige      INTEGER DEFAULT 0,

    -- Quest links
    associated_quest     INTEGER,
    autoquest            INTEGER,

    -- Discovery (first seen on any world)
    first_discovered     INTEGER,

    -- Visibility (0 = hidden/disabled item, 1 = normal)
    visible              INTEGER DEFAULT 1,

    -- Typeinfo summary columns (queryable without parsing typeinfo_json)
    typeinfo_name                TEXT,       -- e.g. "Armor", "Weapon", "Spell Scroll"
    classes_json                 TEXT,       -- JSON array/object of allowed classes
    physical_damage_absorption   INTEGER,    -- armour mitigation value

    -- Pre-computed class label and count (derived from classes_json)
    class_label          TEXT,              -- e.g. "All Classes", "All Priests", "Guardian"
    class_count          INTEGER,           -- number of classes that can use this item

    -- Resolved tier name: tier if set, otherwise 'COMMON' for tierid 0/1/2
    tier_display         TEXT,

    -- Armor proficiency / spell scroll extras (from typeinfo)
    skill_type           TEXT,              -- e.g. "heavyarmor", "mediumarmor", "magicaffinity"
    spell_target         TEXT,              -- e.g. "Enemy", "Caster", "Group (AE)"
    spell_range          TEXT,              -- e.g. "Up to 25.0 meters"
    spell_power_cost     INTEGER,           -- power cost to cast
    spell_resistability  TEXT,              -- e.g. "25.2% Easier", "na"

    -- Common flags as fast-filter booleans
    flag_heirloom        INTEGER DEFAULT 0,
    flag_lore            INTEGER DEFAULT 0,
    flag_lore_equip      INTEGER DEFAULT 0,
    flag_no_trade        INTEGER DEFAULT 0,
    flag_no_value        INTEGER DEFAULT 0,
    flag_no_zone         INTEGER DEFAULT 0,
    flag_prestige        INTEGER DEFAULT 0,
    flag_relic           INTEGER DEFAULT 0,
    flag_attunable       INTEGER DEFAULT 0,
    flag_ornate          INTEGER DEFAULT 0,
    flag_refined         INTEGER DEFAULT 0,
    flag_infusable       INTEGER DEFAULT 0,
    flag_indestructible  INTEGER DEFAULT 0,
    flag_pvp             INTEGER DEFAULT 0,  -- 1 = PvP item (has pvp stats or pvp effect text)

    -- Full raw Census JSON — used by _parse_item(); all nested data lives here
    raw_json             TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_name        ON items(displayname_lower);",
    "CREATE INDEX IF NOT EXISTS idx_tier        ON items(tier);",
    "CREATE INDEX IF NOT EXISTS idx_typeid      ON items(typeid);",
    "CREATE INDEX IF NOT EXISTS idx_level       ON items(level_to_use);",
    "CREATE INDEX IF NOT EXISTS idx_item_level  ON items(item_level);",
    "CREATE INDEX IF NOT EXISTS idx_slot        ON items(slot);",
    "CREATE INDEX IF NOT EXISTS idx_icon        ON items(icon_id);",
    "CREATE INDEX IF NOT EXISTS idx_last_update ON items(last_update);",
    "CREATE INDEX IF NOT EXISTS idx_adorn_color ON items(adornment_color);",
    "CREATE INDEX IF NOT EXISTS idx_visible     ON items(visible);",
    "CREATE INDEX IF NOT EXISTS idx_ti_name     ON items(typeinfo_name);",
    "CREATE INDEX IF NOT EXISTS idx_class_label ON items(class_label);",
    "CREATE INDEX IF NOT EXISTS idx_skill_type  ON items(skill_type);",
    "CREATE INDEX IF NOT EXISTS idx_tier_disp   ON items(tier_display);",
]

# ---------------------------------------------------------------------------
# item_stats table  (one row per item × canonical stat name)
# ---------------------------------------------------------------------------

_CREATE_ITEM_STATS = """
CREATE TABLE IF NOT EXISTS item_stats (
    item_id  INTEGER NOT NULL,
    stat     TEXT    NOT NULL,   -- canonical display name e.g. "Ability Mod"
    value    REAL    NOT NULL,
    PRIMARY KEY (item_id, stat)
);
"""

_CREATE_STAT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_stat_name  ON item_stats(stat);",
    "CREATE INDEX IF NOT EXISTS idx_stat_item  ON item_stats(item_id);",
    "CREATE INDEX IF NOT EXISTS idx_stat_nv    ON item_stats(stat, value);",
]

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

_UPSERT_SQL = """
INSERT OR REPLACE INTO items (
    id, displayname, displayname_lower, gamelink, description, last_update,
    tier, tierid, type, typeid, item_level, level_to_use, planar_level, ilvl, icon_id, max_stack_size,
    slot,
    armor_class_min, armor_class_max,
    damage_min, damage_max, damage_base, damage_type, damage_type_id, damage_rating, delay, wield_style,
    weapon_range_min, weapon_range_max,
    spell_name, spell_tier_id, spell_cast_time, spell_recast_time, spell_duration,
    food_duration, food_satiation, food_level,
    adornment_color,
    container_slots, status_reduction,
    max_charges,
    setbonus_name,
    unique_equip_group, unique_equip_wearable_count, unique_equip_prestige,
    required_skill_name, required_skill_min,
    associated_quest, autoquest, first_discovered,
    visible, typeinfo_name, classes_json, physical_damage_absorption,
    class_label, class_count,
    tier_display,
    skill_type, spell_target, spell_range, spell_power_cost, spell_resistability,
    flag_heirloom, flag_lore, flag_lore_equip, flag_no_trade, flag_no_value,
    flag_no_zone, flag_prestige, flag_relic, flag_attunable, flag_ornate,
    flag_refined, flag_infusable, flag_indestructible, flag_pvp,
    raw_json, classification_list
) VALUES (
    :id, :displayname, :displayname_lower, :gamelink, :description, :last_update,
    :tier, :tierid, :type, :typeid, :item_level, :level_to_use, :planar_level, :ilvl, :icon_id, :max_stack_size,
    :slot,
    :armor_class_min, :armor_class_max,
    :damage_min, :damage_max, :damage_base, :damage_type, :damage_type_id, :damage_rating, :delay, :wield_style,
    :weapon_range_min, :weapon_range_max,
    :spell_name, :spell_tier_id, :spell_cast_time, :spell_recast_time, :spell_duration,
    :food_duration, :food_satiation, :food_level,
    :adornment_color,
    :container_slots, :status_reduction,
    :max_charges,
    :setbonus_name,
    :unique_equip_group, :unique_equip_wearable_count, :unique_equip_prestige,
    :required_skill_name, :required_skill_min,
    :associated_quest, :autoquest, :first_discovered,
    :visible, :typeinfo_name, :classes_json, :physical_damage_absorption,
    :class_label, :class_count,
    :tier_display,
    :skill_type, :spell_target, :spell_range, :spell_power_cost, :spell_resistability,
    :flag_heirloom, :flag_lore, :flag_lore_equip, :flag_no_trade, :flag_no_value,
    :flag_no_zone, :flag_prestige, :flag_relic, :flag_attunable, :flag_ornate,
    :flag_refined, :flag_infusable, :flag_indestructible, :flag_pvp,
    :raw_json, :classification_list
)
"""


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------


def _like_escape(s: str) -> str:
    """Escape SQLite ``LIKE`` wildcards so a user-supplied search string can't
    silently broaden the match (``%``) or force a table scan (``_``).

    Matching SQL must use ``ESCAPE '\\'`` for these escapes to take effect.
    Will move to ``web/lib/db_helpers.py`` in Phase 2a — duplicated per-module
    in Phase 1 for the surgical fix.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _flag(flags: dict, key: str) -> int:
    val = flags.get(key)
    if isinstance(val, dict):
        val = val.get("value", 0)
    return 1 if val in (1, True, "1", 1.0) else 0


def _str_field(item: dict, key: str) -> str | None:
    v = item.get(key)
    if v is None or isinstance(v, dict):
        return None
    s = str(v).strip()
    return s if s else None


def _int_field(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v) or None  # treat 0 as NULL for quest IDs etc.
    except (ValueError, TypeError):
        return None


def _int_field_zero(v: Any) -> int | None:
    """Like _int_field but keeps 0."""
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


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


_PVP_STAT_PREFIXES = ("pvp",)


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
        potency=extract_item_stats(item).get("Potency", 0.0),
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
        "class_label": compute_class_label(typeinfo.get("classes")),
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
        "flag_pvp": _is_pvp_item(item),
        "raw_json": json.dumps(item),
        "classification_list": json.dumps(item.get("classification_list") or []),
    }


# ---------------------------------------------------------------------------
# Synchronous helpers (used by download script)
# ---------------------------------------------------------------------------


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create (or open) the DB, create tables/indexes if missing. Returns connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(_CREATE_META)
    conn.execute(_CREATE_TABLE)
    # Migrate existing DBs: add any columns introduced after initial creation
    # Must run BEFORE index creation so new indexes on new columns don't fail
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for col_name, col_def in _MIGRATIONS:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_def}")
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    # Stats side-table
    conn.execute(_CREATE_ITEM_STATS)
    for idx in _CREATE_STAT_INDEXES:
        conn.execute(idx)
    conn.commit()
    # Backfill flag_pvp for items that predate this column.
    # Uses LOWER(raw_json) LIKE '%pvp%' — catches both pvp stats and effect text.
    # Safe to run every startup; is a no-op once all rows are set.
    _backfill_pvp_flag(conn)
    # Backfill effect-derived stats (Haste etc.) for items that predate this
    # feature.  Version-gated — only runs once per _EFFECT_STATS_VERSION.
    _backfill_effect_stats(conn)
    # Backfill classification_list for rows that predate this column.
    # Uses json_extract to pull the array out of raw_json; safe to re-run.
    _backfill_classification_list(conn)
    return conn


def _backfill_classification_list(conn: sqlite3.Connection) -> None:
    """Populate classification_list for rows that predate the column.

    Uses SQLite's json_extract to pull the array straight out of raw_json.
    Rows that already have a non-NULL value are left untouched, so this is
    a cheap no-op after the first successful run.
    """
    conn.execute("""
        UPDATE items
        SET classification_list = json_extract(raw_json, '$.classification_list')
        WHERE classification_list IS NULL
          AND raw_json IS NOT NULL
    """)
    conn.commit()


def _backfill_pvp_flag(conn: sqlite3.Connection) -> None:
    """Set flag_pvp=1 on any existing item whose raw_json mentions 'pvp'.

    Only touches rows where flag_pvp IS NULL or 0 and raw_json contains the
    string, so it runs quickly after the first pass (nearly all rows are 0).
    Guarded by a version key so the full table scan only happens once.
    """
    if get_meta(conn, "pvp_backfill_version") == "1":
        return  # already done
    conn.execute("""
        UPDATE items
        SET flag_pvp = 1
        WHERE flag_pvp = 0
          AND raw_json IS NOT NULL
          AND LOWER(raw_json) LIKE '%pvp%'
    """)
    set_meta(conn, "pvp_backfill_version", "1")
    conn.commit()


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
        f"SELECT id, raw_json FROM items WHERE raw_json IS NOT NULL AND ({conditions})",
        [f"%{kw}%" for kw in keyword_hints],
    ).fetchall()

    stat_rows: list[tuple] = []
    for item_id, raw_json_str in rows:
        try:
            raw = json.loads(raw_json_str)
        except Exception as exc:
            _log.warning("[db] Failed to parse effect_stats JSON for item_id=%s: %s", item_id, exc)
            continue
        for stat_name, value in extract_effect_stats(raw).items():
            stat_rows.append((item_id, stat_name, value))

    if stat_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO item_stats (item_id, stat, value) VALUES (?, ?, ?)",
            stat_rows,
        )

    set_meta(conn, "effect_stats_version", _EFFECT_STATS_VERSION)
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def upsert_items(items: list[dict], conn: sqlite3.Connection) -> int:
    """Upsert a batch of raw Census item dicts. Returns number inserted/replaced."""
    rows = [item_to_row(item) for item in items]
    conn.executemany(_UPSERT_SQL, rows)
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
        for stat_name, value in extract_item_stats(item).items():
            mod_stat_rows.append((item_id, stat_name, value))
        for stat_name, value in extract_effect_stats(item).items():
            effect_stat_rows.append((item_id, stat_name, value))
    if mod_stat_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO item_stats (item_id, stat, value) VALUES (?, ?, ?)",
            mod_stat_rows,
        )
    if effect_stat_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO item_stats (item_id, stat, value) VALUES (?, ?, ?)",
            effect_stat_rows,
        )
    conn.commit()
    return len(rows)


def item_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]


class GearRow(NamedTuple):
    ilvl: float | None
    wield_style: str | None
    level: int | None  # level_to_use, for adorn-bonus calc
    tier_display: str | None  # for adorn-bonus calc


def gear_for_ids(ids: list[int], db_path: Path = DB_PATH) -> dict[int, GearRow]:
    """Return {item_id: GearRow} for the given ids (read-only).

    Covers both worn items (use ``ilvl``/``wield_style``) and adornments (use
    ``level``/``tier_display`` for the adorn bonus) in one query. Ids missing
    from the DB are absent from the result; non-gear items have ``ilvl=None``.
    Returns {} if the DB doesn't exist yet (graceful when items.db hasn't been
    provisioned)."""
    if not ids or not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, ilvl, wield_style, level_to_use, tier_display FROM items WHERE id IN ({placeholders})",
            ids,
        )
        return {row[0]: GearRow(row[1], row[2], row[3], row[4]) for row in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Async helpers (used by bot)
# ---------------------------------------------------------------------------


async def find_by_name(name: str, path: Path = DB_PATH) -> dict | None:
    """Return raw Census JSON dict for the closest name match, or None."""
    if not path.exists():
        return None
    async with aiosqlite.connect(path) as db:
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
                    f"SELECT raw_json FROM items WHERE {where_clause}"
                    "  AND (level_to_use IS NULL OR level_to_use <= ?)"
                    "  ORDER BY level_to_use DESC, tierid DESC, last_update DESC LIMIT 1",
                    params + (SERVER_MAX_LEVEL,),
                ) as cur:
                    row = await cur.fetchone()
                if row:
                    return row
                # Phase 2: nothing valid — return highest-level item anyway
                async with db.execute(
                    f"SELECT raw_json FROM items WHERE {where_clause}"
                    "  ORDER BY level_to_use DESC, tierid DESC, last_update DESC LIMIT 1",
                    params,
                ) as cur:
                    return await cur.fetchone()
            else:
                async with db.execute(
                    f"SELECT raw_json FROM items WHERE {where_clause}  ORDER BY tierid DESC, last_update DESC LIMIT 1",
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
            (f"%{_like_escape(name.lower())}%",),
        )
        return json.loads(row["raw_json"]) if row else None


async def find_by_id(item_id: int, path: Path = DB_PATH) -> dict | None:
    """Return raw Census JSON dict for the given item ID, or None."""
    if not path.exists():
        return None
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT raw_json FROM items WHERE id = ? LIMIT 1", (item_id,)) as cur:
            row = await cur.fetchone()
        return json.loads(row["raw_json"]) if row else None
