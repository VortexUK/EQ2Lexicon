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


# Archetype colours — canonical source is CLASS_ARCHETYPE_COLOURS in census/constants.py.
# Mirrored here (not imported) to avoid a circular import: constants.py imports CLASS_SEED
# from this module, so this module cannot import back from constants.
_F, _P, _S, _M = "#f87171", "#4ade80", "#fbbf24", "#93b4ff"

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
