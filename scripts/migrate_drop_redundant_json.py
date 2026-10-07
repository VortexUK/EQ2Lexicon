#!/usr/bin/env python3
"""RETIRED — do not run (one-time items migration).

The items schema (db/migrations/0007_items.sql) has no redundant ``*_json`` blob columns;
everything they held is in ``raw_json``.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era migration — the Postgres items schema "
        "(db/migrations/0007_items.sql) never had the redundant JSON columns."
    )
