"""Quick test of the item search query logic, bypassing FastAPI."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite

from backend.eq2db.items import DB_PATH


async def test():
    has_stat = ["Ability Mod"]
    sort_by = "name"
    sort_dir = "asc"

    conditions = ["i.visible = 1"]
    params = []

    where = " AND ".join(conditions)

    stat_alias = {}
    stat_joins = ""
    for idx, stat in enumerate(has_stat):
        alias = f"s{idx}"
        stat_alias[stat] = alias
        stat_joins += f" JOIN item_stats {alias} ON i.id = {alias}.item_id AND {alias}.stat = ?"
        params.append(stat)

    print("stat_joins:", stat_joins)
    print("params:", params)

    _FIXED_SORT = {"level": "i.level_to_use", "tier": "i.tierid", "name": "i.displayname_lower"}
    sort_stat_col = None
    if sort_by in _FIXED_SORT:
        order_clause = f"{_FIXED_SORT[sort_by]} {sort_dir.upper()}, i.displayname_lower ASC"
    else:
        if sort_by in stat_alias:
            sort_stat_col = f"{stat_alias[sort_by]}.value"
        else:
            stat_joins += " LEFT JOIN item_stats ssort ON i.id = ssort.item_id AND ssort.stat = ?"
            params.append(sort_by)
            sort_stat_col = "ssort.value"
        order_clause = f"COALESCE({sort_stat_col}, 0) {sort_dir.upper()}, i.displayname_lower ASC"

    count_sql = f"SELECT COUNT(DISTINCT i.id) FROM items i{stat_joins} WHERE {where}"
    print("\ncount_sql:", count_sql)
    print("params at count time:", params)

    sort_val_select = f", COALESCE({sort_stat_col}, 0) AS _sort_val" if sort_stat_col else ""
    select_sql = (
        f"SELECT i.id, i.displayname, i.tier_display, i.slot, "
        f"i.typeinfo_name, i.level_to_use, i.class_label, i.icon_id"
        f"{sort_val_select} "
        f"FROM items i{stat_joins} "
        f"WHERE {where} "
        f"GROUP BY i.id "
        f"ORDER BY {order_clause} "
        f"LIMIT 5 OFFSET 0"
    )
    print("\nselect_sql:", select_sql)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(count_sql, params) as cur:
            total = (await cur.fetchone())[0]
        print(f"\ntotal: {total}")

        async with db.execute(select_sql, params) as cur:
            rows = await cur.fetchall()
        print(f"first {len(rows)} results:")
        for r in rows:
            print(f"  {dict(r)}")


asyncio.run(test())
