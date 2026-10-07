#!/usr/bin/env python3
"""RETIRED — do not run (one-time items backfill).

``skill_type`` / ``spell_target`` / ``spell_range`` / ``spell_power_cost`` /
``spell_resistability`` are computed at write time by ``ItemCatalogue.item_to_row``.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — these columns are computed at write "
        "time by the Postgres loader (backend/eq2db/items.py item_to_row)."
    )
