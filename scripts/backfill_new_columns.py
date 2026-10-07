#!/usr/bin/env python3
"""RETIRED — one-time SQLite-era backfill (items.db).

Filled the then-new ``visible`` / ``typeinfo_name`` / ``classes_json`` /
``physical_damage_absorption`` / ``class_label`` columns from ``raw_json``
after a schema extension. Superseded by the Postgres ``items`` schema
(db/migrations/0007_items.sql): ``ItemCatalogue.item_to_row`` computes every
derived column at write time, and historic rows arrived pre-backfilled via
the one-time bulk copy.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — the Postgres loader computes these "
        "columns at write time (backend/eq2db/items.py item_to_row) and the bulk copy "
        "pre-filled historic rows."
    )
