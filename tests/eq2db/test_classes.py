"""Integrity tests for the committed classes.db.

The class catalogue is owned by ``data/classes/classes.db`` (committed). These
tests read the rows directly + check the module-level derived constants in
backend.eq2db.classes / backend.census.constants. There is no CLASS_SEED
struct anymore — to change class metadata, edit the row in classes.db and
commit the new file.
"""

from __future__ import annotations

from collections import Counter

from backend.eq2db.classes import catalogue

ARCHETYPE_COLOURS = catalogue.archetype_colours()
ARCHETYPE_GROUPS = catalogue.archetype_groups()
CRAFTER_NAMES = catalogue.crafter_names()
SUBCLASS_GROUPS = catalogue.subclass_groups()


def iter_adventure_class_names():
    return catalogue.adventure_class_names()


def list_all():
    return catalogue.list_all()


class TestDbRows:
    """Properties of the rows committed in classes.db itself."""

    def test_has_26_adventure_plus_9_crafters(self):
        rows = list_all()
        adv = [r for r in rows if r["archetype"] != "Crafter"]
        crafters = [r for r in rows if r["archetype"] == "Crafter"]
        assert len(adv) == 26
        assert len(crafters) == 9
        assert len(rows) == 35

    def test_unique_names(self):
        names = [r["name"] for r in list_all()]
        assert len(set(names)) == len(names)

    def test_adventure_archetypes_and_roles(self):
        archetypes = {"Fighter", "Priest", "Scout", "Mage"}
        roles = {"Tank", "Healer", "Melee DPS", "Ranged DPS", "Support"}
        adv = [r for r in list_all() if r["archetype"] != "Crafter"]
        for r in adv:
            assert r["archetype"] in archetypes, r["name"]
            assert r["role"] in roles, r["name"]

    def test_role_counts_for_adventure_classes(self):
        adv = [r for r in list_all() if r["archetype"] != "Crafter"]
        counts = Counter(r["role"] for r in adv)
        assert counts == {"Tank": 6, "Healer": 7, "Support": 4, "Melee DPS": 4, "Ranged DPS": 5}

    def test_crafter_rows(self):
        crafters = [r for r in list_all() if r["archetype"] == "Crafter"]
        names = {r["name"] for r in crafters}
        assert names == {
            "Sage",
            "Armorer",
            "Weaponsmith",
            "Woodworker",
            "Jeweler",
            "Carpenter",
            "Tailor",
            "Alchemist",
            "Provisioner",
        }
        for r in crafters:
            assert r["subclass"] is None
            assert r["role"] == "Crafter"

    def test_icon_ids_unique(self):
        ids = [r["icon_id"] for r in list_all()]
        assert len(set(ids)) == len(ids)

    def test_only_beastlord_channeler_lack_subclass_among_adventurers(self):
        no_sub = {r["name"] for r in list_all() if r["archetype"] != "Crafter" and r["subclass"] is None}
        assert no_sub == {"Beastlord", "Channeler"}

    def test_known_icon_ids(self):
        by_name = {r["name"]: r for r in list_all()}
        assert by_name["Templar"]["icon_id"] == 13
        assert by_name["Inquisitor"]["icon_id"] == 14
        assert by_name["Swashbuckler"]["icon_id"] == 33
        assert by_name["Channeler"]["icon_id"] == 44

    def test_display_order_continuous_from_zero(self):
        rows = sorted(list_all(), key=lambda r: r["display_order"])
        assert [r["display_order"] for r in rows] == list(range(len(rows)))


class TestSeedDerivedConstants:
    """Single-source-of-truth guarantees: every module-level constant
    derives from classes.db. A future regression that tries to redefine
    class data inline breaks one of these."""

    def test_archetype_colours_match_db(self):
        rows = [r for r in list_all() if r["archetype"] != "Crafter"]
        by_arch: dict[str, str] = {}
        for r in rows:
            by_arch.setdefault(r["archetype"], r["colour"])
        assert ARCHETYPE_COLOURS == by_arch
        assert set(ARCHETYPE_COLOURS) == {"Fighter", "Priest", "Scout", "Mage"}

    def test_archetype_colours_reexported_via_constants(self):
        from backend.census.constants import CLASS_ARCHETYPE_COLOURS

        # Same dict object — back-compat alias, not a hand-mirrored copy.
        assert CLASS_ARCHETYPE_COLOURS is ARCHETYPE_COLOURS

    def test_crafter_names_match_db(self):
        crafters = {r["name"] for r in list_all() if r["archetype"] == "Crafter"}
        assert CRAFTER_NAMES == frozenset(crafters)

    def test_artisans_derives_from_crafter_names(self):
        from backend.census.constants import ARTISANS

        assert ARTISANS is CRAFTER_NAMES

    def test_subclass_groups_cover_24_classes(self):
        """12 subclass pairs × 2 classes each. Channeler + Beastlord excluded."""
        all_subclass_classes = set().union(*[members for _, members in SUBCLASS_GROUPS])
        assert len(all_subclass_classes) == 24
        assert "Channeler" not in all_subclass_classes
        assert "Beastlord" not in all_subclass_classes

    def test_subclass_groups_have_expected_names(self):
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

        by_name = dict(ARCHETYPE_GROUPS)
        assert by_name["Fighter"] == FIGHTERS
        assert by_name["Priest"] == PRIESTS
        assert by_name["Scout"] == SCOUTS
        assert by_name["Mage"] == MAGES

    def test_archetype_sets_match_known_membership(self):
        from backend.census import constants

        assert constants.FIGHTERS == frozenset(["Guardian", "Berserker", "Monk", "Bruiser", "Shadowknight", "Paladin"])
        assert constants.PRIESTS == frozenset(
            ["Templar", "Inquisitor", "Fury", "Warden", "Mystic", "Defiler", "Channeler"]
        )
        assert constants.SCOUTS == frozenset(
            ["Troubador", "Dirge", "Assassin", "Ranger", "Swashbuckler", "Brigand", "Beastlord"]
        )
        assert constants.MAGES == frozenset(["Coercer", "Illusionist", "Conjuror", "Necromancer", "Wizard", "Warlock"])

    def test_class_groups_contains_subclasses(self):
        """CLASS_GROUPS exact-match dict has every subclass group."""
        from backend.census.constants import CLASS_GROUPS

        assert CLASS_GROUPS[frozenset(["Guardian", "Berserker"])] == "All Warriors"
        assert CLASS_GROUPS[frozenset(["Templar", "Inquisitor"])] == "All Clerics"
        assert CLASS_GROUPS[frozenset(["Wizard", "Warlock"])] == "All Sorcerers"
        assert CLASS_GROUPS[frozenset(["Troubador", "Dirge"])] == "All Bards"

    def test_archetypes_ordered_largest_first(self):
        """Decomposition list MUST list full archetypes before subclasses so a
        complete-archetype set produces 'All Fighters' not 'All Warriors,
        All Brawlers, All Crusaders'."""
        from backend.census.constants import ARCHETYPES

        labels = [name for _, name in ARCHETYPES]
        # 4 archetypes + Artisans come first, then the 12 subclasses.
        assert labels[:5] == ["All Fighters", "All Priests", "All Scouts", "All Mages", "All Artisans"]
        assert len(labels) == 5 + 12

    def test_iter_adventure_class_names_returns_26(self):
        names = iter_adventure_class_names()
        assert len(names) == 26
        assert "Sage" not in names  # crafters excluded
        assert "Guardian" in names


class TestComputeClassLabelParity:
    """compute_class_label() output must match the legacy CLASS_SEED-era
    implementation — this is what the items.class_label column was backfilled
    with, so any regression breaks search results until re-backfill."""

    def test_all_classes_label(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        all_advs = {name.lower(): {} for name in iter_adventure_class_names()}
        assert compute_class_label(all_advs) == "All Classes"

    def test_archetype_label(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        fighters = {n: {} for n in ["guardian", "berserker", "monk", "bruiser", "shadowknight", "paladin"]}
        assert compute_class_label(fighters) == "All Fighters"

    def test_subclass_label(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        assert compute_class_label({"guardian": {}, "berserker": {}}) == "All Warriors"
        assert compute_class_label({"templar": {}, "inquisitor": {}}) == "All Clerics"

    def test_single_class_label(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        assert compute_class_label({"guardian": {"displayname": "Guardian"}}) == "Guardian"

    def test_crafters_only(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        all_crafters = {n.lower(): {} for n in CRAFTER_NAMES}
        assert compute_class_label(all_crafters) == "Crafters"

    def test_empty_or_none(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        assert compute_class_label({}) is None
        assert compute_class_label(None) is None

    def test_mixed_archetype_plus_individual(self):
        from backend.eq2db.items import ItemCatalogue

        compute_class_label = ItemCatalogue.compute_class_label

        # All Fighters + one extra Priest → "All Fighters / Templar"
        d = {n: {} for n in ["guardian", "berserker", "monk", "bruiser", "shadowknight", "paladin", "templar"]}
        result = compute_class_label(d)
        assert result == "All Fighters / Templar"
