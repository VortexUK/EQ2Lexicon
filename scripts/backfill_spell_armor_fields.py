#!/usr/bin/env python3
"""RETIRED — one-time SQLite-era backfill (the items catalogue).

Filled the then-new ``skill_type`` / ``spell_target`` / ``spell_range`` /
``spell_power_cost`` / ``spell_resistability`` columns from ``raw_json``
after a schema extension. Superseded by the Postgres ``items`` schema
(db/migrations/0007_items.sql): ``ItemCatalogue.item_to_row`` computes these
columns at write time, and historic rows arrived pre-backfilled via the
one-time bulk copy.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — these columns are computed at write "
        "time by the Postgres loader (backend/eq2db/items.py item_to_row)."
    )
