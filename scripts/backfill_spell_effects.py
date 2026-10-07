#!/usr/bin/env python3
"""RETIRED — do not run (one-time spells backfill).

``SpellCatalogue.spell_to_row`` always writes ``effects`` ('[]' when Census has none, never
NULL). A full refresh is scripts/download_spells.py.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — effects is always written by the "
        "Postgres loader (backend/eq2db/spells.py spell_to_row); re-run "
        "scripts/download_spells.py for a refresh."
    )
