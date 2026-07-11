#!/usr/bin/env python3
"""
Backfill skill_type, spell_target, spell_range, spell_power_cost, spell_resistability
from existing raw_json — no re-download needed.

Usage:
    python scripts/backfill_spell_armor_fields.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH, catalogue

BATCH = 5_000


def _str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def main() -> None:
    conn = catalogue.init_db()
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print(f"Backfilling {total:,} rows…")

    updated = 0
    offset = 0

    while True:
        rows = conn.execute(
            "SELECT id, raw_json FROM items ORDER BY id LIMIT ? OFFSET ?",
            (BATCH, offset),
        ).fetchall()
        if not rows:
            break

        batch = []
        for item_id, raw in rows:
            d = json.loads(raw)
            ti = d.get("typeinfo") or {}
            batch.append(
                {
                    "id": item_id,
                    "skill_type": _str(ti.get("skilltype")),
                    "spell_target": _str(ti.get("spelltarget")),
                    "spell_range": _str(ti.get("spellrange")),
                    "spell_power_cost": _int(ti.get("spellpowercost")),
                    "spell_resistability": _str(ti.get("resistability")),
                }
            )

        conn.executemany(
            """
            UPDATE items SET
                skill_type          = :skill_type,
                spell_target        = :spell_target,
                spell_range         = :spell_range,
                spell_power_cost    = :spell_power_cost,
                spell_resistability = :spell_resistability
            WHERE id = :id
        """,
            batch,
        )
        conn.commit()

        updated += len(rows)
        offset += BATCH
        print(f"  {updated:,} / {total:,}")

    conn.close()
    print(f"\nDone. {updated:,} rows updated.")


if __name__ == "__main__":
    main()
