"""Tests for the editable raid-roster helpers on the zones catalogue.

Postgres edition: tests lease an isolated scratch schema via the
``zones_schema`` fixture (tests/fixtures/zones_raids_db.py) and construct
``ZoneCatalogue(zones_schema)`` — the analog of the old
``ZoneCatalogue(tmp_db)``. Raw seeding/assertions go through
``pg_conn(schema)`` (dict rows, %s params).

Note on the two former init_db data-normalization tests: the one-time
SQLite fixups (comma-joined encounter_name collapse, " (Zone)" suffix
strip) were retired in the Postgres cutover — ``PgCatalogue.init_db()``
creates and mutates nothing (DDL is owned by db/migrations/; prod data
crossed over already normalized). The descendants below pin that new
contract plus the surviving alias-resolution behaviour.
"""

from __future__ import annotations

import pytest

from backend.eq2db import zones as zones_db
from tests.fixtures.pg import pg_conn


def _seed_legacy_zone(schema: str) -> tuple[int, int]:
    """Seed one zone + one comma-joined encounter (two mobs at positions
    0 + 1) — the legacy pre-normalization shape. Returns (zone_id, encounter_id)."""
    with pg_conn(schema) as conn:
        conn.execute(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES (1, 'Shard of Hate', 'shard of hate', 'RoK', 'Rise of Kunark', 'test', 'test')"
        )
        zone_id = 1
        enc_id = conn.execute(
            "INSERT INTO zone_encounters (zone_id, encounter_name, position) "
            "VALUES (%s, 'Ire, Malevolence', 3) RETURNING id",
            (zone_id,),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (%s, 'Ire', 'ire', 0)",
            (enc_id,),
        )
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (%s, 'Malevolence', 'malevolence', 1)",
            (enc_id,),
        )
        return zone_id, enc_id


def test_init_db_is_schema_passive_no_data_normalization(zones_schema):
    """Descendant of the SQLite comma-joined-encounter-name normalization
    test: on Postgres ``init_db()`` is a pooled-connection handle, NOT a
    schema/data fixup pass — a legacy-shaped row passes through untouched
    (the one-time normalization was retired at cutover; the copied prod
    data was already normalized). Pins the "init_db never mutates data"
    contract so a future destructive _post_init analog is caught."""
    zone_id, enc_id = _seed_legacy_zone(zones_schema)
    # Add a non-comma encounter alongside.
    with pg_conn(zones_schema) as conn:
        enc_id2 = conn.execute(
            "INSERT INTO zone_encounters (zone_id, encounter_name, position) "
            "VALUES (%s, 'Demetrius Crane', 1) RETURNING id",
            (zone_id,),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (%s, 'Demetrius Crane', 'demetrius crane', 0)",
            (enc_id2,),
        )

    # init_db is idempotent and side-effect free — twice for good measure.
    zones_db.ZoneCatalogue(zones_schema).init_db().close()
    zones_db.ZoneCatalogue(zones_schema).init_db().close()

    with pg_conn(zones_schema) as conn:
        rows = {r["id"]: r["encounter_name"] for r in conn.execute("SELECT id, encounter_name FROM zone_encounters")}
    assert rows[enc_id] == "Ire, Malevolence"  # untouched — normalization retired
    assert rows[enc_id2] == "Demetrius Crane"  # untouched


def test_paren_zone_alias_resolves_to_canonical(zones_schema):
    """Descendant of the SQLite " (Zone)" suffix-strip normalization test:
    the rewrite itself was retired at cutover (prod data crossed over
    already stripped, with the old suffixed name preserved as an alias) —
    what survives is the alias contract: find_by_name resolves BOTH the
    clean name and the historical parenthesised form to the same canonical
    zone."""
    with pg_conn(zones_schema) as conn:
        conn.execute(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES (1, 'Kurn''s Tower', 'kurn''s tower', 'TSO', 'The Shadow Odyssey', 'test', 'test')"
        )
        conn.execute(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES (2, 'Halls of Fate', 'halls of fate', 'EoF', 'Echoes of Faydwer', 'test', 'test')"
        )
        conn.execute(
            "INSERT INTO zone_aliases (alias, alias_lower, zone_id) "
            "VALUES ('Kurn''s Tower (Zone)', 'kurn''s tower (zone)', 1)"
        )

    cat = zones_db.ZoneCatalogue(zones_schema)
    from_clean = cat.find_by_name("Kurn's Tower")
    from_suffixed = cat.find_by_name("Kurn's Tower (Zone)")
    assert from_clean is not None and from_suffixed is not None
    assert from_clean["name"] == from_suffixed["name"] == "Kurn's Tower"
    assert from_clean["name_lower"] == "kurn's tower"
    # The unaffected zone resolves only by its own name.
    assert cat.find_by_name("Halls of Fate") is not None


def _bootstrap_zone(schema: str) -> int:
    """Single zone + zero encounters. Returns zone_id."""
    with pg_conn(schema) as conn:
        conn.execute(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_confidence, expansion_source) "
            "VALUES (1, 'Test Zone', 'test zone', 'RoK', 'Rise of Kunark', 'test', 'test')"
        )
    return 1


def test_add_encounter_creates_row_and_position0_mob(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Adkar Vyx")
    assert enc["encounter_name"] == "Adkar Vyx"
    assert enc["position"] == 1
    assert enc["mobs"] == [{"mob_name": "Adkar Vyx", "position": 0}]


def test_add_encounter_appends_after_existing(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="First")
    enc2 = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Second")
    assert enc2["position"] == 2


def test_update_encounter_renames_primary_and_position0_mob(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Old Name")
    updated = zones_db.ZoneCatalogue(zones_schema).update_encounter(enc["id"], primary_mob="New Name")
    assert updated["encounter_name"] == "New Name"
    assert updated["mobs"][0]["mob_name"] == "New Name"


def test_update_encounter_stage_and_wiki(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Boss")
    updated = zones_db.ZoneCatalogue(zones_schema).update_encounter(enc["id"], stage="Wing 1", wiki_url="http://x")
    assert updated["stage"] == "Wing 1"
    assert updated["wiki_url"] == "http://x"
    # primary unchanged
    assert updated["encounter_name"] == "Boss"


def test_delete_encounter_cascades_mobs(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Doomed")
    assert zones_db.ZoneCatalogue(zones_schema).delete_encounter(enc["id"]) is True
    with pg_conn(zones_schema) as c:
        n_enc = c.execute("SELECT COUNT(*) AS n FROM zone_encounters WHERE id = %s", (enc["id"],)).fetchone()["n"]
        n_mobs = c.execute(
            "SELECT COUNT(*) AS n FROM zone_encounter_mobs WHERE encounter_id = %s", (enc["id"],)
        ).fetchone()["n"]
    assert n_enc == 0
    assert n_mobs == 0


def test_delete_encounter_missing_returns_false(zones_schema):
    assert zones_db.ZoneCatalogue(zones_schema).delete_encounter(99999) is False


def test_reorder_encounters_atomic_permutation(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    a = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="A")
    b = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="B")
    c = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="C")
    # Reverse order: C, B, A
    zones_db.ZoneCatalogue(zones_schema).reorder_encounters(zid, [c["id"], b["id"], a["id"]])
    with pg_conn(zones_schema) as conn:
        positions = {
            r["id"]: r["position"]
            for r in conn.execute("SELECT id, position FROM zone_encounters WHERE zone_id = %s", (zid,))
        }
    assert positions[c["id"]] == 1
    assert positions[b["id"]] == 2
    assert positions[a["id"]] == 3


def test_reorder_encounters_rejects_missing_id(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    a = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="A")
    b = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="B")
    with pytest.raises(ValueError):
        zones_db.ZoneCatalogue(zones_schema).reorder_encounters(zid, [a["id"]])  # missing b
    with pytest.raises(ValueError):
        zones_db.ZoneCatalogue(zones_schema).reorder_encounters(zid, [a["id"], b["id"], 9999])  # extra


def test_reorder_encounters_rejects_duplicates(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    a = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="A")
    b = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="B")
    with pytest.raises(ValueError):
        zones_db.ZoneCatalogue(zones_schema).reorder_encounters(zid, [a["id"], a["id"], b["id"]])


def test_add_mob_appends_sibling(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="Sibling")
    assert sib["position"] == 1
    enc2 = zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="Third")
    assert enc2["position"] == 2


def test_add_mob_make_primary_shifts_old_primary(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="OldPrimary")
    zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="NewPrimary", make_primary=True)
    with pg_conn(zones_schema) as conn:
        mobs = [
            (r["mob_name"], r["position"])
            for r in conn.execute(
                "SELECT mob_name, position FROM zone_encounter_mobs WHERE encounter_id = %s ORDER BY position",
                (enc["id"],),
            )
        ]
        name = conn.execute("SELECT encounter_name FROM zone_encounters WHERE id = %s", (enc["id"],)).fetchone()[
            "encounter_name"
        ]
    assert mobs[0] == ("NewPrimary", 0)
    assert ("OldPrimary", 1) in mobs
    assert name == "NewPrimary"


def test_update_mob_renames_primary_updates_encounter_name(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="Sibling")
    primary_id = next(m["id"] for m in zones_db.ZoneCatalogue(zones_schema).list_mobs(enc["id"]) if m["position"] == 0)
    zones_db.ZoneCatalogue(zones_schema).update_mob(primary_id, mob_name="Renamed")
    with pg_conn(zones_schema) as conn:
        assert (
            conn.execute(
                "SELECT encounter_name FROM zone_encounters WHERE id = %s",
                (enc["id"],),
            ).fetchone()["encounter_name"]
            == "Renamed"
        )
    # Renaming a sibling must NOT touch encounter_name
    zones_db.ZoneCatalogue(zones_schema).update_mob(sib["id"], mob_name="SibRenamed")
    with pg_conn(zones_schema) as conn:
        assert (
            conn.execute(
                "SELECT encounter_name FROM zone_encounters WHERE id = %s",
                (enc["id"],),
            ).fetchone()["encounter_name"]
            == "Renamed"
        )


def test_promote_mob_swaps_with_primary(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="Sibling")
    zones_db.ZoneCatalogue(zones_schema).promote_mob(sib["id"])
    with pg_conn(zones_schema) as conn:
        mobs = [
            (r["mob_name"], r["position"])
            for r in conn.execute(
                "SELECT mob_name, position FROM zone_encounter_mobs WHERE encounter_id = %s ORDER BY position",
                (enc["id"],),
            )
        ]
        name = conn.execute("SELECT encounter_name FROM zone_encounters WHERE id = %s", (enc["id"],)).fetchone()[
            "encounter_name"
        ]
    assert mobs == [("Sibling", 0), ("Primary", 1)]
    assert name == "Sibling"


def test_promote_mob_noop_when_already_primary(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="OnlyOne")
    primary_id = next(m["id"] for m in zones_db.ZoneCatalogue(zones_schema).list_mobs(enc["id"]) if m["position"] == 0)
    result = zones_db.ZoneCatalogue(zones_schema).promote_mob(primary_id)
    assert result["position"] == 0
    assert result["mob_name"] == "OnlyOne"


def test_delete_mob_refuses_last_mob(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Only")
    only_id = next(m["id"] for m in zones_db.ZoneCatalogue(zones_schema).list_mobs(enc["id"]) if m["position"] == 0)
    with pytest.raises(ValueError, match="last mob"):
        zones_db.ZoneCatalogue(zones_schema).delete_mob(only_id)


def test_delete_mob_refuses_primary_while_siblings_exist(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Primary")
    zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="Sibling")
    primary_id = next(m["id"] for m in zones_db.ZoneCatalogue(zones_schema).list_mobs(enc["id"]) if m["position"] == 0)
    with pytest.raises(ValueError, match="primary"):
        zones_db.ZoneCatalogue(zones_schema).delete_mob(primary_id)


def test_delete_mob_sibling_succeeds(zones_schema):
    zid = _bootstrap_zone(zones_schema)
    enc = zones_db.ZoneCatalogue(zones_schema).add_encounter(zid, primary_mob="Primary")
    sib = zones_db.ZoneCatalogue(zones_schema).add_mob(enc["id"], mob_name="Sibling")
    assert zones_db.ZoneCatalogue(zones_schema).delete_mob(sib["id"]) is True
    mobs = zones_db.ZoneCatalogue(zones_schema).list_mobs(enc["id"])
    assert [m["mob_name"] for m in mobs] == ["Primary"]


def test_delete_mob_missing_returns_false(zones_schema):
    assert zones_db.ZoneCatalogue(zones_schema).delete_mob(99999) is False
