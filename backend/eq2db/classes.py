"""EQ2 adventure-class catalogue.

The 26 classes are static, so the canonical data lives here as CLASS_SEED and
the SQLite catalogue (data/classes/classes.db) is built from it by
scripts/build_classes_db.py (there's no Census download — unlike recipes/spells).
Keyed by class NAME: EQ2 has several unrelated class-id schemes (our icon_id is
the EQ2wire icon id; AA trees and Census type.classid use different ids), so
name is the only stable cross-reference.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClassInfo:
    name: str
    archetype: str  # Fighter | Priest | Scout | Mage
    subclass: str | None  # middle tier; None for Beastlord & Channeler
    role: str  # Tank | Healer | Melee DPS | Ranged DPS | Support
    colour: str  # hex (archetype colour)
    icon_id: int  # EQ2wire class_medium icon id


# Archetype colours. Public, canonical, ordered Fighter / Priest / Scout / Mage.
# Consumed via classes.ARCHETYPE_COLOURS by census/constants.py (re-exported as
# CLASS_ARCHETYPE_COLOURS for back-compat with older callers) and by any renderer
# that needs to tint class icons. Don't redefine these anywhere else.
ARCHETYPE_COLOURS: dict[str, str] = {
    "Fighter": "#f87171",
    "Priest": "#4ade80",
    "Scout": "#fbbf24",
    "Mage": "#93b4ff",
}
_F, _P, _S, _M = (
    ARCHETYPE_COLOURS["Fighter"],
    ARCHETYPE_COLOURS["Priest"],
    ARCHETYPE_COLOURS["Scout"],
    ARCHETYPE_COLOURS["Mage"],
)

# Ordered: archetype [Fighter, Priest, Scout, Mage], icon_id ascending within
# each archetype. display_order is assigned from this order at seed time.
CLASS_SEED: tuple[ClassInfo, ...] = (
    ClassInfo("Guardian", "Fighter", "Warrior", "Tank", _F, 3),
    ClassInfo("Berserker", "Fighter", "Warrior", "Tank", _F, 4),
    ClassInfo("Monk", "Fighter", "Brawler", "Tank", _F, 6),
    ClassInfo("Bruiser", "Fighter", "Brawler", "Tank", _F, 7),
    ClassInfo("Shadowknight", "Fighter", "Crusader", "Tank", _F, 9),
    ClassInfo("Paladin", "Fighter", "Crusader", "Tank", _F, 10),
    ClassInfo("Templar", "Priest", "Cleric", "Healer", _P, 13),
    ClassInfo("Inquisitor", "Priest", "Cleric", "Healer", _P, 14),
    ClassInfo("Warden", "Priest", "Druid", "Healer", _P, 16),
    ClassInfo("Fury", "Priest", "Druid", "Healer", _P, 17),
    ClassInfo("Mystic", "Priest", "Shaman", "Healer", _P, 19),
    ClassInfo("Defiler", "Priest", "Shaman", "Healer", _P, 20),
    ClassInfo("Channeler", "Priest", None, "Healer", _P, 44),
    ClassInfo("Swashbuckler", "Scout", "Rogue", "Melee DPS", _S, 33),
    ClassInfo("Brigand", "Scout", "Rogue", "Melee DPS", _S, 34),
    ClassInfo("Troubador", "Scout", "Bard", "Support", _S, 36),
    ClassInfo("Dirge", "Scout", "Bard", "Support", _S, 37),
    ClassInfo("Ranger", "Scout", "Predator", "Ranged DPS", _S, 39),
    ClassInfo("Assassin", "Scout", "Predator", "Melee DPS", _S, 40),
    ClassInfo("Beastlord", "Scout", None, "Melee DPS", _S, 42),
    ClassInfo("Wizard", "Mage", "Sorcerer", "Ranged DPS", _M, 23),
    ClassInfo("Warlock", "Mage", "Sorcerer", "Ranged DPS", _M, 24),
    ClassInfo("Coercer", "Mage", "Enchanter", "Support", _M, 26),
    ClassInfo("Illusionist", "Mage", "Enchanter", "Support", _M, 27),
    ClassInfo("Conjuror", "Mage", "Summoner", "Ranged DPS", _M, 29),
    ClassInfo("Necromancer", "Mage", "Summoner", "Ranged DPS", _M, 30),
)

# Tradeskill (artisan) class names. Not part of CLASS_SEED because they're not
# adventure classes — no archetype/subclass/role/icon_id semantics — but Census
# item rows can list them when an item is restricted to crafters. Single source
# of truth: anywhere else that needed an "artisans" frozenset (items.py,
# census/constants.py) now derives from here.
CRAFTER_NAMES: frozenset[str] = frozenset(
    [
        "Sage",
        "Armorer",
        "Weaponsmith",
        "Woodworker",
        "Jeweler",
        "Carpenter",
        "Tailor",
        "Alchemist",
        "Provisioner",
    ]
)


# Ordered list of (subclass_name, frozenset[class_name]) for the 12 subclass
# groups (Warriors, Crusaders, …). Channeler and Beastlord have subclass=None
# so they're correctly excluded. Order: stable by first-occurrence in CLASS_SEED
# so display ordering matches archetype ordering (Fighter subclasses first,
# then Priest, Scout, Mage).
def _build_subclass_groups() -> tuple[tuple[str, frozenset[str]], ...]:
    seen: dict[str, list[str]] = {}
    for c in CLASS_SEED:
        if c.subclass is None:
            continue
        seen.setdefault(c.subclass, []).append(c.name)
    return tuple((sub, frozenset(names)) for sub, names in seen.items())


SUBCLASS_GROUPS: tuple[tuple[str, frozenset[str]], ...] = _build_subclass_groups()


# Ordered list of (archetype_name, frozenset[class_name]) — Fighter/Priest/Scout/Mage.
def _build_archetype_groups() -> tuple[tuple[str, frozenset[str]], ...]:
    seen: dict[str, list[str]] = {}
    for c in CLASS_SEED:
        seen.setdefault(c.archetype, []).append(c.name)
    return tuple((arc, frozenset(names)) for arc, names in seen.items())


ARCHETYPE_GROUPS: tuple[tuple[str, frozenset[str]], ...] = _build_archetype_groups()


def _db_path() -> Path:
    env = os.getenv("DB_CLASSES_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "data" / "classes" / "classes.db"


DB_PATH: Path = _db_path()

_CREATE_CLASSES = """
CREATE TABLE IF NOT EXISTS classes (
    name           TEXT PRIMARY KEY,
    archetype      TEXT    NOT NULL,
    subclass       TEXT,
    role           TEXT    NOT NULL,
    colour         TEXT    NOT NULL,
    display_order  INTEGER NOT NULL,
    icon_id        INTEGER NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_classes_archetype ON classes (archetype);",
    "CREATE INDEX IF NOT EXISTS idx_classes_role ON classes (role);",
]


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Create the classes table/indexes if missing. Returns an open connection."""
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(_CREATE_CLASSES)
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()
    return conn


def seed(conn: sqlite3.Connection) -> int:
    """(Re)populate the classes table from CLASS_SEED. display_order = the
    index of each record in CLASS_SEED. Returns the row count."""
    rows = [(c.name, c.archetype, c.subclass, c.role, c.colour, i, c.icon_id) for i, c in enumerate(CLASS_SEED)]
    with conn:
        conn.execute("DELETE FROM classes")
        conn.executemany(
            "INSERT INTO classes (name, archetype, subclass, role, colour, display_order, icon_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def iter_adventure_class_names() -> list[str]:
    """Return all adventure-class names in display_order (sorted alphabetically within CLASS_SEED).

    Sourced directly from CLASS_SEED so this works without an initialised DB.
    Used by web routes that need the class name list without a DB round-trip.
    """
    return [c.name for c in CLASS_SEED]


def list_all(path: Path = DB_PATH) -> list[dict]:
    """All classes ordered by display_order. Empty list if the DB is missing/unseeded."""
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM classes ORDER BY display_order").fetchall()]
    finally:
        conn.close()


def find_by_name(name: str, path: Path = DB_PATH) -> dict | None:
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM classes WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def by_role(role: str, path: Path = DB_PATH) -> list[dict]:
    return [c for c in list_all(path) if c["role"] == role]


def by_archetype(archetype: str, path: Path = DB_PATH) -> list[dict]:
    return [c for c in list_all(path) if c["archetype"] == archetype]
