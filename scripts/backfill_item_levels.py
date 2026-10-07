"""RETIRED — one-time SQLite-era backfill (the items catalogue).

Recomputed the ``ilvl`` column for wearable-gear rows after the formula
constants in ``backend/census/item_level.py`` were retuned. Superseded by the
Postgres ``items`` schema (db/migrations/0007_items.sql):
``ItemCatalogue.item_to_row`` computes ``ilvl`` via ``compute_ilvl`` at write
time, and historic rows arrived pre-backfilled via the one-time bulk copy.
Retuning the formula today means re-running scripts/download_items.py (or
adding a dedicated PG backfill) rather than this script.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — ilvl is computed at write time by the "
        "Postgres loader (backend/eq2db/items.py item_to_row → compute_ilvl)."
    )
