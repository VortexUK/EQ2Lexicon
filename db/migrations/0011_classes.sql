create schema if not exists classes;
set search_path to classes, public;

-- Phase 3: the last SQLite reference data moves to Postgres. classes was a
-- hand-maintained committed file with no build script, so THE SEEDS BELOW
-- ARE the canonical data (generated from data/classes/classes.db at
-- migration time; the 2026-09-22 Coercer/Illusionist icon fix included).
-- Future edits: change rows in the DB and add a new migration for
-- reproducibility — this file is immutable once applied.

CREATE TABLE classes (
    name          text    PRIMARY KEY,
    archetype     text    NOT NULL,
    subclass      text,                 -- NULL for Channeler/Beastlord/crafters
    role          text    NOT NULL,
    colour        text    NOT NULL,
    display_order integer NOT NULL,
    icon_id        integer NOT NULL,  -- EQ2wire sprite-sheet icon id
    -- Census type.classid. Seeded identical to icon_id because both come
    -- from the same game enumeration (Templar=13, Mystic=19 — verified
    -- against live type.classid), but they are DIFFERENT CONCEPTS: the
    -- 2026-08 Coercer/Illusionist prod bug was exactly this conflation.
    census_classid integer NOT NULL
);

CREATE INDEX idx_classes_archetype ON classes (archetype);
CREATE INDEX idx_classes_role      ON classes (role);

-- seeds
INSERT INTO classes (name, archetype, subclass, role, colour, display_order, icon_id, census_classid) VALUES
    ('Guardian', 'Fighter', 'Warrior', 'Tank', '#f87171', 0, 3, 3),
    ('Berserker', 'Fighter', 'Warrior', 'Tank', '#f87171', 1, 4, 4),
    ('Monk', 'Fighter', 'Brawler', 'Tank', '#f87171', 2, 6, 6),
    ('Bruiser', 'Fighter', 'Brawler', 'Tank', '#f87171', 3, 7, 7),
    ('Shadowknight', 'Fighter', 'Crusader', 'Tank', '#f87171', 4, 9, 9),
    ('Paladin', 'Fighter', 'Crusader', 'Tank', '#f87171', 5, 10, 10),
    ('Templar', 'Priest', 'Cleric', 'Healer', '#4ade80', 6, 13, 13),
    ('Inquisitor', 'Priest', 'Cleric', 'Healer', '#4ade80', 7, 14, 14),
    ('Warden', 'Priest', 'Druid', 'Healer', '#4ade80', 8, 16, 16),
    ('Fury', 'Priest', 'Druid', 'Healer', '#4ade80', 9, 17, 17),
    ('Mystic', 'Priest', 'Shaman', 'Healer', '#4ade80', 10, 19, 19),
    ('Defiler', 'Priest', 'Shaman', 'Healer', '#4ade80', 11, 20, 20),
    ('Channeler', 'Priest', NULL, 'Healer', '#4ade80', 12, 44, 44),
    ('Swashbuckler', 'Scout', 'Rogue', 'Melee DPS', '#fbbf24', 13, 33, 33),
    ('Brigand', 'Scout', 'Rogue', 'Melee DPS', '#fbbf24', 14, 34, 34),
    ('Troubador', 'Scout', 'Bard', 'Support', '#fbbf24', 15, 36, 36),
    ('Dirge', 'Scout', 'Bard', 'Support', '#fbbf24', 16, 37, 37),
    ('Ranger', 'Scout', 'Predator', 'Ranged DPS', '#fbbf24', 17, 39, 39),
    ('Assassin', 'Scout', 'Predator', 'Melee DPS', '#fbbf24', 18, 40, 40),
    ('Beastlord', 'Scout', NULL, 'Melee DPS', '#fbbf24', 19, 42, 42),
    ('Wizard', 'Mage', 'Sorcerer', 'Ranged DPS', '#93b4ff', 20, 23, 23),
    ('Warlock', 'Mage', 'Sorcerer', 'Ranged DPS', '#93b4ff', 21, 24, 24),
    ('Coercer', 'Mage', 'Enchanter', 'Support', '#93b4ff', 22, 27, 27),
    ('Illusionist', 'Mage', 'Enchanter', 'Support', '#93b4ff', 23, 26, 26),
    ('Conjuror', 'Mage', 'Summoner', 'Ranged DPS', '#93b4ff', 24, 29, 29),
    ('Necromancer', 'Mage', 'Summoner', 'Ranged DPS', '#93b4ff', 25, 30, 30),
    ('Sage', 'Crafter', NULL, 'Crafter', '#a1a1aa', 26, 100, 100),
    ('Armorer', 'Crafter', NULL, 'Crafter', '#a1a1aa', 27, 101, 101),
    ('Weaponsmith', 'Crafter', NULL, 'Crafter', '#a1a1aa', 28, 102, 102),
    ('Woodworker', 'Crafter', NULL, 'Crafter', '#a1a1aa', 29, 103, 103),
    ('Jeweler', 'Crafter', NULL, 'Crafter', '#a1a1aa', 30, 104, 104),
    ('Carpenter', 'Crafter', NULL, 'Crafter', '#a1a1aa', 31, 105, 105),
    ('Tailor', 'Crafter', NULL, 'Crafter', '#a1a1aa', 32, 106, 106),
    ('Alchemist', 'Crafter', NULL, 'Crafter', '#a1a1aa', 33, 107, 107),
    ('Provisioner', 'Crafter', NULL, 'Crafter', '#a1a1aa', 34, 108, 108)
ON CONFLICT (name) DO NOTHING;
