"""Mappings for Census API field names → display names and groupings."""

# Maps lowercase Census API stat type names to (display_name, group)
# group is 'primary' (green) or 'secondary' (cyan)
STAT_MAP: dict[str, tuple[str, str]] = {
    # Primary attributes (green)
    "primaryattribute": ("Primary Attributes", "primary"),
    "primary_attribute": ("Primary Attributes", "primary"),
    "str": ("Strength", "primary"),
    "strength": ("Strength", "primary"),
    "sta": ("Stamina", "primary"),
    "stamina": ("Stamina", "primary"),
    "agi": ("Agility", "primary"),
    "agility": ("Agility", "primary"),
    "int": ("Intelligence", "primary"),
    "intelligence": ("Intelligence", "primary"),
    "wis": ("Wisdom", "primary"),
    "wisdom": ("Wisdom", "primary"),
    "combatskill": ("Combat Skills", "primary"),
    "combat_skill": ("Combat Skills", "primary"),
    "combatskills": ("Combat Skills", "primary"),
    # Secondary stats (cyan)
    "critbonus": ("Crit Bonus", "secondary"),
    "crit_bonus": ("Crit Bonus", "secondary"),
    "castingspeed": ("Casting Speed", "secondary"),
    "casting_speed": ("Casting Speed", "secondary"),
    "critchance": ("Crit Chance", "secondary"),
    "crit_chance": ("Crit Chance", "secondary"),
    "potency": ("Potency", "secondary"),
    "maxhealth": ("Max Health", "secondary"),
    "max_health": ("Max Health", "secondary"),
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
}

# EQ2 class groups — used to collapse full class lists into group names
_FIGHTERS = frozenset(["Guardian", "Berserker", "Monk", "Bruiser", "Shadowknight", "Paladin"])
_PRIESTS = frozenset(["Templar", "Inquisitor", "Fury", "Warden", "Mystic", "Defiler"])
_SCOUTS = frozenset(["Troubador", "Dirge", "Assassin", "Ranger", "Swashbuckler", "Brigand"])
_MAGES = frozenset(["Coercer", "Illusionist", "Conjuror", "Necromancer", "Wizard", "Warlock"])

ALL_CLASSES: frozenset[str] = _FIGHTERS | _PRIESTS | _SCOUTS | _MAGES

CLASS_GROUPS: dict[frozenset, str] = {
    ALL_CLASSES: "All Classes",
    _FIGHTERS: "All Fighters",
    frozenset(["Guardian", "Berserker"]): "All Warriors",
    frozenset(["Monk", "Bruiser"]): "All Brawlers",
    frozenset(["Shadowknight", "Paladin"]): "All Crusaders",
    _PRIESTS: "All Priests",
    frozenset(["Templar", "Inquisitor"]): "All Clerics",
    frozenset(["Fury", "Warden"]): "All Druids",
    frozenset(["Mystic", "Defiler"]): "All Shamans",
    _SCOUTS: "All Scouts",
    frozenset(["Troubador", "Dirge"]): "All Bards",
    frozenset(["Assassin", "Ranger"]): "All Predators",
    frozenset(["Swashbuckler", "Brigand"]): "All Rogues",
    _MAGES: "All Mages",
    frozenset(["Coercer", "Illusionist"]): "All Enchanters",
    frozenset(["Conjuror", "Necromancer"]): "All Summoners",
    frozenset(["Wizard", "Warlock"]): "All Sorcerers",
}

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
