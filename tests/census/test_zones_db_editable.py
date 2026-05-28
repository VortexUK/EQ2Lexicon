"""Tests for the editable raid-roster helpers added to zones_db."""

from __future__ import annotations

import sqlite3

from census import zones_db


def _seed_legacy_zone(path) -> tuple[int, int]:
    """Build a minimal zones.db with one zone + one comma-joined encounter
    (two mobs at positions 0 + 1). Returns (zone_id, encounter_id)."""
    conn = zones_db.init_db(path)
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
    conn = zones_db.init_db(p)
    try:
        rows = {r[0]: r[1] for r in conn.execute("SELECT id, encounter_name FROM zone_encounters")}
        assert rows[enc_id] == "Ire"  # comma-joined collapsed to primary
        assert rows[enc_id2] == "Demetrius Crane"  # untouched
    finally:
        conn.close()

    # Second run is a no-op (encounter_name no longer contains a comma).
    conn = zones_db.init_db(p)
    try:
        assert conn.execute("SELECT encounter_name FROM zone_encounters WHERE id = ?", (enc_id,)).fetchone()[0] == "Ire"
    finally:
        conn.close()


def _bootstrap_zone(p):
    """Single zone + zero encounters. Returns zone_id."""
    conn = zones_db.init_db(p)
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
    enc = zones_db.add_encounter(zid, primary_mob="Adkar Vyx", path=p)
    assert enc["encounter_name"] == "Adkar Vyx"
    assert enc["position"] == 1
    assert enc["mobs"] == [{"mob_name": "Adkar Vyx", "position": 0}]


def test_add_encounter_appends_after_existing(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    zones_db.add_encounter(zid, primary_mob="First", path=p)
    enc2 = zones_db.add_encounter(zid, primary_mob="Second", path=p)
    assert enc2["position"] == 2


def test_update_encounter_renames_primary_and_position0_mob(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.add_encounter(zid, primary_mob="Old Name", path=p)
    updated = zones_db.update_encounter(enc["id"], primary_mob="New Name", path=p)
    assert updated["encounter_name"] == "New Name"
    assert updated["mobs"][0]["mob_name"] == "New Name"


def test_update_encounter_stage_and_wiki(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.add_encounter(zid, primary_mob="Boss", path=p)
    updated = zones_db.update_encounter(enc["id"], stage="Wing 1", wiki_url="http://x", path=p)
    assert updated["stage"] == "Wing 1"
    assert updated["wiki_url"] == "http://x"
    # primary unchanged
    assert updated["encounter_name"] == "Boss"


def test_delete_encounter_cascades_mobs(tmp_path):
    p = tmp_path / "zones.db"
    zid = _bootstrap_zone(p)
    enc = zones_db.add_encounter(zid, primary_mob="Doomed", path=p)
    assert zones_db.delete_encounter(enc["id"], path=p) is True
    with sqlite3.connect(p) as c:
        assert c.execute("SELECT COUNT(*) FROM zone_encounters WHERE id = ?", (enc["id"],)).fetchone()[0] == 0
        assert (
            c.execute("SELECT COUNT(*) FROM zone_encounter_mobs WHERE encounter_id = ?", (enc["id"],)).fetchone()[0]
            == 0
        )


def test_delete_encounter_missing_returns_false(tmp_path):
    p = tmp_path / "zones.db"
    zones_db.init_db(p)
    assert zones_db.delete_encounter(99999, path=p) is False
