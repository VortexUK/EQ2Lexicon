#!/usr/bin/env python3
"""
Drop redundant JSON blob columns that duplicate data already in raw_json.
The bot reads all item data through raw_json → _parse_item(), so these
columns are pure storage waste.

Columns removed (~300 MB):
  typeinfo_json, modifiers_json, effect_list_json, adornment_slots_json,
  adornment_list_json, classification_json, slot_list_json,
  setbonus_list_json, flags_json, classes_json

Kept:
  raw_json             — full Census response; used by _parse_item()
  class_label          — pre-computed display label
  class_count          — pre-computed count
  physical_damage_absorption, typeinfo_name, visible  — fast-filter columns
  ... all other flat columns

Usage:
    python scripts/migrate_drop_redundant_json.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

DROP_COLS = [
    "typeinfo_json",
    "modifiers_json",
    "effect_list_json",
    "adornment_slots_json",
    "adornment_list_json",
    "classification_json",
    "slot_list_json",
    "setbonus_list_json",
    "flags_json",
    "classes_json",
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    to_drop = [c for c in DROP_COLS if c in existing]

    if not to_drop:
        print("Nothing to drop — all redundant columns already removed.")
        conn.close()
        return

    page_before = conn.execute("PRAGMA page_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    print(f"DB size before : {page_before * page_size / 1024**2:.1f} MB")
    print(f"Dropping {len(to_drop)} column(s): {to_drop}")

    for col in to_drop:
        print(f"  DROP COLUMN {col} …", end=" ", flush=True)
        conn.execute(f"ALTER TABLE items DROP COLUMN {col}")
        conn.commit()
        print("done")

    print("Running VACUUM …", end=" ", flush=True)
    conn.execute("VACUUM")
    print("done")

    conn.execute("ANALYZE")
    conn.commit()

    page_after = conn.execute("PRAGMA page_count").fetchone()[0]
    saved = (page_before - page_after) * page_size / 1024**2
    print(f"DB size after  : {page_after * page_size / 1024**2:.1f} MB")
    print(f"Space saved    : {saved:.1f} MB")

    conn.close()


if __name__ == "__main__":
    main()
