#!/usr/bin/env python3
"""RETIRED — do not run (it read the items catalogue as a local file).

It reported which raw_json keys the flat item columns did not cover. Do the same with
ad-hoc SQL over ``items.items.raw_json`` (e.g. ``jsonb_object_keys(raw_json::jsonb)``).
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: SQLite-era dev one-off — the catalogue lives in Postgres now; "
        "analyse raw_json with ad-hoc SQL against the items schema instead."
    )
