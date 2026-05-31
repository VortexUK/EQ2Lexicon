-- SQL queries for backend/eq2db/items.py.
-- DDL (CREATE TABLE / INDEX, idempotent ALTER migrations) stays in items.py.

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
