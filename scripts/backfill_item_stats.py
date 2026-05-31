#!/usr/bin/env python
"""
Backfill the item_stats table from raw_json stored in items.db.

Run once after upgrading to the version that added item_stats, or any time
you want to rebuild the stats index from scratch.

    python scripts/backfill_item_stats.py [--rebuild]

--rebuild  : DROP and recreate item_stats before filling (default: incremental)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import sqlite3

from backend.eq2db.items import DB_PATH, extract_item_stats, init_db


def run(rebuild: bool = False) -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH} — nothing to backfill.")
        return

    print(f"Opening {DB_PATH}")
    conn = init_db(DB_PATH)  # ensures item_stats table + indexes exist

    if rebuild:
        print("Rebuilding: dropping existing item_stats rows…")
        conn.execute("DELETE FROM item_stats")
        conn.commit()

    # Count total
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print(f"Processing {total:,} items…")

    batch_size = 2000
    inserted = 0
    offset = 0

    while True:
        rows = conn.execute(
            "SELECT id, raw_json FROM items LIMIT ? OFFSET ?",
            (batch_size, offset),
        ).fetchall()
        if not rows:
            break

        stat_rows: list[tuple] = []
        for item_id, raw_text in rows:
            if not raw_text:
                continue
            try:
                raw = json.loads(raw_text)
            except Exception:
                continue
            for stat_name, value in extract_item_stats(raw).items():
                stat_rows.append((item_id, stat_name, value))

        if stat_rows:
            conn.executemany(
                "INSERT OR REPLACE INTO item_stats (item_id, stat, value) VALUES (?, ?, ?)",
                stat_rows,
            )
            conn.commit()
            inserted += len(stat_rows)

        offset += batch_size
        done = min(offset, total)
        print(f"  {done:,}/{total:,} items processed  ({inserted:,} stat rows so far)", end="\r")

    print(f"\nDone — {inserted:,} rows in item_stats.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill item_stats table")
    parser.add_argument("--rebuild", action="store_true", help="Drop existing rows first")
    args = parser.parse_args()
    run(rebuild=args.rebuild)
