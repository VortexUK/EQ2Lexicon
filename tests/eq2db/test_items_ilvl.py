"""Tests for the ilvl column on the items schema — item_to_row + upsert round-trip.

Postgres edition: DB tests lease an isolated scratch schema via the
``items_schema`` fixture (tests/fixtures/catalogues_db.py) and construct
``ItemCatalogue(items_schema)`` — the analog of the old
``ItemCatalogue(tmp_db)``. The former "missing DB file" case asserts the
EMPTY-schema behaviour instead.
"""

from __future__ import annotations

from backend.eq2db.items import ItemCatalogue

item_to_row = ItemCatalogue.item_to_row


def _raw_gear(*, item_type="Armor", tier="FABLED", leveltouse=100, potency=None, item_id=1, wieldstyle=None):
    modifiers = {}
    if potency is not None:
        modifiers["potency"] = {"value": potency, "displayname": "Potency"}
    item = {
        "id": item_id,
        "displayname": "Test Gear",
        "type": item_type,
        "tier": tier,
        "leveltouse": leveltouse,
        "modifiers": modifiers,
    }
    if wieldstyle is not None:
        item["typeinfo"] = {"wieldstyle": wieldstyle}
    return item


def _raw_two_hander(*, item_id=1):
    return _raw_gear(item_type="Weapon", item_id=item_id, wieldstyle="Two-Handed")


def test_item_to_row_gear_has_ilvl():
    # Fabled (5), level 100, no potency -> (1.0) * (300 + 23*5) = 415.
    assert item_to_row(_raw_gear())["ilvl"] == 415.0


def test_item_to_row_potency_boosts():
    assert item_to_row(_raw_gear(potency=1000.0))["ilvl"] > 415.0


def test_item_to_row_non_gear_is_none():
    assert item_to_row(_raw_gear(item_type="Spell Scroll"))["ilvl"] is None


def test_item_to_row_no_level_is_none():
    assert item_to_row(_raw_gear(leveltouse=0))["ilvl"] is None


def test_upsert_round_trip_persists_ilvl(items_schema):
    cat = ItemCatalogue(items_schema)
    with cat.init_db() as conn:
        cat.upsert_items(
            [
                _raw_gear(item_id=1, potency=480.0),  # 415 + 26*ln(480) = 575.5
                _raw_gear(item_id=2, item_type="House Item"),  # non-gear -> NULL
            ],
            conn,
        )
        rows = {r["id"]: r["ilvl"] for r in conn.execute("SELECT id, ilvl FROM items ORDER BY id").fetchall()}
    assert rows[1] == 575.5
    assert rows[2] is None


def test_ilvl_column_exists_in_fresh_schema(items_schema):
    # (was test_init_db_adds_ilvl_column_to_legacy_db — the SQLite-era
    # legacy-DB ALTER migration is retired; ilvl is a real column owned by
    # db/migrations/0007_items.sql.) A freshly leased schema already carries
    # the column, and a value round-trips through upsert.
    cat = ItemCatalogue(items_schema)
    with cat.init_db() as conn:
        cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'items'",
                (items_schema,),
            ).fetchall()
        }
        assert "ilvl" in cols
        cat.upsert_items([_raw_gear(item_id=7)], conn)  # Fabled lvl 100, no potency -> 415
        assert conn.execute("SELECT ilvl FROM items WHERE id = 7").fetchone()["ilvl"] == 415.0


def test_gear_for_ids_round_trip(items_schema):
    cat = ItemCatalogue(items_schema)
    with cat.init_db() as conn:
        cat.upsert_items(
            [
                _raw_gear(item_id=10, potency=480.0),  # gear -> numeric ilvl
                _raw_gear(item_id=20, item_type="Spell Scroll"),  # non-gear -> NULL ilvl
            ],
            conn,
        )
    result = ItemCatalogue(items_schema).gear_for_ids([10, 20, 999])  # 999 absent
    assert result[10][0] == 575.5  # (ilvl, wield_style)
    assert result[20][0] is None
    assert 999 not in result


def test_gear_for_ids_returns_wield_style(items_schema):
    cat = ItemCatalogue(items_schema)
    with cat.init_db() as conn:
        cat.upsert_items([_raw_two_hander(item_id=30)], conn)
    assert ItemCatalogue(items_schema).gear_for_ids([30])[30][1] == "Two-Handed"


def test_gear_for_ids_empty_schema_returns_empty(items_schema):
    # (was test_gear_for_ids_missing_db_returns_empty — a nonexistent DB file
    # is no longer a concept; the equivalent is an EMPTY leased schema.)
    empty = ItemCatalogue(items_schema)
    assert empty.gear_for_ids([1, 2, 3]) == {}
    assert empty.gear_for_ids([]) == {}
    assert empty.ready() is False  # empty schema reads as not-loaded
