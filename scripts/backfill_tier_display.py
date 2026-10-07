"""RETIRED — do not run (one-time items backfill).

``tier_display`` (``tier`` with a ``'COMMON'`` default for null/empty tiers) is computed at
write time by ``ItemCatalogue.item_to_row``.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — tier_display is computed at write time "
        "by the Postgres loader (backend/eq2db/items.py item_to_row)."
    )
