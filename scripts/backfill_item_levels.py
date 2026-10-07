"""RETIRED — do not run (one-time items backfill).

``ilvl`` is computed at write time by ``ItemCatalogue.item_to_row`` (``compute_ilvl``). After
retuning the formula in ``backend/census/item_level.py``, re-run scripts/download_items.py
(or write a dedicated Postgres backfill).
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — ilvl is computed at write time by the "
        "Postgres loader (backend/eq2db/items.py item_to_row → compute_ilvl)."
    )
