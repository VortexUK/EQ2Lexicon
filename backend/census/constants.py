"""Mappings for Census API field names → display names and groupings.

Class-group membership and archetype colours are OWNED by the committed
classes.db (read via backend.eq2db.classes.catalogue at this module's import,
so a broken classes.db still fails fast at process start). Anything defined
here that names classes is derived from that source. Don't redefine class
groupings or colours here — edit the row in classes.db and commit the file.
"""

from backend.eq2db.classes import catalogue as _classes

_SEED_ARCHETYPE_COLOURS = _classes.archetype_colours()
ARCHETYPE_GROUPS = _classes.archetype_groups()
CRAFTER_NAMES = _classes.crafter_names()
SUBCLASS_GROUPS = _classes.subclass_groups()

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

# EQ2 class groups — derived from classes.db rows (single source of truth).
# Public surface kept stable for back-compat with image/tooltip.py,
# server/api/item.py, scripts/build_recipe_classes.py.
_BY_ARCHETYPE: dict[str, frozenset[str]] = dict(ARCHETYPE_GROUPS)
_BY_SUBCLASS: dict[str, frozenset[str]] = dict(SUBCLASS_GROUPS)

FIGHTERS: frozenset[str] = _BY_ARCHETYPE["Fighter"]
PRIESTS: frozenset[str] = _BY_ARCHETYPE["Priest"]
SCOUTS: frozenset[str] = _BY_ARCHETYPE["Scout"]
MAGES: frozenset[str] = _BY_ARCHETYPE["Mage"]
ARTISANS: frozenset[str] = CRAFTER_NAMES

# Canonical archetype colours. Sourced from classes.ARCHETYPE_COLOURS; this is
# just a back-compat alias for callers that historically imported
# CLASS_ARCHETYPE_COLOURS from census.constants.
CLASS_ARCHETYPE_COLOURS: dict[str, str] = _SEED_ARCHETYPE_COLOURS

# Ordered list used for archetype decomposition (most general → most specific).
# Full archetypes are listed first so a complete-archetype class set is
# consumed before subclass groups are tested.
ARCHETYPES: list[tuple[frozenset, str]] = (
    [(_BY_ARCHETYPE[name], f"All {name}s") for name in ("Fighter", "Priest", "Scout", "Mage")]
    + [(ARTISANS, "All Artisans")]
    + [(_BY_SUBCLASS[sub], f"All {sub}s") for sub, _ in SUBCLASS_GROUPS]
)

ALL_CLASSES: frozenset[str] = FIGHTERS | PRIESTS | SCOUTS | MAGES
ALL_WITH_ARTISANS: frozenset[str] = ALL_CLASSES | ARTISANS

# Exact-match dict used by tooltip / item-route renderers. Built from the same
# DB-derived groups above so renaming a subclass in classes.db
# propagates here without any hand-edit.
CLASS_GROUPS: dict[frozenset, str] = {
    ALL_WITH_ARTISANS: "All Classes",
    ALL_CLASSES: "All Classes",
    **{members: f"All {arch}s" for arch, members in ARCHETYPE_GROUPS},
    **{members: f"All {sub}s" for sub, members in SUBCLASS_GROUPS},
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
