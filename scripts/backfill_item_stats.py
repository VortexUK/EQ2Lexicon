#!/usr/bin/env python
"""
Rebuild the item_stats side-table from raw_json stored in the items schema.

Run whenever the stat-extraction patterns change (STAT_MAP additions, new
_EFFECT_STAT_PATTERNS entries in backend/eq2db/items.py) — this re-extracts
both modifier-derived and effect-derived stats in place, no re-download. The
old SQLite startup backfills are gone: the loader computes stats at write
time, and this script is THE way to recompute them after a pattern change.

    uv run python scripts/backfill_item_stats.py [--rebuild]

--rebuild  : DELETE existing item_stats rows before filling (default:
             incremental). Use it when a pattern CHANGED its value or was
             removed — the incremental pass never overwrites an existing
             effect-derived row and never deletes stale ones.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.eq2db.items import catalogue  # noqa: E402

# Same split as ItemCatalogue.upsert_items: modifier-derived stats always win
# (DO UPDATE); effect-derived stats never overwrite them (DO NOTHING).
_UPSERT_MOD_STAT = (
    "INSERT INTO item_stats (item_id, stat, value) VALUES (%s, %s, %s) "
    "ON CONFLICT (item_id, stat) DO UPDATE SET value = excluded.value"
)
_INSERT_EFFECT_STAT = (
    "INSERT INTO item_stats (item_id, stat, value) VALUES (%s, %s, %s) ON CONFLICT (item_id, stat) DO NOTHING"
)


def run(rebuild: bool = False) -> None:
    if not catalogue.ready():
        print(f"items schema '{catalogue.schema}' holds no rows — nothing to backfill.")
        return

    print(f"Opening Postgres schema '{catalogue.schema}'")
    conn = catalogue.init_db()

    if rebuild:
        print("Rebuilding: dropping existing item_stats rows…")
        conn.execute("DELETE FROM item_stats")
        conn.commit()

    # Count total
    total = catalogue.fetchval(conn.execute("SELECT COUNT(*) FROM items"))
    print(f"Processing {total:,} items…")

    batch_size = 2000
    inserted = 0
    offset = 0

    while True:
        # ORDER BY id: Postgres gives no stable row order across separate
        # LIMIT/OFFSET statements without it (rows would repeat/vanish).
        rows = conn.execute(
            "SELECT id, raw_json FROM items ORDER BY id LIMIT %s OFFSET %s",
            (batch_size, offset),
        ).fetchall()
        if not rows:
            break

        mod_rows: list[tuple] = []
        effect_rows: list[tuple] = []
        for row in rows:
            item_id, raw_text = row["id"], row["raw_json"]
            if not raw_text:
                continue
            try:
                raw = json.loads(raw_text)
            except Exception:
                continue
            for stat_name, value in catalogue.extract_item_stats(raw).items():
                mod_rows.append((item_id, stat_name, value))
            for stat_name, value in catalogue.extract_effect_stats(raw).items():
                effect_rows.append((item_id, stat_name, value))

        if mod_rows:
            conn.executemany(_UPSERT_MOD_STAT, mod_rows)
        if effect_rows:
            conn.executemany(_INSERT_EFFECT_STAT, effect_rows)
        if mod_rows or effect_rows:
            conn.commit()
            inserted += len(mod_rows) + len(effect_rows)

        offset += batch_size
        done = min(offset, total)
        print(f"  {done:,}/{total:,} items processed  ({inserted:,} stat rows so far)", end="\r")

    print(f"\nDone — {inserted:,} stat rows written to item_stats.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild the item_stats table from raw_json")
    parser.add_argument("--rebuild", action="store_true", help="Drop existing rows first")
    args = parser.parse_args()
    run(rebuild=args.rebuild)
