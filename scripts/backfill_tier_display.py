"""RETIRED — one-time SQLite-era backfill (the items catalogue).

Filled the then-new ``tier_display`` column (``tier`` with a ``'COMMON'``
default for null/empty tiers). Superseded by the Postgres ``items`` schema
(db/migrations/0007_items.sql): ``ItemCatalogue.item_to_row`` computes
``tier_display`` at write time, and historic rows arrived pre-backfilled via
the one-time bulk copy.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — tier_display is computed at write time "
        "by the Postgres loader (backend/eq2db/items.py item_to_row)."
    )
