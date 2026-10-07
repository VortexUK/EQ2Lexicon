"""Backfill the ``out_level`` column in the recipes schema — no re-download.

The crafting tier (T1–T14) shown on the recipe page is derived from the level of
the item a recipe makes, NOT from the fuel name (the old fuel-prefix heuristic was
wrong for ~79% of recipes — the same adjective, e.g. "Smoldering", appears across
wildly different tiers depending on the fuel type). This backfill resolves each
recipe's crafted-output level from the items schema and stores it on
``recipes.out_level``; the API then maps level → tier.

Output level is resolved from the first leveled output in priority order:
``out_elaborate_id`` (the named-quality scroll for spell recipes) →
``out_worked_id`` → ``out_formed_id`` → ``out_simple_id``. Levels < 1 are treated
as "no level" (intermediate components, etc.), leaving ``out_level`` NULL.

Idempotent. By default only fills rows where ``out_level IS NULL`` (cheap on
re-run); pass ``--rebuild`` / ``rebuild=True`` to recompute every row after an
items refresh.

Both catalogue families live in the one Postgres database (DATABASE_URL), so
the old "load 372k item levels into a Python dict, then executemany UPDATEs"
stitch is now a single cross-schema ``UPDATE … FROM``.

Usage:

    uv run python scripts/backfill_recipe_levels.py
    uv run python scripts/backfill_recipe_levels.py --rebuild
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.eq2db.items import catalogue as items_catalogue  # noqa: E402
from backend.eq2db.recipes import catalogue as recipes_catalogue  # noqa: E402

# One statement replaces the old per-recipe dict lookup. Each output-id column
# LEFT JOINs the items table restricted to leveled items (level_to_use >= 1),
# and COALESCE picks the first hit in priority order — elaborate first: for
# spell-scroll recipes it points at the named scroll (the simple/worked/formed
# ids often point at the fuel/component instead). A NULL id, an id missing
# from items, and an item with level < 1 all fall through to the next column,
# exactly like the old `next((levels[i] for i in out_ids if i in levels), None)`.
# RETURNING reports whether each processed row resolved a level.
_UPDATE_SQL = """
UPDATE recipes AS r
SET out_level = sub.lvl
FROM (
    SELECT r2.id AS rid,
           COALESCE(ie.level_to_use, iw.level_to_use, ifo.level_to_use, isi.level_to_use) AS lvl
    FROM recipes AS r2
    LEFT JOIN {items}.items AS ie  ON ie.id  = r2.out_elaborate_id AND ie.level_to_use  >= 1
    LEFT JOIN {items}.items AS iw  ON iw.id  = r2.out_worked_id    AND iw.level_to_use  >= 1
    LEFT JOIN {items}.items AS ifo ON ifo.id = r2.out_formed_id    AND ifo.level_to_use >= 1
    LEFT JOIN {items}.items AS isi ON isi.id = r2.out_simple_id    AND isi.level_to_use >= 1
    {where}
) AS sub
WHERE r.id = sub.rid
RETURNING (sub.lvl IS NOT NULL) AS has_level
"""


def run(rebuild: bool = False) -> tuple[int, int]:
    """Backfill recipes.out_level from the items schema. Returns
    (rows_processed, rows_with_level).

    Returns (0, 0) when the items catalogue holds no rows yet (the Postgres
    analogue of the old "catalogue file absent" soft exit) — never wipes
    existing out_level values against an unloaded items schema.
    """
    if not items_catalogue.ready():
        return 0, 0

    conn = recipes_catalogue.init_db()
    try:
        sql = _UPDATE_SQL.format(
            items=items_catalogue.schema,  # cross-schema reference, e.g. items.items
            where="" if rebuild else "WHERE r2.out_level IS NULL",
        )
        rows = conn.execute(sql).fetchall()
        conn.commit()
        with_level = sum(1 for row in rows if row["has_level"])
        return len(rows), with_level
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the out_level column in the recipes schema")
    ap.add_argument("--rebuild", action="store_true", help="Recompute every row (default: only NULL out_level)")
    args = ap.parse_args()

    if not items_catalogue.ready():
        print(f"items schema '{items_catalogue.schema}' holds no rows — cannot resolve recipe levels.")
        sys.exit(1)

    print(f"Backfilling out_level in schema '{recipes_catalogue.schema}' (rebuild={args.rebuild}) ...")
    processed, with_level = run(rebuild=args.rebuild)
    print(f"Done. {processed:,} recipes processed, {with_level:,} now carry an out_level.")


if __name__ == "__main__":
    main()
