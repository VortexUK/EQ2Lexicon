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
class GuildMember:
    name: str
    level: Optional[int]
    cls: Optional[str]       # adventurer class
    ts_class: Optional[str]  # tradeskill class
    ts_level: Optional[int]
    aa_level: Optional[int]
    deity: Optional[str]
    rank: Optional[str]


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
