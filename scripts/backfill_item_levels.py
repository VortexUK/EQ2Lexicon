"""Backfill the ``ilvl`` column on items.db in place — no re-download.

Computes the item level for every wearable-gear row (Armor / Weapon / Shield)
from its ``level_to_use``, ``tier_display``, and Potency (read from the
``item_stats`` side-table), using the same ``census.item_level.compute_ilvl`` as
the live parser and the upsert path. Non-gear rows are left NULL.

Idempotent — re-run any time (after a download, or after retuning the formula
constants in ``census/item_level.py``). Then copy items.db to the Railway volume.

Usage:

    .venv/Scripts/python scripts/backfill_item_levels.py
    .venv/Scripts/python scripts/backfill_item_levels.py --db path/to/items.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.census.item_level import GEAR_TYPES, compute_ilvl  # noqa: E402
from backend.eq2db.items import DB_PATH, init_db  # noqa: E402

BATCH = 5000


def backfill(db_path: Path) -> tuple[int, int]:
    """Return (gear_rows_updated, rows_with_an_ilvl)."""
    conn = init_db(db_path)
    placeholders = ",".join("?" for _ in GEAR_TYPES)
    rows = conn.execute(
        f"""
        SELECT i.id, i.level_to_use, i.tier_display, i.type, i.wield_style, COALESCE(s.value, 0.0)
        FROM items i
        LEFT JOIN item_stats s ON s.item_id = i.id AND s.stat = 'Potency'
        WHERE i.type IN ({placeholders})
        """,
        tuple(GEAR_TYPES),
    ).fetchall()

    updates: list[tuple[float | None, int]] = []
    with_value = 0
    for item_id, level_to_use, tier_display, item_type, wield_style, potency in rows:
        ilvl = compute_ilvl(level_to_use, tier_display, potency, item_type, two_handed=wield_style == "Two-Handed")
        if ilvl is not None:
            with_value += 1
        updates.append((ilvl, item_id))

    for start in range(0, len(updates), BATCH):
        conn.executemany("UPDATE items SET ilvl = ? WHERE id = ?", updates[start : start + BATCH])
        conn.commit()
        print(f"  ...{min(start + BATCH, len(updates)):,}/{len(updates):,}", end="\r")

    conn.close()
    return len(updates), with_value


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the ilvl column on items.db")
    ap.add_argument("--db", type=Path, default=DB_PATH, help=f"items.db path (default: {DB_PATH})")
    args = ap.parse_args()

    print(f"Backfilling ilvl in {args.db} ...")
    gear, with_value = backfill(args.db)
    print(f"\nDone. {gear:,} gear rows processed, {with_value:,} now carry an ilvl.")


if __name__ == "__main__":
    main()
