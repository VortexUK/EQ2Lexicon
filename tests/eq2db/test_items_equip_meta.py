"""find_equip_meta_by_ids — the narrow read equipment parsing uses.

It replaced a read of raw_json (about 3.7 KB an item, on every character
parse), so what matters is that it answers exactly what the full row would
for the three fields the parser uses.
"""

from __future__ import annotations

import pytest

from backend.eq2db.items import ItemCatalogue


def _raw(item_id: int, name: str, tier: str, **extra) -> dict:
    return {"id": item_id, "displayname": name, "type": "Armor", "tier": tier, "leveltouse": 80, **extra}


@pytest.mark.asyncio
async def test_equip_meta_agrees_with_the_full_row(items_schema):
    cat = ItemCatalogue(items_schema)
    raws = [
        _raw(1, "Test Helm", "FABLED", iconid=4321),
        _raw(2, "Test Adornment", "LEGENDARY"),  # no icon in Census
    ]
    with cat.init_db() as conn:
        cat.upsert_items(raws, conn)
        conn.commit()

    got = await cat.find_equip_meta_by_ids([1, 2, 999, 0])

    assert set(got) == {1, 2}  # unknown and zero ids are simply absent
    assert got[1] == {"displayname": "Test Helm", "tier": "FABLED", "iconid": 4321}
    assert got[2]["displayname"] == "Test Adornment"
    assert not got[2]["iconid"]  # the parser reads a falsy icon as "none"
    for raw in raws:
        full = await cat.find_by_id(raw["id"])
        assert full is not None
        assert got[raw["id"]]["displayname"] == full["displayname"]
        assert got[raw["id"]]["tier"] == full["tier"]


@pytest.mark.asyncio
async def test_equip_meta_with_no_ids_does_not_query(items_schema):
    assert await ItemCatalogue(items_schema).find_equip_meta_by_ids([]) == {}
    assert await ItemCatalogue(items_schema).find_equip_meta_by_ids([0]) == {}
