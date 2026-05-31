"""Tests for the ilvl column on items.db — item_to_row + upsert round-trip."""

from __future__ import annotations

from backend.eq2db.items import init_db, item_to_row, upsert_items


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


def test_upsert_round_trip_persists_ilvl(tmp_path):
    conn = init_db(tmp_path / "items.db")
    try:
        upsert_items(
            [
                _raw_gear(item_id=1, potency=480.0),  # 415 + 26*ln(480) = 575.5
                _raw_gear(item_id=2, item_type="House Item"),  # non-gear -> NULL
            ],
            conn,
        )
        rows = dict(conn.execute("SELECT id, ilvl FROM items ORDER BY id").fetchall())
        assert rows[1] == 575.5
        assert rows[2] is None
    finally:
        conn.close()


def test_init_db_adds_ilvl_column_to_legacy_db(tmp_path):
    # A pre-existing DB without the column gains it on init_db (migration).
    # Simulate "legacy" by creating the full schema then dropping the column.
    import sqlite3

    path = tmp_path / "legacy.db"
    init_db(path).close()
    legacy = sqlite3.connect(path)
    legacy.execute("ALTER TABLE items DROP COLUMN ilvl")
    legacy.commit()
    legacy.close()
    assert "ilvl" not in {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(items)")}

    conn = init_db(path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        assert "ilvl" in cols
    finally:
        conn.close()


def test_gear_for_ids_round_trip(tmp_path):
    from backend.eq2db.items import gear_for_ids

    path = tmp_path / "items.db"
    conn = init_db(path)
    try:
        upsert_items(
            [
                _raw_gear(item_id=10, potency=480.0),  # gear -> numeric ilvl
                _raw_gear(item_id=20, item_type="Spell Scroll"),  # non-gear -> NULL ilvl
            ],
            conn,
        )
    finally:
        conn.close()
    result = gear_for_ids([10, 20, 999], path)  # 999 absent
    assert result[10][0] == 575.5  # (ilvl, wield_style)
    assert result[20][0] is None
    assert 999 not in result


def test_gear_for_ids_returns_wield_style(tmp_path):
    from backend.eq2db.items import gear_for_ids

    path = tmp_path / "items.db"
    conn = init_db(path)
    try:
        upsert_items([_raw_two_hander(item_id=30)], conn)
    finally:
        conn.close()
    assert gear_for_ids([30], path)[30][1] == "Two-Handed"


def test_gear_for_ids_missing_db_returns_empty(tmp_path):
    from backend.eq2db.items import gear_for_ids

    assert gear_for_ids([1, 2, 3], tmp_path / "nope.db") == {}
    assert gear_for_ids([], tmp_path / "whatever.db") == {}
