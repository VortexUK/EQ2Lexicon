"""Mappings for Census API field names → display names and groupings."""

from backend.eq2db.classes import CLASS_SEED

# Maps lowercase Census API stat type names to (display_name, group)
# group is 'primary' (green) or 'secondary' (cyan)
STAT_MAP: dict[str, tuple[str, str]] = {
    # Primary attributes (green)
    "primaryattribute": ("Primary Attributes", "primary"),
    "primary_attribute": ("Primary Attributes", "primary"),
    # In modern EQ2 the `strength` XML tag represents the combined Primary Attributes stat
    "str": ("Primary Attributes", "primary"),
    "strength": ("Primary Attributes", "primary"),
    "sta": ("Stamina", "primary"),
    "stamina": ("Stamina", "primary"),
    "agi": ("Primary Attributes", "primary"),
    "agility": ("Primary Attributes", "primary"),
    "int": ("Primary Attributes", "primary"),
    "intelligence": ("Primary Attributes", "primary"),
    "wis": ("Primary Attributes", "primary"),
    "wisdom": ("Primary Attributes", "primary"),
    "combatskill": ("Combat Skills", "primary"),
    "combat_skill": ("Combat Skills", "primary"),
    "combatskills": ("Combat Skills", "primary"),
    # Secondary stats (cyan)
    "critbonus": ("Crit Bonus", "secondary"),
    "crit_bonus": ("Crit Bonus", "secondary"),
    "castingspeed": ("Casting Speed", "secondary"),
    "casting_speed": ("Casting Speed", "secondary"),
    "spelltimecastpct": ("Casting Speed", "secondary"),  # XML tag name from API
    "critchance": ("Crit Chance", "secondary"),
    "crit_chance": ("Crit Chance", "secondary"),
    "potency": ("Potency", "secondary"),
    "basemodifier": ("Potency", "secondary"),  # XML tag name from API
    "maxhealth": ("Max Health", "secondary"),
    "max_health": ("Max Health", "secondary"),
    "maxhpperc": ("Max Health", "secondary"),  # XML tag name from API
    "maxpower": ("Max Power", "secondary"),
    "max_power": ("Max Power", "secondary"),
    "abilitymod": ("Ability Mod", "secondary"),
    "ability_mod": ("Ability Mod", "secondary"),
    "haste": ("Haste", "secondary"),
    "dps": ("DPS", "secondary"),
    "multiatk": ("Multi Attack", "secondary"),
    "multi_attack": ("Multi Attack", "secondary"),
    "multi_atk": ("Multi Attack", "secondary"),
    "strikethrough": ("Strikethrough", "secondary"),
    "accuracy": ("Accuracy", "secondary"),
    "flurry": ("Flurry", "secondary"),
    "block": ("Block", "secondary"),
    "blockchance": ("Block Chance", "secondary"),
    "block_chance": ("Block Chance", "secondary"),
    "parry": ("Parry", "secondary"),
    "deflection": ("Deflection", "secondary"),
    "dodge": ("Dodge", "secondary"),
    "spellaversion": ("Spell Aversion", "secondary"),
    "spell_aversion": ("Spell Aversion", "secondary"),
    "criticalavoidance": ("Critical Avoidance", "secondary"),
    "critical_avoidance": ("Critical Avoidance", "secondary"),
    "overcap": ("Overcap Bonus", "secondary"),
    "weaponskill": ("Weapon Skill", "secondary"),
    "weapon_skill": ("Weapon Skill", "secondary"),
    "aeautoatk": ("AE Auto Attack", "secondary"),
    "ae_auto_atk": ("AE Auto Attack", "secondary"),
    "aeautoattack": ("AE Auto Attack", "secondary"),
    "spelldmgbonus": ("Spell Dmg Bonus", "secondary"),
    "spell_dmg_bonus": ("Spell Dmg Bonus", "secondary"),
    "attackspeed": ("Attack Speed", "secondary"),
    "attack_speed": ("Attack Speed", "secondary"),
    "arcane": ("Resistances", "primary"),
    "elemental": ("Resistances", "primary"),
    "noxious": ("Resistances", "primary"),
}

# EQ2 class groups — used to collapse full class lists into group names.
# Derived from CLASS_SEED (the single source of class→archetype membership).
_CLASSES_BY_ARCHETYPE: dict[str, frozenset[str]] = {
    archetype: frozenset(c.name for c in CLASS_SEED if c.archetype == archetype)
    for archetype in ("Fighter", "Priest", "Scout", "Mage")
}

FIGHTERS = _CLASSES_BY_ARCHETYPE["Fighter"]
PRIESTS = _CLASSES_BY_ARCHETYPE["Priest"]
SCOUTS = _CLASSES_BY_ARCHETYPE["Scout"]
MAGES = _CLASSES_BY_ARCHETYPE["Mage"]
ARTISANS = frozenset(
    ["Sage", "Armorer", "Weaponsmith", "Woodworker", "Jeweler", "Carpenter", "Tailor", "Alchemist", "Provisioner"]
)

# Canonical archetype colours — used by image/tooltip.py, census/classes_db.py, and any
# future renderer that needs to tint class icons.  Both the PIL tooltip renderer and
# classes_db mirror these values; classes_db cannot import them directly (circular: this
# module already imports CLASS_SEED from classes_db) so the values are intentionally kept
# in sync via this comment.  #f87171 Fighter · #4ade80 Priest · #fbbf24 Scout · #93b4ff Mage
CLASS_ARCHETYPE_COLOURS: dict[str, str] = {
    "Fighter": "#f87171",
    "Priest": "#4ade80",
    "Scout": "#fbbf24",
    "Mage": "#93b4ff",
}

# Ordered list used for archetype decomposition (most specific → least specific)
ARCHETYPES: list[tuple[frozenset, str]] = [
    (FIGHTERS, "All Fighters"),
    (PRIESTS, "All Priests"),
    (SCOUTS, "All Scouts"),
    (MAGES, "All Mages"),
    (ARTISANS, "All Artisans"),
]

ALL_CLASSES: frozenset[str] = FIGHTERS | PRIESTS | SCOUTS | MAGES
ALL_WITH_ARTISANS: frozenset[str] = ALL_CLASSES | ARTISANS

CLASS_GROUPS: dict[frozenset, str] = {
    ALL_WITH_ARTISANS: "All Classes",
    ALL_CLASSES: "All Classes",
    FIGHTERS: "All Fighters",
    frozenset(["Guardian", "Berserker"]): "All Warriors",
    frozenset(["Monk", "Bruiser"]): "All Brawlers",
    frozenset(["Shadowknight", "Paladin"]): "All Crusaders",
    PRIESTS: "All Priests",
    frozenset(["Templar", "Inquisitor"]): "All Clerics",
    frozenset(["Fury", "Warden"]): "All Druids",
    frozenset(["Mystic", "Defiler"]): "All Shamans",
    SCOUTS: "All Scouts",
    frozenset(["Troubador", "Dirge"]): "All Bards",
    frozenset(["Assassin", "Ranger"]): "All Predators",
    frozenset(["Swashbuckler", "Brigand"]): "All Rogues",
    MAGES: "All Mages",
    frozenset(["Coercer", "Illusionist"]): "All Enchanters",
    frozenset(["Conjuror", "Necromancer"]): "All Summoners",
    frozenset(["Wizard", "Warlock"]): "All Sorcerers",
    ARTISANS: "All Artisans",
}

# typeinfo fields to render as info rows.
# "duration" values are formatted as "X sec / X min / X hr".
# Add new entries here to display additional typeinfo fields.
TYPEINFO_DISPLAY: list[tuple[str, str, str]] = [
    # (api_field_in_typeinfo, display_label, format)  — format: "duration" | "str"
    # Spell scroll / ability (Census API stores these with "spell" prefix in typeinfo)
    ("spelltarget", "Target", "str"),
    ("spellrange", "Range", "str"),
    ("spellcasttime", "Casting", "duration"),
    ("spellrecasttime", "Recast", "duration"),
    ("spellduration", "Duration", "duration"),
    # Food / drink duration (pre-formatted string like "6 minutes")
    ("duration", "Duration", "duration"),
    # Other items (legacy field names — kept for backward compat; usually no-ops)
    ("casttime", "Casting", "duration"),
    ("recasttime", "Recast", "duration"),
]

# Top-level item fields to render as info rows.
ITEM_DISPLAY: list[tuple[str, str, str]] = [
    ("maxcharges", "Charges", "charges"),
]

# Spell tier order, lowest → highest
SPELL_TIER_ORDER: list[str] = ["Apprentice", "Journeyman", "Adept", "Expert", "Master", "Grandmaster"]

# Flag field names → display labels (order matters for output)
FLAG_FIELDS: list[tuple[str, str]] = [
    ("is_heirloom", "HEIRLOOM"),
    ("heirloom", "HEIRLOOM"),
    ("is_lore_equip", "LORE-EQUIP"),
    ("lore_equip", "LORE-EQUIP"),
    ("is_lore", "LORE"),
    ("lore", "LORE"),
    ("is_attuneable", "ATTUNEABLE"),
    ("attuneable", "ATTUNEABLE"),
    ("is_no_trade", "NO-TRADE"),
    ("no_trade", "NO-TRADE"),
    ("is_no_zone", "NO-ZONE"),
    ("no_zone", "NO-ZONE"),
    ("is_no_value", "NO-VALUE"),
    ("no_value", "NO-VALUE"),
]
