from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ItemStat:
    name: str  # Raw Census API name
    display_name: str  # Human-readable name
    value: float
    stat_group: str  # 'primary' or 'secondary'


@dataclass
class ItemEffect:
    name: str
    trigger: str  # e.g. "When Equipped:"
    lines: list[tuple[int, str]]  # (indentation_level, text)


@dataclass
class SpellEntry:
    name: str
    tier: str
    spell_type: str  # 'spells' or 'arts'
    level: int


@dataclass
class CharacterSpells:
    character_name: str
    entries: list[SpellEntry]


@dataclass
class GuildMember:
    name: str
    level: int | None
    cls: str | None  # adventurer class
    ts_class: str | None  # tradeskill class
    ts_level: int | None
    aa_level: int | None
    deity: str | None
    rank: str | None
    rank_id: int | None  # numeric rank for sort order
    guild_status: int | None = None  # status points contributed to the guild
    played_time: int | None = None  # total /played seconds


@dataclass
class GuildData:
    name: str
    world: str
    members: list[GuildMember]


@dataclass
class SetBonusEntry:
    required_items: int
    effect: str  # "Applies Focus: ..." header line
    lines: list[str]  # descriptiontag_1, descriptiontag_2, …


@dataclass
class RecipeBookEntry:
    """One recipe taught by a recipe-book item (from typeinfo.recipe_list)."""

    id: str
    name: str


@dataclass
class ItemData:
    id: str
    name: str
    quality: str  # fabled, legendary, treasured, uncommon, common
    description: str
    icon_id: str | None
    icon_bytes: bytes | None  # Raw PNG/image bytes for the icon
    slot_type: str  # Head, Chest, etc.
    armor_type: str  # Leather Armor, Plate Armor, etc.
    mitigation: int | None
    item_level: int | None
    required_level: int | None
    classes: list[str]
    ilvl: float | None = None  # WoW-style item level; None for non-gear
    stats: list[ItemStat] = field(default_factory=list)
    effects: list[ItemEffect] = field(default_factory=list)
    adornment_slots: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    game_link: str | None = None
    container_slots: int | None = None
    extra_info: list[tuple[str, str]] = field(default_factory=list)  # (label, value) rows
    set_name: str | None = None
    set_bonuses: list[SetBonusEntry] = field(default_factory=list)
    # Recipe-book items (typeinfo.name == "recipescroll") list the recipes they
    # teach in typeinfo.recipe_list; empty for every other item type.
    recipe_list: list[RecipeBookEntry] = field(default_factory=list)


@dataclass
class AdornSlot:
    color: str  # "White", "Yellow", "Red", etc.
    adorn_name: str | None = None  # None = empty slot
    adorn_id: str | None = None  # item DB id for tooltip lookup


@dataclass
class EquipmentSlot:
    slot_name: str
    item_name: str
    item_id: str | None = None
    icon_id: str | None = None
    tier: str | None = None  # FABLED, LEGENDARY, etc.
    adorn_slots: list = field(default_factory=list)  # list[AdornSlot]


@dataclass
class GearSet:
    """One saved in-game equipment set from the adventure_sets collection.
    ``name`` is the player-given label ("DPS", "Tank", ...)."""

    name: str
    equipment: list = field(default_factory=list)  # list[EquipmentSlot]


@dataclass
class CharacterOverview:
    id: str
    name: str
    level: int | None
    cls: str | None  # adventurer class
    race: str | None
    gender: str | None
    deity: str | None
    aa_count: int
    world: str
    ts_class: str | None = None  # tradeskill class
    ts_level: int | None = None
    guild_name: str | None = None  # guild the character belongs to (None = no guild)
    stats: dict = field(default_factory=dict)
    equipment: list[EquipmentSlot] = field(default_factory=list)
    spell_ids: list[int] = field(default_factory=list)  # raw spell IDs from Census


@dataclass
class NodeAA:
    node_id: int
    tree_id: int
    tier: int


@dataclass
class AAProfile:
    name: str
    aa_list: list[NodeAA]


@dataclass
class CharacterAAs:
    character_name: str
    aa_list: list[NodeAA]
    profiles: list[AAProfile] = field(default_factory=list)

    def for_tree(self, tree_id: int) -> dict[int, int]:
        """Return {node_id: tier} for all nodes in the given tree."""
        return {aa.node_id: aa.tier for aa in self.aa_list if aa.tree_id == tree_id}

    @property
    def tree_ids(self) -> set[int]:
        return {aa.tree_id for aa in self.aa_list}
