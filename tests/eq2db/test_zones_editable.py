"""Tests for the editable raid-roster helpers added to zones_db."""

from __future__ import annotations

import sqlite3

from backend.eq2db import zones as zones_db


def _seed_legacy_zone(path) -> tuple[int, int]:
    """Build a minimal zones.db with one zone + one comma-joined encounter
    (two mobs at positions 0 + 1). Returns (zone_id, encounter_id)."""
    conn = zones_db.ZoneCatalogue(path).init_db()
    try:
        conn.execute(
            "INSERT INTO zones (name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES ('Shard of Hate', 'shard of hate', 'RoK', 'Rise of Kunark', 'test', 'test')"
        )
        zone_id = conn.execute("SELECT id FROM zones WHERE name = 'Shard of Hate'").fetchone()[0]
        conn.execute(
            "INSERT INTO zone_encounters (zone_id, encounter_name, position) VALUES (?, 'Ire, Malevolence', 3)",
            (zone_id,),
        )
        enc_id = conn.execute(
            "SELECT id FROM zone_encounters WHERE zone_id = ? AND position = 3", (zone_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (?, 'Ire', 'ire', 0)",
            (enc_id,),
        )
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (?, 'Malevolence', 'malevolence', 1)",
            (enc_id,),
        )
        conn.commit()
        return zone_id, enc_id
    finally:
        conn.close()


def test_init_db_normalizes_comma_joined_encounter_name(tmp_path):
    """A legacy encounter whose name is the comma-joined mob list is rewritten
    to the position-0 mob's name. Non-comma names are left alone. Idempotent."""
    p = tmp_path / "zones.db"
    zone_id, enc_id = _seed_legacy_zone(p)
    # Add a non-comma encounter that should NOT be touched.
    with sqlite3.connect(p) as conn:
        conn.execute(
            "INSERT INTO zone_encounters (zone_id, encounter_name, position) VALUES (?, 'Demetrius Crane', 1)",
            (zone_id,),
        )
        enc_id2 = conn.execute(
            "SELECT id FROM zone_encounters WHERE zone_id = ? AND position = 1", (zone_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (?, 'Demetrius Crane', 'demetrius crane', 0)",
            (enc_id2,),
        )
        conn.commit()

    # Re-init to trigger normalization (init_db is idempotent).
    conn = zones_db.ZoneCatalogue(p).init_db()
    try:
        rows = {r[0]: r[1] for r in conn.execute("SELECT id, encounter_name FROM zone_encounters")}
        assert rows[enc_id] == "Ire"  # comma-joined collapsed to primary
        assert rows[enc_id2] == "Demetrius Crane"  # untouched
    finally:
        conn.close()

    # Second run is a no-op (encounter_name no longer contains a comma).
    conn = zones_db.ZoneCatalogue(p).init_db()
    try:
        assert conn.execute("SELECT encounter_name FROM zone_encounters WHERE id = ?", (enc_id,)).fetchone()[0] == "Ire"
    finally:
        conn.close()


def test_init_db_strips_zone_suffix_from_zone_names(tmp_path):
    """A wiki-import zone name ending in ' (Zone)' is rewritten to the bare
    name, and the parenthesised form is preserved as an alias so historic
    references still resolve. Non-suffixed names are left alone. Idempotent."""
    p = tmp_path / "zones.db"
    conn = zones_db.ZoneCatalogue(p).init_db()
    try:
        conn.execute(
            "INSERT INTO zones (name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES ('Kurn''s Tower (Zone)', 'kurn''s tower (zone)', 'TSO', 'The Shadow Odyssey', 'test', 'test')"
        )
        conn.execute(
            "INSERT INTO zones (name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES ('Halls of Fate', 'halls of fate', 'EoF', 'Echoes of Faydwer', 'test', 'test')"
        )
        conn.commit()
    finally:
        conn.close()

    # Re-init to trigger the cleanup.
    conn = zones_db.ZoneCatalogue(p).init_db()
    try:
        names = {r[0]: r[1] for r in conn.execute("SELECT name, name_lower FROM zones ORDER BY name")}
        assert "Kurn's Tower" in names, "suffix should be stripped"
        assert names["Kurn's Tower"] == "kurn's tower", "name_lower should be stripped too"
        assert "Halls of Fate" in names, "unaffected zone should remain unchanged"
        assert "Kurn's Tower (Zone)" not in names, "old suffixed name should be gone"

        # Old name preserved as alias for backward-compat lookups.
        alias_row = conn.execute(
            "SELECT z.name FROM zone_aliases a JOIN zones z ON z.id = a.zone_id WHERE a.alias_lower = ?",
            ("kurn's tower (zone)",),
        ).fetchone()
        assert alias_row is not None, "old name should be preserved as alias"
        assert alias_row[0] == "Kurn's Tower", "alias should resolve to cleaned zone"

        # find_by_name resolves both forms to the same canonical zone.
        from_clean = zones_db.ZoneCatalogue(p).find_by_name("Kurn's Tower")
        from_suffixed = zones_db.ZoneCatalogue(p).find_by_name("Kurn's Tower (Zone)")
        assert from_clean is not None and from_suffixed is not None
        assert from_clean["name"] == from_suffixed["name"] == "Kurn's Tower"
    finally:
        conn.close()

    # Second run is a no-op (no rows match the LIKE filter anymore).
    conn = zones_db.ZoneCatalogue(p).init_db()
    try:
        cleaned_name = conn.execute("SELECT name FROM zones WHERE name_lower = 'kurn''s tower'").fetchone()
        assert cleaned_name is not None and cleaned_name[0] == "Kurn's Tower"
    finally:
        conn.close()


def _bootstrap_zone(p):
    """Single zone + zero encounters. Returns zone_id."""
    conn = zones_db.ZoneCatalogue(p).init_db()
    try:
        conn.execute(
            "INSERT INTO zones (name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES ('Test Zone', 'test zone', 'RoK', 'Rise of Kunark', 'test', 'test')"
        )
        zid = conn.execute("SELECT id FROM zones WHERE name = 'Test Zone'").fetchone()[0]
        conn.commit()
        return zid
    finally:
        conn.close()


def test_add_encounter_creates_row_and_position0_mob(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Adkar Vyx")
    assert enc["encounter_name"] == "Adkar Vyx"
    assert enc["position"] == 1
    assert enc["mobs"] == [{"mob_name": "Adkar Vyx", "position": 0}]


def test_add_encounter_appends_after_existing(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="First")
    enc2 = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Second")
    assert enc2["position"] == 2


def test_update_encounter_renames_primary_and_position0_mob(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Old Name")
    updated = zones_db.ZoneCatalogue(p).update_encounter(enc["id"], primary_mob="New Name")
    assert updated["encounter_name"] == "New Name"
    assert updated["mobs"][0]["mob_name"] == "New Name"


def test_update_encounter_stage_and_wiki(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Boss")
    updated = zones_db.ZoneCatalogue(p).update_encounter(enc["id"], stage="Wing 1", wiki_url="http://x")
    assert updated["stage"] == "Wing 1"
    assert updated["wiki_url"] == "http://x"
    # primary unchanged
    assert updated["encounter_name"] == "Boss"


def test_delete_encounter_cascades_mobs(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Doomed")
    assert zones_db.ZoneCatalogue(p).delete_encounter(enc["id"]) is True
    with sqlite3.connect(p) as c:
        assert c.execute("SELECT COUNT(*) FROM zone_encounters WHERE id = ?", (enc["id"],)).fetchone()[0] == 0
        assert (
            c.execute("SELECT COUNT(*) FROM zone_encounter_mobs WHERE encounter_id = ?", (enc["id"],)).fetchone()[0]
            == 0
        )


def test_delete_encounter_missing_returns_false(tmp_path):
    p = tmp_path / "zones.db"
    zones_db.ZoneCatalogue(p).init_db()
    assert zones_db.ZoneCatalogue(p).delete_encounter(99999) is False


def test_reorder_encounters_atomic_permutation(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    a = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="A")
    b = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="B")
    c = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="C")
    # Reverse order: C, B, A
    zones_db.ZoneCatalogue(p).reorder_encounters(zid, [c["id"], b["id"], a["id"]])
    with sqlite3.connect(p) as conn:
        positions = {
            r[0]: r[1] for r in conn.execute("SELECT id, position FROM zone_encounters WHERE zone_id = ?", (zid,))
        }
    assert positions[c["id"]] == 1
    assert positions[b["id"]] == 2
    assert positions[a["id"]] == 3


def test_reorder_encounters_rejects_missing_id(tmp_path):
    import pytest as _pytest

    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    a = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="A")
    b = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="B")
    with _pytest.raises(ValueError):
        zones_db.ZoneCatalogue(p).reorder_encounters(zid, [a["id"]])  # missing b
    with _pytest.raises(ValueError):
        zones_db.ZoneCatalogue(p).reorder_encounters(zid, [a["id"], b["id"], 9999])  # extra


def test_reorder_encounters_rejects_duplicates(tmp_path):
    import pytest as _pytest

    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    a = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="A")
    b = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="B")
    with _pytest.raises(ValueError):
        zones_db.ZoneCatalogue(p).reorder_encounters(zid, [a["id"], a["id"], b["id"]])


def test_add_mob_appends_sibling(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="Sibling")
    assert sib["position"] == 1
    enc2 = zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="Third")
    assert enc2["position"] == 2


def test_add_mob_make_primary_shifts_old_primary(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="OldPrimary")
    zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="NewPrimary", make_primary=True)
    with sqlite3.connect(p) as conn:
        mobs = [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position",
                (enc["id"],),
            )
        ]
    assert mobs[0] == ("NewPrimary", 0)
    assert ("OldPrimary", 1) in mobs
    with sqlite3.connect(p) as conn:
        name = conn.execute("SELECT encounter_name FROM zone_encounters WHERE id = ?", (enc["id"],)).fetchone()[0]
    assert name == "NewPrimary"


def test_update_mob_renames_primary_updates_encounter_name(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="Sibling")
    primary_id = next(m["id"] for m in zones_db.ZoneCatalogue(p).list_mobs(enc["id"]) if m["position"] == 0)
    zones_db.ZoneCatalogue(p).update_mob(primary_id, mob_name="Renamed")
    with sqlite3.connect(p) as conn:
        assert (
            conn.execute(
                "SELECT encounter_name FROM zone_encounters WHERE id = ?",
                (enc["id"],),
            ).fetchone()[0]
            == "Renamed"
        )
    # Renaming a sibling must NOT touch encounter_name
    zones_db.ZoneCatalogue(p).update_mob(sib["id"], mob_name="SibRenamed")
    with sqlite3.connect(p) as conn:
        assert (
            conn.execute(
                "SELECT encounter_name FROM zone_encounters WHERE id = ?",
                (enc["id"],),
            ).fetchone()[0]
            == "Renamed"
        )


def test_promote_mob_swaps_with_primary(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="Sibling")
    zones_db.ZoneCatalogue(p).promote_mob(sib["id"])
    with sqlite3.connect(p) as conn:
        mobs = [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position",
                (enc["id"],),
            )
        ]
        name = conn.execute("SELECT encounter_name FROM zone_encounters WHERE id = ?", (enc["id"],)).fetchone()[0]
    assert mobs == [("Sibling", 0), ("Primary", 1)]
    assert name == "Sibling"


def test_promote_mob_noop_when_already_primary(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="OnlyOne")
    primary_id = next(m["id"] for m in zones_db.ZoneCatalogue(p).list_mobs(enc["id"]) if m["position"] == 0)
    result = zones_db.ZoneCatalogue(p).promote_mob(primary_id)
    assert result["position"] == 0
    assert result["mob_name"] == "OnlyOne"


def test_delete_mob_refuses_last_mob(tmp_path):
    import pytest as _pytest

    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Only")
    only_id = next(m["id"] for m in zones_db.ZoneCatalogue(p).list_mobs(enc["id"]) if m["position"] == 0)
    with _pytest.raises(ValueError, match="last mob"):
        zones_db.ZoneCatalogue(p).delete_mob(only_id)


def test_delete_mob_refuses_primary_while_siblings_exist(tmp_path):
    import pytest as _pytest

    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Primary")
    zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="Sibling")
    primary_id = next(m["id"] for m in zones_db.ZoneCatalogue(p).list_mobs(enc["id"]) if m["position"] == 0)
    with _pytest.raises(ValueError, match="primary"):
        zones_db.ZoneCatalogue(p).delete_mob(primary_id)


def test_delete_mob_sibling_succeeds(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.ZoneCatalogue(p).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(p).add_mob(enc["id"], mob_name="Sibling")
    assert zones_db.ZoneCatalogue(p).delete_mob(sib["id"]) is True
    mobs = zones_db.ZoneCatalogue(p).list_mobs(enc["id"])
    assert [m["mob_name"] for m in mobs] == ["Primary"]


def test_delete_mob_missing_returns_false(tmp_path):
    p = tmp_path / "zones.db"
    zones_db.ZoneCatalogue(p).init_db()
    assert zones_db.ZoneCatalogue(p).delete_mob(99999) is False
