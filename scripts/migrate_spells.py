"""RETIRED — do not run (one-time spells schema migration).

The spells schema is owned by db/migrations/0008_spells.sql, and scripts/download_spells.py
writes every derived column (``base_name``, ``base_name_lower``, ``passes_spellcheck``) at
upsert time.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era schema migration — the spells schema is owned by "
        "db/migrations/ and scripts/download_spells.py computes the derived columns at write time."
    )
