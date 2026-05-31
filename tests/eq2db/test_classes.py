from __future__ import annotations

from backend.eq2db import classes as classes_db
from backend.eq2db.classes import CLASS_SEED


class TestSeedIntegrity:
    def test_has_26_unique_classes(self):
        names = [c.name for c in CLASS_SEED]
        assert len(names) == 26
        assert len(set(names)) == 26

    def test_valid_archetypes_and_roles(self):
        archetypes = {"Fighter", "Priest", "Scout", "Mage"}
        roles = {"Tank", "Healer", "Melee DPS", "Ranged DPS", "Support"}
        for c in CLASS_SEED:
            assert c.archetype in archetypes, c.name
            assert c.role in roles, c.name

    def test_role_counts(self):
        from collections import Counter

        counts = Counter(c.role for c in CLASS_SEED)
        assert counts == {"Tank": 6, "Healer": 7, "Support": 4, "Melee DPS": 4, "Ranged DPS": 5}

    def test_icon_ids_unique(self):
        ids = [c.icon_id for c in CLASS_SEED]
        assert len(set(ids)) == 26

    def test_only_beastlord_channeler_lack_subclass(self):
        no_sub = {c.name for c in CLASS_SEED if c.subclass is None}
        assert no_sub == {"Beastlord", "Channeler"}

    def test_known_icon_ids(self):
        by_name = {c.name: c for c in CLASS_SEED}
        assert by_name["Templar"].icon_id == 13
        assert by_name["Inquisitor"].icon_id == 14
        assert by_name["Swashbuckler"].icon_id == 33
        assert by_name["Channeler"].icon_id == 44


class TestDbRoundTrip:
    def test_seed_and_list_all(self):
        conn = classes_db.init_db(__import__("pathlib").Path(":memory:"))
        try:
            n = classes_db.seed(conn)
            assert n == 26
            conn.row_factory = __import__("sqlite3").Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM classes ORDER BY display_order")]
            assert len(rows) == 26
            assert rows[0]["name"] == "Guardian"  # first in archetype/icon order
            assert rows[0]["display_order"] == 0
            assert [r["display_order"] for r in rows] == list(range(26))
        finally:
            conn.close()


class TestConstantsDerivation:
    def test_archetype_sets_match_legacy_literals(self):
        from backend.census import constants

        assert constants.FIGHTERS == frozenset(["Guardian", "Berserker", "Monk", "Bruiser", "Shadowknight", "Paladin"])
        assert constants.PRIESTS == frozenset(
            ["Templar", "Inquisitor", "Fury", "Warden", "Mystic", "Defiler", "Channeler"]
        )
        assert constants.SCOUTS == frozenset(
            ["Troubador", "Dirge", "Assassin", "Ranger", "Swashbuckler", "Brigand", "Beastlord"]
        )
        assert constants.MAGES == frozenset(["Coercer", "Illusionist", "Conjuror", "Necromancer", "Wizard", "Warlock"])

    def test_archetype_sets_come_from_seed(self):
        from backend.census import constants
        from backend.eq2db.classes import CLASS_SEED

        union = constants.FIGHTERS | constants.PRIESTS | constants.SCOUTS | constants.MAGES
        assert union == {c.name for c in CLASS_SEED}


class TestSeedDerivedConstants:
    """Single-source-of-truth guarantees for the class-grouping refactor.

    The constants below USED to be hardcoded in three places (eq2db/items.py,
    census/constants.py, server/api/item.py) and easily drifted apart. They
    now derive from CLASS_SEED + CRAFTER_NAMES. These tests pin the derivation
    so a future regression can't silently re-introduce the duplication.
    """

    def test_archetype_colours_exposed(self):
        from backend.eq2db.classes import ARCHETYPE_COLOURS

        assert ARCHETYPE_COLOURS == {
            "Fighter": "#f87171",
            "Priest": "#4ade80",
            "Scout": "#fbbf24",
            "Mage": "#93b4ff",
        }

    def test_archetype_colours_reexported_via_constants(self):
        from backend.census.constants import CLASS_ARCHETYPE_COLOURS
        from backend.eq2db.classes import ARCHETYPE_COLOURS

        # Same dict object — back-compat alias, not a hand-mirrored copy.
        assert CLASS_ARCHETYPE_COLOURS is ARCHETYPE_COLOURS

    def test_crafter_names_complete(self):
        from backend.eq2db.classes import CRAFTER_NAMES

        assert CRAFTER_NAMES == frozenset(
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

    def test_artisans_derives_from_crafter_names(self):
        from backend.census.constants import ARTISANS
        from backend.eq2db.classes import CRAFTER_NAMES

        assert ARTISANS is CRAFTER_NAMES

    def test_subclass_groups_cover_24_classes(self):
        """12 subclasses × 2 classes each. Channeler + Beastlord excluded."""
        from backend.eq2db.classes import SUBCLASS_GROUPS

        all_subclass_classes = set().union(*[members for _, members in SUBCLASS_GROUPS])
        assert len(all_subclass_classes) == 24
        assert "Channeler" not in all_subclass_classes
        assert "Beastlord" not in all_subclass_classes

    def test_subclass_groups_have_expected_names(self):
        from backend.eq2db.classes import SUBCLASS_GROUPS

        names = {name for name, _ in SUBCLASS_GROUPS}
        assert names == {
            "Warrior",
            "Brawler",
            "Crusader",
            "Cleric",
            "Druid",
            "Shaman",
            "Rogue",
            "Bard",
            "Predator",
            "Sorcerer",
            "Enchanter",
            "Summoner",
        }

    def test_archetype_groups_match_constants(self):
        from backend.census.constants import FIGHTERS, MAGES, PRIESTS, SCOUTS
        from backend.eq2db.classes import ARCHETYPE_GROUPS

        by_name = dict(ARCHETYPE_GROUPS)
        assert by_name["Fighter"] == FIGHTERS
        assert by_name["Priest"] == PRIESTS
        assert by_name["Scout"] == SCOUTS
        assert by_name["Mage"] == MAGES

    def test_class_groups_contains_subclasses(self):
        """CLASS_GROUPS exact-match dict has every subclass group."""
        from backend.census.constants import CLASS_GROUPS

        assert CLASS_GROUPS[frozenset(["Guardian", "Berserker"])] == "All Warriors"
        assert CLASS_GROUPS[frozenset(["Templar", "Inquisitor"])] == "All Clerics"
        assert CLASS_GROUPS[frozenset(["Wizard", "Warlock"])] == "All Sorcerers"
        assert CLASS_GROUPS[frozenset(["Troubador", "Dirge"])] == "All Bards"

    def test_archetypes_ordered_largest_first(self):
        """Decomposition list MUST list full archetypes before subclasses so
        a complete-archetype set produces 'All Fighters' not 'All Warriors,
        All Brawlers, All Crusaders'."""
        from backend.census.constants import ARCHETYPES

        labels = [name for _, name in ARCHETYPES]
        # The four archetypes + Artisans come first, then the 12 subclasses.
        assert labels[:5] == ["All Fighters", "All Priests", "All Scouts", "All Mages", "All Artisans"]
        assert len(labels) == 5 + 12  # 4 archetypes + Artisans + 12 subclasses


class TestComputeClassLabelParity:
    """compute_class_label() must produce the same labels as before the
    refactor — this is what the items.db `class_label` column was backfilled
    with, so any regression breaks search results until re-backfill."""

    def test_all_classes_label(self):
        from backend.eq2db.classes import CLASS_SEED
        from backend.eq2db.items import compute_class_label

        all_advs = {c.name.lower(): {} for c in CLASS_SEED}
        assert compute_class_label(all_advs) == "All Classes"

    def test_archetype_label(self):
        from backend.eq2db.items import compute_class_label

        fighters = {n: {} for n in ["guardian", "berserker", "monk", "bruiser", "shadowknight", "paladin"]}
        assert compute_class_label(fighters) == "All Fighters"

    def test_subclass_label(self):
        from backend.eq2db.items import compute_class_label

        assert compute_class_label({"guardian": {}, "berserker": {}}) == "All Warriors"
        assert compute_class_label({"templar": {}, "inquisitor": {}}) == "All Clerics"

    def test_single_class_label(self):
        from backend.eq2db.items import compute_class_label

        assert compute_class_label({"guardian": {"displayname": "Guardian"}}) == "Guardian"

    def test_crafters_only(self):
        from backend.eq2db.items import compute_class_label

        all_crafters = {
            n: {}
            for n in [
                "sage",
                "armorer",
                "weaponsmith",
                "woodworker",
                "jeweler",
                "carpenter",
                "tailor",
                "alchemist",
                "provisioner",
            ]
        }
        assert compute_class_label(all_crafters) == "Crafters"

    def test_empty_or_none(self):
        from backend.eq2db.items import compute_class_label

        assert compute_class_label({}) is None
        assert compute_class_label(None) is None

    def test_mixed_archetype_plus_individual(self):
        from backend.eq2db.items import compute_class_label

        # All Fighters + one extra Priest → "All Fighters / Templar"
        d = {n: {} for n in ["guardian", "berserker", "monk", "bruiser", "shadowknight", "paladin", "templar"]}
        result = compute_class_label(d)
        assert result == "All Fighters / Templar"
