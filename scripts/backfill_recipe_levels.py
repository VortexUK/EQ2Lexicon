"""Backfill the ``out_level`` column on recipes.db in place — no re-download.

The crafting tier (T1–T14) shown on the recipe page is derived from the level of
the item a recipe makes, NOT from the fuel name (the old fuel-prefix heuristic was
wrong for ~79% of recipes — the same adjective, e.g. "Smoldering", appears across
wildly different tiers depending on the fuel type). This backfill resolves each
recipe's crafted-output level from items.db and stores it on ``recipes.out_level``;
the API then maps level → tier.

Output level is resolved from the first leveled output in priority order:
``out_elaborate_id`` (the named-quality scroll for spell recipes) →
``out_worked_id`` → ``out_formed_id`` → ``out_simple_id``. Levels < 1 are treated
as "no level" (intermediate components, etc.), leaving ``out_level`` NULL.

Idempotent. By default only fills rows where ``out_level IS NULL`` (cheap on
re-run); pass ``--rebuild`` / ``rebuild=True`` to recompute every row after an
items.db refresh.

Usage:

    .venv/Scripts/python scripts/backfill_recipe_levels.py
    .venv/Scripts/python scripts/backfill_recipe_levels.py --rebuild
    .venv/Scripts/python scripts/backfill_recipe_levels.py --recipes-db path/to/recipes.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.eq2db.items import DB_PATH as ITEMS_DB_PATH  # noqa: E402
from backend.eq2db.recipes import DB_PATH as RECIPES_DB_PATH  # noqa: E402
from backend.eq2db.recipes import RecipeCatalogue  # noqa: E402

BATCH = 5000

# Output-id columns in resolution priority. Elaborate first: for spell-scroll
# recipes it points at the named scroll (the simple/worked/formed ids often
# point at the fuel/component instead).
_OUTPUT_COLS = ("out_elaborate_id", "out_worked_id", "out_formed_id", "out_simple_id")


def _load_item_levels(items_path: Path) -> dict[int, int]:
    """Return {item_id: level_to_use} for leveled items (level >= 1)."""
    conn = sqlite3.connect(f"file:{items_path}?mode=ro", uri=True)
    try:
        return {iid: lvl for iid, lvl in conn.execute("SELECT id, level_to_use FROM items") if lvl and lvl >= 1}
    finally:
        conn.close()


def run(
    rebuild: bool = False,
    recipes_path: Path = RECIPES_DB_PATH,
    items_path: Path = ITEMS_DB_PATH,
) -> tuple[int, int]:
    """Backfill recipes.out_level from items.db. Returns (rows_processed, rows_with_level).

    Safe to call from a startup thread: returns (0, 0) if items.db is absent.
    """
    if not items_path.exists():
        return 0, 0

    levels = _load_item_levels(items_path)

    conn = RecipeCatalogue(recipes_path).init_db()  # runs the out_level migration if needed
    try:
        cols = ", ".join(_OUTPUT_COLS)
        where = "" if rebuild else " WHERE out_level IS NULL"
        rows = conn.execute(f"SELECT id, {cols} FROM recipes{where}").fetchall()

        updates: list[tuple[int | None, int]] = []
        with_level = 0
        for rid, *out_ids in rows:
            lvl = next((levels[i] for i in out_ids if i in levels), None)
            if lvl is not None:
                with_level += 1
            updates.append((lvl, rid))

        for start in range(0, len(updates), BATCH):
            conn.executemany("UPDATE recipes SET out_level = ? WHERE id = ?", updates[start : start + BATCH])
            conn.commit()
            print(f"  ...{min(start + BATCH, len(updates)):,}/{len(updates):,}", end="\r")

        return len(updates), with_level
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the out_level column on recipes.db")
    ap.add_argument(
        "--recipes-db", type=Path, default=RECIPES_DB_PATH, help=f"recipes.db path (default: {RECIPES_DB_PATH})"
    )
    ap.add_argument("--items-db", type=Path, default=ITEMS_DB_PATH, help=f"items.db path (default: {ITEMS_DB_PATH})")
    ap.add_argument("--rebuild", action="store_true", help="Recompute every row (default: only NULL out_level)")
    args = ap.parse_args()

    if not args.items_db.exists():
        print(f"items DB not found at {args.items_db} — cannot resolve recipe levels.")
        sys.exit(1)

    print(f"Backfilling out_level in {args.recipes_db} (rebuild={args.rebuild}) ...")
    processed, with_level = run(rebuild=args.rebuild, recipes_path=args.recipes_db, items_path=args.items_db)
    print(f"\nDone. {processed:,} recipes processed, {with_level:,} now carry an out_level.")


if __name__ == "__main__":
    main()
