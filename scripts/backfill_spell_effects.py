#!/usr/bin/env python3
"""RETIRED — one-time SQLite-era backfill (spells.db).

Fetched spells whose ``effects`` column was NULL from Census (batches of 50
ids) and filled just that column — needed once when ``effects`` was added
after the initial download. Superseded by the Postgres ``spells`` schema
(db/migrations/0008_spells.sql): ``SpellCatalogue.spell_to_row`` always
writes ``effects`` ('[]' when Census has none, never NULL), and historic
rows arrived pre-backfilled via the one-time bulk copy. A full refresh is
scripts/download_spells.py.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era backfill — effects is always written by the "
        "Postgres loader (backend/eq2db/spells.py spell_to_row); re-run "
        "scripts/download_spells.py for a refresh."
    )
