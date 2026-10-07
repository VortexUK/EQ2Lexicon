#!/usr/bin/env python3
"""RETIRED — one-time SQLite-era migration (items.db).

Dropped the redundant ``*_json`` blob columns (typeinfo_json, modifiers_json,
effect_list_json, …) that duplicated data already present in ``raw_json``
(~300 MB). The Postgres ``items`` schema (db/migrations/0007_items.sql) was
created without those columns, so there is nothing to drop.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era migration — the Postgres items schema "
        "(db/migrations/0007_items.sql) never had the redundant JSON columns."
    )
