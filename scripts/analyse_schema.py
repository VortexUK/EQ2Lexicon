#!/usr/bin/env python3
"""RETIRED — SQLite-era dev one-off (opened the items catalogue directly).

Sampled ``raw_json`` rows and reported which top-level / typeinfo keys the
flat column schema did or didn't cover, to guide schema extensions. The
items catalogue now lives in the Postgres ``items`` schema
(db/migrations/0007_items.sql); an equivalent analysis is ad-hoc SQL over
``items.items.raw_json`` (e.g. ``jsonb_object_keys(raw_json::jsonb)``).

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: SQLite-era dev one-off — the catalogue lives in Postgres now; "
        "analyse raw_json with ad-hoc SQL against the items schema instead."
    )
