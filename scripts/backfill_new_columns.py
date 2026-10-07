#!/usr/bin/env python3
"""RETIRED — do not run (one-time items backfill).

The derived ``visible`` / ``typeinfo_name`` / ``classes_json`` /
``physical_damage_absorption`` / ``class_label`` columns are computed at write time by
``ItemCatalogue.item_to_row``.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — the Postgres loader computes these "
        "columns at write time (backend/eq2db/items.py item_to_row) and the bulk copy "
        "pre-filled historic rows."
    )
