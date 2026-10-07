"""RETIRED — one-time SQLite-era schema migration (the spells catalogue).

Rebuilt the old spells catalogue in place: dropped ``raw_json``/``spellbook``,
added ``base_name``/``base_name_lower``/``passes_spellcheck`` and the
composite indexes, via the classic rename-copy-drop dance. Superseded by
the Postgres ``spells`` schema (db/migrations/0008_spells.sql): the schema
is migration-owned and ``scripts/download_spells.py`` writes every derived
column at upsert time.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: one-time SQLite-era schema migration — the spells schema is owned by "
        "db/migrations/ and scripts/download_spells.py computes the derived columns at write time."
    )
