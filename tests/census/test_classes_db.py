from __future__ import annotations

from census import classes_db
from census.classes_db import CLASS_SEED


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
        from census import constants

        assert constants.FIGHTERS == frozenset(["Guardian", "Berserker", "Monk", "Bruiser", "Shadowknight", "Paladin"])
        assert constants.PRIESTS == frozenset(
            ["Templar", "Inquisitor", "Fury", "Warden", "Mystic", "Defiler", "Channeler"]
        )
        assert constants.SCOUTS == frozenset(
            ["Troubador", "Dirge", "Assassin", "Ranger", "Swashbuckler", "Brigand", "Beastlord"]
        )
        assert constants.MAGES == frozenset(["Coercer", "Illusionist", "Conjuror", "Necromancer", "Wizard", "Warlock"])

    def test_archetype_sets_come_from_seed(self):
        from census import constants
        from census.classes_db import CLASS_SEED

        union = constants.FIGHTERS | constants.PRIESTS | constants.SCOUTS | constants.MAGES
        assert union == {c.name for c in CLASS_SEED}
