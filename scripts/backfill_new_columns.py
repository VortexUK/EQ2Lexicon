#!/usr/bin/env python3
"""
Backfill newly-added columns (visible, typeinfo_name, classes_json,
physical_damage_absorption) from the existing raw_json column.

Safe to re-run: uses UPDATE ... WHERE id = ?, skips nothing.

Usage:
    python scripts/backfill_new_columns.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH, compute_class_label, init_db

BATCH_SIZE = 5_000

UPDATE_SQL = """
UPDATE items SET
    visible                    = :visible,
    typeinfo_name              = :typeinfo_name,
    classes_json               = :classes_json,
    physical_damage_absorption = :physical_damage_absorption,
    class_label                = :class_label,
    class_count                = :class_count
WHERE id = :id
"""


def extract(raw: dict) -> dict:
    typeinfo = raw.get("typeinfo") or {}
    classes = typeinfo.get("classes") if isinstance(typeinfo, dict) else None
    pda = typeinfo.get("physicaldamageabsorption")

    visible_raw = raw.get("visible")
    try:
        visible = int(visible_raw) if visible_raw is not None else 1
    except (ValueError, TypeError):
        visible = 1

    name = typeinfo.get("name")
    if isinstance(name, dict):
        name = None
    elif name is not None:
        name = str(name).strip() or None

    try:
        pda_int = int(pda) if pda is not None else None
    except (ValueError, TypeError):
        pda_int = None

    return {
        "id": raw["id"],
        "visible": visible,
        "typeinfo_name": name,
        "classes_json": json.dumps(classes) if classes is not None else None,
        "physical_damage_absorption": pda_int,
        "class_label": compute_class_label(classes),
        "class_count": len(classes) if classes else None,
    }


def main() -> None:
    conn = init_db(DB_PATH)  # also runs ALTER TABLE migrations if columns are missing

    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print(f"Backfilling {total:,} rows…")

    offset = 0
    processed = 0

    while True:
        rows = conn.execute("SELECT id, raw_json FROM items LIMIT ? OFFSET ?", (BATCH_SIZE, offset)).fetchall()
        if not rows:
            break

        batch = [extract(json.loads(r[1])) for r in rows]
        conn.executemany(UPDATE_SQL, batch)
        conn.commit()

        processed += len(rows)
        offset += BATCH_SIZE
        pct = processed / total * 100
        print(f"  {processed:>7,} / {total:,}  ({pct:.1f}%)")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
