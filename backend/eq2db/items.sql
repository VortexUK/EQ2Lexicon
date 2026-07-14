-- SQL for backend/eq2db/items.py. Schema + DML both live here. The shared
-- `_meta` table is created from backend/eq2db/_meta.sql via _meta.create_table().

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_items
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

-- :name schema_item_stats
-- One row per item × canonical stat name.
CREATE TABLE IF NOT EXISTS item_stats (
    item_id  INTEGER NOT NULL,
    stat     TEXT    NOT NULL,   -- canonical display name e.g. "Ability Mod"
    value    REAL    NOT NULL,
    PRIMARY KEY (item_id, stat)
);

-- Multi-statement block — init_db runs via conn.executescript().
-- :name indexes_items
CREATE INDEX IF NOT EXISTS idx_name        ON items(displayname_lower);
CREATE INDEX IF NOT EXISTS idx_tier        ON items(tier);
CREATE INDEX IF NOT EXISTS idx_typeid      ON items(typeid);
CREATE INDEX IF NOT EXISTS idx_level       ON items(level_to_use);
CREATE INDEX IF NOT EXISTS idx_item_level  ON items(item_level);
CREATE INDEX IF NOT EXISTS idx_slot        ON items(slot);
CREATE INDEX IF NOT EXISTS idx_icon        ON items(icon_id);
CREATE INDEX IF NOT EXISTS idx_last_update ON items(last_update);
CREATE INDEX IF NOT EXISTS idx_adorn_color ON items(adornment_color);
CREATE INDEX IF NOT EXISTS idx_visible     ON items(visible);
CREATE INDEX IF NOT EXISTS idx_ti_name     ON items(typeinfo_name);
CREATE INDEX IF NOT EXISTS idx_class_label ON items(class_label);
CREATE INDEX IF NOT EXISTS idx_skill_type  ON items(skill_type);
CREATE INDEX IF NOT EXISTS idx_tier_disp   ON items(tier_display);

-- :name indexes_item_stats
CREATE INDEX IF NOT EXISTS idx_stat_name  ON item_stats(stat);
CREATE INDEX IF NOT EXISTS idx_stat_item  ON item_stats(item_id);
CREATE INDEX IF NOT EXISTS idx_stat_nv    ON item_stats(stat, value);

-- Idempotent ALTER template. _MIGRATIONS lives in items.py (data, not SQL);
-- init_db loops it and formats this template per row.
-- :name migration_add_column
ALTER TABLE items ADD COLUMN {col} {coltype};

-- :name upsert
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
);

-- :name count
SELECT COUNT(*) FROM items;

-- :name backfill_classification_list
UPDATE items
SET classification_list = json_extract(raw_json, '$.classification_list')
WHERE classification_list IS NULL
  AND raw_json IS NOT NULL;

-- :name backfill_pvp_flag
UPDATE items
SET flag_pvp = 1
WHERE flag_pvp = 0
  AND raw_json IS NOT NULL
  AND LOWER(raw_json) LIKE '%pvp%';

-- :name select_raw_json_by_keyword
-- {conditions} is OR-joined per-keyword LIKE filters; sized at call time.
SELECT id, raw_json FROM items WHERE raw_json IS NOT NULL AND ({conditions});

-- :name insert_item_stat_ignore
INSERT OR IGNORE INTO item_stats (item_id, stat, value) VALUES (?, ?, ?);

-- :name insert_item_stat_replace
INSERT OR REPLACE INTO item_stats (item_id, stat, value) VALUES (?, ?, ?);

-- :name gear_for_ids
-- {placeholders} = comma-joined "?,?,..."
SELECT id, ilvl, wield_style, level_to_use, tier_display FROM items WHERE id IN ({placeholders});

-- :name find_by_id_raw_json
SELECT raw_json FROM items WHERE id = ? LIMIT 1;

-- :name stats_for_ids
-- {placeholders} = comma-joined "?,?,..."
SELECT item_id, stat, value FROM item_stats WHERE item_id IN ({placeholders});

-- :name set_bonus_rows_for_ids
-- {placeholders} = comma-joined "?,?,..."
SELECT id, setbonus_name, raw_json FROM items
WHERE id IN ({placeholders}) AND setbonus_name IS NOT NULL;

-- find_by_name composes one of these depending on SERVER_MAX_LEVEL +
-- exact-vs-LIKE. {where} is the column condition: 'displayname_lower = ?'
-- or 'displayname_lower LIKE ? ESCAPE \\'.

-- :name find_by_name_level_capped
SELECT raw_json FROM items WHERE {where}
  AND (level_to_use IS NULL OR level_to_use <= ?)
  ORDER BY level_to_use DESC, tierid DESC, last_update DESC LIMIT 1;

-- :name find_by_name_any_level
SELECT raw_json FROM items WHERE {where}
  ORDER BY level_to_use DESC, tierid DESC, last_update DESC LIMIT 1;

-- :name find_by_name_no_max_level
SELECT raw_json FROM items WHERE {where}  ORDER BY tierid DESC, last_update DESC LIMIT 1;
