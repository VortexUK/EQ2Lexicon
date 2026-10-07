create schema if not exists items;
set search_path to items, public;

-- Reviewed translation of backend/eq2db/items.sql's schema blocks (Phase 2:
-- the read-only catalogue mirrors move off the Railway volume).
-- items.id is CENSUS-ASSIGNED (ids exceed int4 — max observed 4,294,964,656)
-- — plain bigint PK, no identity. SQLite REAL columns become double
-- precision (SQLite REAL is 8-byte). 0/1 flags stay integers per the type
-- policy. classification_list existed only as a SQLite ALTER migration —
-- here it is a real column. raw_json stays text on purpose: ~1.38 GB of
-- payload nothing queries into at runtime (build-time backfills cast
-- raw_json::jsonb themselves).

CREATE TABLE _meta (
    key   text PRIMARY KEY,
    value text
);

CREATE TABLE items (
    -- Identity
    id                   bigint PRIMARY KEY,
    displayname          text NOT NULL,
    displayname_lower    text NOT NULL,
    gamelink             text,
    description          text,
    last_update          bigint,

    -- Quality / classification
    tier                 text,
    tierid               integer,
    type                 text,
    typeid               integer,
    item_level           integer,
    level_to_use         integer,
    planar_level         integer,
    ilvl                 double precision,  -- WoW-style item level; NULL for non-gear
    icon_id              bigint,
    max_stack_size       integer,

    -- Primary slot (slot_list[0].name for quick filtering)
    slot                 text,

    -- Armor
    armor_class_min      integer,
    armor_class_max      integer,

    -- Weapon (from typeinfo)
    damage_min           integer,
    damage_max           integer,
    damage_base          integer,
    damage_type          text,
    damage_type_id       integer,
    damage_rating        double precision,
    delay                double precision,
    wield_style          text,

    -- Spell scroll / ability (from typeinfo)
    spell_name           text,
    spell_tier_id        integer,
    spell_cast_time      double precision,
    spell_recast_time    double precision,
    spell_duration       double precision,

    -- Ranged weapon
    weapon_range_min     double precision,
    weapon_range_max     double precision,

    -- Food / drink / consumable (from typeinfo)
    food_duration        text,
    food_satiation       text,
    food_level           integer,

    -- Adornment (from typeinfo)
    adornment_color      text,

    -- Container / house item (from typeinfo)
    container_slots      integer,
    status_reduction     bigint,

    -- Charges
    max_charges          integer,    -- -1 = unlimited

    -- Requirements
    required_skill_name  text,
    required_skill_min   integer,

    -- Set bonus
    setbonus_name        text,

    -- Unique equipment group (prestige slot-limit sets)
    unique_equip_group          text,
    unique_equip_wearable_count integer,
    unique_equip_prestige       integer DEFAULT 0,

    -- Quest links (census ids — bigint like items.id)
    associated_quest     bigint,
    autoquest            bigint,

    -- Discovery (first seen on any world; epoch)
    first_discovered     bigint,

    -- Visibility (0 = hidden/disabled item, 1 = normal)
    visible              integer DEFAULT 1,

    -- Typeinfo summary columns (queryable without parsing raw_json)
    typeinfo_name                text,     -- e.g. "Armor", "Weapon", "Spell Scroll"
    classes_json                 text,     -- JSON array/object of allowed classes
    physical_damage_absorption   integer,  -- armour mitigation value

    -- Pre-computed class label and count (derived from classes_json)
    class_label          text,             -- e.g. "All Classes", "All Priests", "Guardian"
    class_count          integer,

    -- Resolved tier name: tier if set, otherwise 'COMMON' for tierid 0/1/2
    tier_display         text,

    -- Armor proficiency / spell scroll extras (from typeinfo)
    skill_type           text,             -- e.g. "heavyarmor", "magicaffinity"
    spell_target         text,             -- e.g. "Enemy", "Caster", "Group (AE)"
    spell_range          text,             -- e.g. "Up to 25.0 meters"
    spell_power_cost     integer,
    spell_resistability  text,             -- e.g. "25.2% Easier", "na"

    -- Common flags as fast-filter booleans
    flag_heirloom        integer DEFAULT 0,
    flag_lore            integer DEFAULT 0,
    flag_lore_equip      integer DEFAULT 0,
    flag_no_trade        integer DEFAULT 0,
    flag_no_value        integer DEFAULT 0,
    flag_no_zone         integer DEFAULT 0,
    flag_prestige        integer DEFAULT 0,
    flag_relic           integer DEFAULT 0,
    flag_attunable       integer DEFAULT 0,
    flag_ornate          integer DEFAULT 0,
    flag_refined         integer DEFAULT 0,
    flag_infusable       integer DEFAULT 0,
    flag_indestructible  integer DEFAULT 0,
    flag_pvp             integer DEFAULT 0,  -- 1 = PvP item (pvp stats or pvp effect text)

    -- JSON array of census classification nodes (was a SQLite ALTER-only column)
    classification_list  text,

    -- Full raw Census JSON — used by _parse_item(); all nested data lives here
    raw_json             text
);

-- One row per item × canonical stat name.
CREATE TABLE item_stats (
    item_id  bigint NOT NULL,
    stat     text   NOT NULL,   -- canonical display name e.g. "Ability Mod"
    value    double precision NOT NULL,
    PRIMARY KEY (item_id, stat)
);

CREATE INDEX idx_items_name        ON items (displayname_lower);
CREATE INDEX idx_items_tier        ON items (tier);
CREATE INDEX idx_items_typeid      ON items (typeid);
CREATE INDEX idx_items_level       ON items (level_to_use);
CREATE INDEX idx_items_item_level  ON items (item_level);
CREATE INDEX idx_items_slot        ON items (slot);
CREATE INDEX idx_items_icon        ON items (icon_id);
CREATE INDEX idx_items_last_update ON items (last_update);
CREATE INDEX idx_items_adorn_color ON items (adornment_color);
CREATE INDEX idx_items_visible     ON items (visible);
CREATE INDEX idx_items_ti_name     ON items (typeinfo_name);
CREATE INDEX idx_items_class_label ON items (class_label);
CREATE INDEX idx_items_skill_type  ON items (skill_type);
CREATE INDEX idx_items_tier_disp   ON items (tier_display);

CREATE INDEX idx_item_stats_name ON item_stats (stat);
CREATE INDEX idx_item_stats_item ON item_stats (item_id);
CREATE INDEX idx_item_stats_nv   ON item_stats (stat, value);

-- seeds
-- (none — read-only census-catalogue mirror; marker kept for the
-- scratch-schema leaser's uniform reset contract)
