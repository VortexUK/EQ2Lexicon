from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "items" / "items.db"

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

    -- Complex nested blobs (display-only, not queried)
    modifiers_json       TEXT,
    typeinfo_json        TEXT,
    effect_list_json     TEXT,
    adornment_slots_json TEXT,
    adornment_list_json  TEXT,
    classification_json  TEXT,
    slot_list_json       TEXT,
    setbonus_list_json   TEXT,
    flags_json           TEXT,

    -- Full raw Census JSON — lets existing _parse_item() work unchanged
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
]

_UPSERT_SQL = """
INSERT OR REPLACE INTO items (
    id, displayname, displayname_lower, gamelink, description, last_update,
    tier, tierid, type, typeid, item_level, level_to_use, planar_level, icon_id, max_stack_size,
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
    flag_heirloom, flag_lore, flag_lore_equip, flag_no_trade, flag_no_value,
    flag_no_zone, flag_prestige, flag_relic, flag_attunable, flag_ornate,
    flag_refined, flag_infusable, flag_indestructible,
    modifiers_json, typeinfo_json, effect_list_json, adornment_slots_json,
    adornment_list_json, classification_json, slot_list_json, setbonus_list_json,
    flags_json, raw_json
) VALUES (
    :id, :displayname, :displayname_lower, :gamelink, :description, :last_update,
    :tier, :tierid, :type, :typeid, :item_level, :level_to_use, :planar_level, :icon_id, :max_stack_size,
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
    :flag_heirloom, :flag_lore, :flag_lore_equip, :flag_no_trade, :flag_no_value,
    :flag_no_zone, :flag_prestige, :flag_relic, :flag_attunable, :flag_ornate,
    :flag_refined, :flag_infusable, :flag_indestructible,
    :modifiers_json, :typeinfo_json, :effect_list_json, :adornment_slots_json,
    :adornment_list_json, :classification_json, :slot_list_json, :setbonus_list_json,
    :flags_json, :raw_json
)
"""


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

def _flag(flags: dict, key: str) -> int:
    val = flags.get(key)
    if isinstance(val, dict):
        val = val.get("value", 0)
    return 1 if val in (1, True, "1", 1.0) else 0


def _str_field(item: dict, key: str) -> Optional[str]:
    v = item.get(key)
    if v is None or isinstance(v, dict):
        return None
    s = str(v).strip()
    return s if s else None


def _int_field(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v) or None   # treat 0 as NULL for quest IDs etc.
    except (ValueError, TypeError):
        return None


def _int_field_zero(v: Any) -> Optional[int]:
    """Like _int_field but keeps 0."""
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def item_to_row(item: dict) -> dict:
    """Convert a raw Census API item dict to a flat DB row dict."""
    typeinfo     = item.get("typeinfo") or {}
    flags        = item.get("flags") or {}
    slot_list    = item.get("slot_list") or []
    extended     = item.get("_extended") or {}
    reqskill     = item.get("requiredskill")
    if not isinstance(reqskill, dict):
        reqskill = {}

    discovered   = (extended.get("discovered") or {}).get("timestamp")
    aq           = _int_field(item.get("associatedquest"))
    autoq        = _int_field(item.get("autoquest"))

    return {
        "id":                   item.get("id"),
        "displayname":          item.get("displayname", ""),
        "displayname_lower":    (item.get("displayname") or "").lower(),
        "gamelink":             _str_field(item, "gamelink"),
        "description":          _str_field(item, "description"),
        "last_update":          _int_field_zero(item.get("last_update")),
        "tier":                 _str_field(item, "tier"),
        "tierid":               _int_field_zero(item.get("tierid")),
        "type":                 _str_field(item, "type"),
        "typeid":               _int_field_zero(item.get("typeid")),
        "item_level":           _int_field_zero(item.get("itemlevel")),
        "level_to_use":         _int_field_zero(item.get("leveltouse")),
        "planar_level":         _int_field_zero(item.get("planar_level")),
        "icon_id":              _int_field_zero(item.get("iconid")),
        "max_stack_size":       _int_field_zero(item.get("maxstacksize")),
        "slot":                 slot_list[0].get("name") if slot_list else None,
        "armor_class_min":      _int_field_zero(typeinfo.get("minarmorclass")),
        "armor_class_max":      _int_field_zero(typeinfo.get("maxarmorclass")),
        "damage_min":           _int_field_zero(typeinfo.get("mindamage")),
        "damage_max":           _int_field_zero(typeinfo.get("maxdamage")),
        "damage_base":          _int_field_zero(typeinfo.get("damage")),
        "damage_type":          _str_field(typeinfo, "damagetype"),
        "damage_type_id":       _int_field_zero(typeinfo.get("damagetypeid")),
        "damage_rating":        typeinfo.get("damagerating"),
        "delay":                typeinfo.get("delay"),
        "wield_style":          _str_field(typeinfo, "wieldstyle"),
        "spell_name":           _str_field(typeinfo, "spellname"),
        "spell_tier_id":        _int_field_zero(typeinfo.get("tier")),
        "spell_cast_time":      typeinfo.get("spellcasttime"),
        "spell_recast_time":    typeinfo.get("spellrecasttime"),
        "spell_duration":       typeinfo.get("spellduration"),
        "weapon_range_min":     typeinfo.get("minrange"),
        "weapon_range_max":     typeinfo.get("range"),
        "food_duration":        _str_field(typeinfo, "duration"),
        "food_satiation":       _str_field(typeinfo, "satiation"),
        "food_level":           _int_field_zero(typeinfo.get("foodlevel")),
        "adornment_color":      _str_field(typeinfo, "color"),
        "container_slots":      _int_field_zero(typeinfo.get("slots")),
        "status_reduction":     _int_field_zero(typeinfo.get("statusreduction")),
        "max_charges":          _int_field_zero(item.get("maxcharges")),
        "setbonus_name":             (item.get("setbonus_info") or {}).get("displayname"),
        "unique_equip_group":        (item.get("unique_equipment_group") or {}).get("text"),
        "unique_equip_wearable_count": _int_field_zero((item.get("unique_equipment_group") or {}).get("wearable_count")),
        "unique_equip_prestige":     1 if (item.get("unique_equipment_group") or {}).get("prestige") == "true" else 0,
        "required_skill_name":  reqskill.get("text"),
        "required_skill_min":   _int_field_zero(reqskill.get("min_skill")),
        "associated_quest":     aq,
        "autoquest":            autoq,
        "first_discovered":     _int_field_zero(discovered),
        "flag_heirloom":        _flag(flags, "heirloom"),
        "flag_lore":            _flag(flags, "lore"),
        "flag_lore_equip":      _flag(flags, "lore-equip"),
        "flag_no_trade":        _flag(flags, "notrade"),
        "flag_no_value":        _flag(flags, "novalue"),
        "flag_no_zone":         _flag(flags, "nozone"),
        "flag_prestige":        _flag(flags, "prestige"),
        "flag_relic":           _flag(flags, "relic"),
        "flag_attunable":       _flag(flags, "attunable"),
        "flag_ornate":          _flag(flags, "ornate"),
        "flag_refined":         _flag(flags, "refined"),
        "flag_infusable":       _flag(flags, "infusable"),
        "flag_indestructible":  _flag(flags, "indestructible"),
        "modifiers_json":       json.dumps(item.get("modifiers") or {}),
        "typeinfo_json":        json.dumps(typeinfo),
        "effect_list_json":     json.dumps(item.get("effect_list") or []),
        "adornment_slots_json": json.dumps(item.get("adornmentslot_list") or []),
        "adornment_list_json":  json.dumps(item.get("adornment_list") or []),
        "classification_json":  json.dumps(item.get("classification_list") or []),
        "slot_list_json":       json.dumps(slot_list),
        "setbonus_list_json":   json.dumps(item.get("setbonus_list") or []),
        "flags_json":           json.dumps(flags),
        "raw_json":             json.dumps(item),
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


def upsert_items(items: list[dict], conn: sqlite3.Connection) -> int:
    """Upsert a batch of raw Census item dicts. Returns number inserted/replaced."""
    rows = [item_to_row(item) for item in items]
    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()
    return len(rows)


def item_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]


# ---------------------------------------------------------------------------
# Async helpers (used by bot)
# ---------------------------------------------------------------------------

async def find_by_name(name: str, path: Path = DB_PATH) -> Optional[dict]:
    """Return raw Census JSON dict for the closest name match, or None."""
    try:
        import aiosqlite
    except ImportError:
        return _find_by_name_sync(name, path)

    if not path.exists():
        return None
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        # Exact match first
        async with db.execute(
            "SELECT raw_json FROM items WHERE displayname_lower = ? LIMIT 1",
            (name.lower(),),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return json.loads(row["raw_json"])
        # LIKE fallback
        async with db.execute(
            "SELECT raw_json FROM items WHERE displayname_lower LIKE ? LIMIT 1",
            (f"%{name.lower()}%",),
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row["raw_json"]) if row else None


async def find_by_id(item_id: int, path: Path = DB_PATH) -> Optional[dict]:
    """Return raw Census JSON dict for the given item ID, or None."""
    try:
        import aiosqlite
    except ImportError:
        return _find_by_id_sync(item_id, path)

    if not path.exists():
        return None
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT raw_json FROM items WHERE id = ? LIMIT 1", (item_id,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row["raw_json"]) if row else None


def _find_by_name_sync(name: str, path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT raw_json FROM items WHERE displayname_lower = ? LIMIT 1",
            (name.lower(),),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT raw_json FROM items WHERE displayname_lower LIKE ? LIMIT 1",
                (f"%{name.lower()}%",),
            ).fetchone()
        return json.loads(row["raw_json"]) if row else None


def _find_by_id_sync(item_id: int, path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT raw_json FROM items WHERE id = ? LIMIT 1", (item_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None
