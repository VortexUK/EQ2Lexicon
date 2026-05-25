"""Build the recipe_classes mapping in recipes.db from recipe-book items.

A recipe's tradeskill class is NOT in the recipe JSON — recipes only carry a
shared crafting station (`bench`), so e.g. Armorer and Weaponsmith both use the
forge. The class is only knowable from recipe-book items (`typeinfo.name ==
"recipescroll"`), whose typeinfo carries:

  - classes:     {"armorer": {"displayname": "Armorer", "level": 20, ...}}
  - recipe_list: [{"id": <recipe id>, "name": "Steel Kite Shield"}, ...]

This walks every recipe book in items.db and writes (recipe_id, class) rows
into recipes.db's recipe_classes table (rebuilt from scratch each run). A recipe
taught by both an Armorer and a Weaponsmith book gets a row for each, so it
shows under either class filter on the recipes page.

Usage:
    uv run python scripts/build_recipe_classes.py
    uv run python scripts/build_recipe_classes.py --items-db ... --recipes-db ...
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Allow running from repo root (`uv run python scripts/build_recipe_classes.py`)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from census.db import DB_PATH as ITEMS_DB  # noqa: E402
from census.recipes_db import DB_PATH as RECIPES_DB  # noqa: E402
from census.recipes_db import init_db  # noqa: E402

# The nine EQ2 tradeskill classes. A few "recipescroll" books (≈72 food/quest
# books, 1 recipe each) carry *adventure* classes in typeinfo.classes instead
# (Wizard, Templar, …) — noise for a crafting filter — so we whitelist the real
# crafting classes and ignore anything else.
TRADESKILL_CLASSES = frozenset(
    {
        "Armorer",
        "Weaponsmith",
        "Tailor",
        "Carpenter",
        "Provisioner",
        "Woodworker",
        "Sage",
        "Alchemist",
        "Jeweler",
    }
)


def build(items_db: str, recipes_db: str) -> None:
    # Read recipe-book items (read-only — never mutate the item catalogue).
    src = sqlite3.connect(f"file:{items_db}?mode=ro", uri=True)
    pairs: set[tuple[int, str]] = set()
    books = 0
    skipped = 0
    try:
        cur = src.execute("SELECT raw_json FROM items WHERE typeinfo_name = 'recipescroll'")
        for (raw,) in cur:
            if not raw:
                continue
            typeinfo = json.loads(raw).get("typeinfo") or {}
            classes = typeinfo.get("classes") or {}
            recipe_list = typeinfo.get("recipe_list") or []
            if not classes or not recipe_list:
                skipped += 1
                continue
            class_names = [
                name
                for key, v in classes.items()
                if (
                    name := (v.get("displayname") if isinstance(v, dict) and v.get("displayname") else key.capitalize())
                )
                in TRADESKILL_CLASSES
            ]
            if not class_names:
                skipped += 1
                continue
            books += 1
            for entry in recipe_list:
                rid = entry.get("id")
                if rid is None:
                    continue
                for cls in class_names:
                    pairs.add((int(rid), cls))
    finally:
        src.close()

    conn = init_db(Path(recipes_db))  # ensures recipe_classes table/indexes exist
    try:
        conn.execute("DELETE FROM recipe_classes")
        conn.executemany(
            "INSERT OR IGNORE INTO recipe_classes (recipe_id, class) VALUES (?, ?)",
            sorted(pairs),
        )
        conn.commit()
        distinct_recipes = conn.execute("SELECT COUNT(DISTINCT recipe_id) FROM recipe_classes").fetchone()[0]
        matched = conn.execute(
            "SELECT COUNT(DISTINCT rc.recipe_id) FROM recipe_classes rc JOIN recipes r ON r.id = rc.recipe_id"
        ).fetchone()[0]
        multi = conn.execute(
            "SELECT COUNT(*) FROM (SELECT recipe_id FROM recipe_classes GROUP BY recipe_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    finally:
        conn.close()

    per_class = Counter(cls for _, cls in pairs)
    print(f"recipe books scanned:      {books}  (skipped {skipped} with no classes/recipes)")
    print(f"(recipe_id, class) pairs:  {len(pairs)}")
    print(f"distinct recipes mapped:   {distinct_recipes}  (matched in recipes table: {matched})")
    print(f"recipes in >1 class:       {multi}")
    print("per class:")
    for cls, n in per_class.most_common():
        print(f"  {n:6d}  {cls}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build recipe_classes mapping from recipe-book items.")
    ap.add_argument("--items-db", default=str(ITEMS_DB), help="path to items.db (read-only source)")
    ap.add_argument("--recipes-db", default=str(RECIPES_DB), help="path to recipes.db (mapping written here)")
    args = ap.parse_args()
    build(args.items_db, args.recipes_db)


if __name__ == "__main__":
    main()
