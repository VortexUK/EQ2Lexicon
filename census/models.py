from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ItemStat:
    name: str           # Raw Census API name
    display_name: str   # Human-readable name
    value: float
    stat_group: str     # 'primary' or 'secondary'


@dataclass
class ItemEffect:
    name: str
    trigger: str                    # e.g. "When Equipped:"
    lines: list[tuple[int, str]]    # (indentation_level, text)


@dataclass
class SpellEntry:
    name: str
    tier: str
    spell_type: str   # 'spells' or 'arts'
    level: int


@dataclass
class CharacterSpells:
    character_name: str
    entries: list[SpellEntry]


@dataclass
class GuildMember:
    name: str
    level: Optional[int]
    cls: Optional[str]       # adventurer class
    ts_class: Optional[str]  # tradeskill class
    ts_level: Optional[int]
    aa_level: Optional[int]
    deity: Optional[str]
    rank: Optional[str]
    rank_id: Optional[int]   # numeric rank for sort order


@dataclass
class GuildData:
    name: str
    world: str
    members: list[GuildMember]


@dataclass
class ItemData:
    id: str
    name: str
    quality: str                    # fabled, legendary, treasured, uncommon, common
    description: str
    icon_id: Optional[str]
    icon_bytes: Optional[bytes]     # Raw PNG/image bytes for the icon
    slot_type: str                  # Head, Chest, etc.
    armor_type: str                 # Leather Armor, Plate Armor, etc.
    mitigation: Optional[int]
    item_level: Optional[int]
    required_level: Optional[int]
    classes: list[str]
    stats: list[ItemStat] = field(default_factory=list)
    effects: list[ItemEffect] = field(default_factory=list)
    adornment_slots: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    game_link: Optional[str] = None
    container_slots: Optional[int] = None
    extra_info: list[tuple[str, str]] = field(default_factory=list)  # (label, value) rows


@dataclass
class EquipmentSlot:
    slot_name: str
    item_name: str
    item_id: Optional[str] = None
    icon_id: Optional[str] = None
    tier: Optional[str] = None      # FABLED, LEGENDARY, etc.


@dataclass
class CharacterOverview:
    id: str
    name: str
    level: Optional[int]
    cls: Optional[str]              # adventurer class
    race: Optional[str]
    gender: Optional[str]
    deity: Optional[str]
    aa_count: int
    world: str
    ts_class: Optional[str] = None  # tradeskill class
    ts_level: Optional[int] = None
    stats: dict = field(default_factory=dict)
    equipment: list[EquipmentSlot] = field(default_factory=list)


@dataclass
class NodeAA:
    node_id: int
    tree_id: int
    tier: int


@dataclass
class CharacterAAs:
    character_name: str
    aa_list: list[NodeAA]

    def for_tree(self, tree_id: int) -> dict[int, int]:
        """Return {node_id: tier} for all nodes in the given tree."""
        return {aa.node_id: aa.tier for aa in self.aa_list if aa.tree_id == tree_id}

    @property
    def tree_ids(self) -> set[int]:
        return {aa.tree_id for aa in self.aa_list}
