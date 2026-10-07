-- SQL for backend/eq2db/items.py (Postgres `items` schema; unqualified —
-- the catalogue's connections set search_path). Schema DDL lives in
-- db/migrations/0007_items.sql; this file is DML only. item_to_row computes
-- flag_pvp / classification_list / effect stats at write time.

-- :name upsert
INSERT INTO items (
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
    %(id)s, %(displayname)s, %(displayname_lower)s, %(gamelink)s, %(description)s, %(last_update)s,
    %(tier)s, %(tierid)s, %(type)s, %(typeid)s, %(item_level)s, %(level_to_use)s, %(planar_level)s, %(ilvl)s, %(icon_id)s, %(max_stack_size)s,
    %(slot)s,
    %(armor_class_min)s, %(armor_class_max)s,
    %(damage_min)s, %(damage_max)s, %(damage_base)s, %(damage_type)s, %(damage_type_id)s, %(damage_rating)s, %(delay)s, %(wield_style)s,
    %(weapon_range_min)s, %(weapon_range_max)s,
    %(spell_name)s, %(spell_tier_id)s, %(spell_cast_time)s, %(spell_recast_time)s, %(spell_duration)s,
    %(food_duration)s, %(food_satiation)s, %(food_level)s,
    %(adornment_color)s,
    %(container_slots)s, %(status_reduction)s,
    %(max_charges)s,
    %(setbonus_name)s,
    %(unique_equip_group)s, %(unique_equip_wearable_count)s, %(unique_equip_prestige)s,
    %(required_skill_name)s, %(required_skill_min)s,
    %(associated_quest)s, %(autoquest)s, %(first_discovered)s,
    %(visible)s, %(typeinfo_name)s, %(classes_json)s, %(physical_damage_absorption)s,
    %(class_label)s, %(class_count)s,
    %(tier_display)s,
    %(skill_type)s, %(spell_target)s, %(spell_range)s, %(spell_power_cost)s, %(spell_resistability)s,
    %(flag_heirloom)s, %(flag_lore)s, %(flag_lore_equip)s, %(flag_no_trade)s, %(flag_no_value)s,
    %(flag_no_zone)s, %(flag_prestige)s, %(flag_relic)s, %(flag_attunable)s, %(flag_ornate)s,
    %(flag_refined)s, %(flag_infusable)s, %(flag_indestructible)s, %(flag_pvp)s,
    %(raw_json)s, %(classification_list)s
)
ON CONFLICT (id) DO UPDATE SET
    displayname = excluded.displayname, displayname_lower = excluded.displayname_lower,
    gamelink = excluded.gamelink, description = excluded.description, last_update = excluded.last_update,
    tier = excluded.tier, tierid = excluded.tierid, type = excluded.type, typeid = excluded.typeid,
    item_level = excluded.item_level, level_to_use = excluded.level_to_use,
    planar_level = excluded.planar_level, ilvl = excluded.ilvl,
    icon_id = excluded.icon_id, max_stack_size = excluded.max_stack_size,
    slot = excluded.slot,
    armor_class_min = excluded.armor_class_min, armor_class_max = excluded.armor_class_max,
    damage_min = excluded.damage_min, damage_max = excluded.damage_max, damage_base = excluded.damage_base,
    damage_type = excluded.damage_type, damage_type_id = excluded.damage_type_id,
    damage_rating = excluded.damage_rating, delay = excluded.delay, wield_style = excluded.wield_style,
    weapon_range_min = excluded.weapon_range_min, weapon_range_max = excluded.weapon_range_max,
    spell_name = excluded.spell_name, spell_tier_id = excluded.spell_tier_id,
    spell_cast_time = excluded.spell_cast_time, spell_recast_time = excluded.spell_recast_time,
    spell_duration = excluded.spell_duration,
    food_duration = excluded.food_duration, food_satiation = excluded.food_satiation,
    food_level = excluded.food_level,
    adornment_color = excluded.adornment_color,
    container_slots = excluded.container_slots, status_reduction = excluded.status_reduction,
    max_charges = excluded.max_charges,
    setbonus_name = excluded.setbonus_name,
    unique_equip_group = excluded.unique_equip_group,
    unique_equip_wearable_count = excluded.unique_equip_wearable_count,
    unique_equip_prestige = excluded.unique_equip_prestige,
    required_skill_name = excluded.required_skill_name, required_skill_min = excluded.required_skill_min,
    associated_quest = excluded.associated_quest, autoquest = excluded.autoquest,
    first_discovered = excluded.first_discovered,
    visible = excluded.visible, typeinfo_name = excluded.typeinfo_name,
    classes_json = excluded.classes_json,
    physical_damage_absorption = excluded.physical_damage_absorption,
    class_label = excluded.class_label, class_count = excluded.class_count,
    tier_display = excluded.tier_display,
    skill_type = excluded.skill_type, spell_target = excluded.spell_target,
    spell_range = excluded.spell_range, spell_power_cost = excluded.spell_power_cost,
    spell_resistability = excluded.spell_resistability,
    flag_heirloom = excluded.flag_heirloom, flag_lore = excluded.flag_lore,
    flag_lore_equip = excluded.flag_lore_equip, flag_no_trade = excluded.flag_no_trade,
    flag_no_value = excluded.flag_no_value, flag_no_zone = excluded.flag_no_zone,
    flag_prestige = excluded.flag_prestige, flag_relic = excluded.flag_relic,
    flag_attunable = excluded.flag_attunable, flag_ornate = excluded.flag_ornate,
    flag_refined = excluded.flag_refined, flag_infusable = excluded.flag_infusable,
    flag_indestructible = excluded.flag_indestructible, flag_pvp = excluded.flag_pvp,
    raw_json = excluded.raw_json, classification_list = excluded.classification_list;

-- :name count
SELECT COUNT(*) AS n FROM items;

-- :name insert_item_stat_ignore
-- Effect-derived stats: never overwrite a modifier-derived value.
INSERT INTO item_stats (item_id, stat, value) VALUES (%s, %s, %s)
ON CONFLICT (item_id, stat) DO NOTHING;

-- :name insert_item_stat_replace
-- Modifier-derived stats: always win.
INSERT INTO item_stats (item_id, stat, value) VALUES (%s, %s, %s)
ON CONFLICT (item_id, stat) DO UPDATE SET value = excluded.value;

-- :name gear_for_ids
SELECT id, ilvl, wield_style, level_to_use, tier_display FROM items WHERE id = ANY(%s);

-- :name find_by_id_raw_json
SELECT raw_json FROM items WHERE id = %s LIMIT 1;

-- :name stats_for_ids
SELECT item_id, stat, value FROM item_stats WHERE item_id = ANY(%s);

-- :name set_bonus_rows_for_ids
SELECT id, setbonus_name, raw_json FROM items
WHERE id = ANY(%s) AND setbonus_name IS NOT NULL;

-- find_by_name composes one of these depending on SERVER_MAX_LEVEL +
-- exact-vs-LIKE. {where} is the column condition: 'displayname_lower = %s'
-- or 'displayname_lower LIKE %s ESCAPE ''\'''. NULLS LAST puts
-- NULLs after real values for the nullable sort columns.

-- :name find_by_name_level_capped
SELECT raw_json FROM items WHERE {where}
  AND (level_to_use IS NULL OR level_to_use <= %s)
  ORDER BY level_to_use DESC NULLS LAST, tierid DESC NULLS LAST, last_update DESC NULLS LAST LIMIT 1;

-- :name find_by_name_any_level
SELECT raw_json FROM items WHERE {where}
  ORDER BY level_to_use DESC NULLS LAST, tierid DESC NULLS LAST, last_update DESC NULLS LAST LIMIT 1;

-- :name find_by_name_no_max_level
SELECT raw_json FROM items WHERE {where}
  ORDER BY tierid DESC NULLS LAST, last_update DESC NULLS LAST LIMIT 1;

-- :name spellscroll_names_for_class
-- Candidate spell names (with tier suffix) whose scroll MAY be scribable
-- by a class — the per-class spell universe (the spells schema has no
-- class column; the scroll's classes_json is the class linkage). LIKE is
-- a prefilter only (classes_json keys are lowercase, so case-sensitive
-- LIKE is correct): the caller re-checks classes_json, because legacy
-- all-class collection scrolls list every class with level 0.
-- Param: '%%"<cls_lower>"%%' built in Python.
SELECT DISTINCT spell_name, classes_json FROM items
WHERE typeinfo_name = 'spellscroll' AND spell_name IS NOT NULL AND classes_json LIKE %s;

-- :name raw_json_by_ids
-- The rotation simulator scans equipped items' effect lines for
-- base-damage bonuses.
SELECT id, raw_json FROM items WHERE id = ANY(%s);

-- :name spell_meta_by_names
-- Rotation simulator: spell duration and power cost exist ONLY on the
-- spellscroll item rows, keyed "<Spell Name> (<TierName>)" (spell_name ==
-- displayname there). raw_json carries the scroll's effect_list — the
-- properly SCALED damage text (the spells-schema spell-record text is
-- unscaled for some spells).
SELECT spell_name, spell_duration, spell_power_cost, raw_json FROM items
WHERE typeinfo_name = 'spellscroll' AND spell_name = ANY(%s);
