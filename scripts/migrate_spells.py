#!/usr/bin/env python3
"""
Migrate spells.db to the updated schema:

  - Drop  raw_json        (was ~317 MB — no longer needed)
  - Drop  spellbook       (redundant with type)
  - Add   base_name       TEXT  — Roman-numeral suffix stripped from name
  - Add   base_name_lower TEXT  — lowercase version of base_name
  - Add   passes_spellcheck INTEGER — pre-computed spellcheck eligibility flag
  - Add   composite indexes (passes_spellcheck, level) and (base_name_lower, tier)

The migration rebuilds the table in-place using the classic SQLite
rename-copy-drop dance, so it works on any SQLite version.

Safe to re-run: detects whether the new columns already exist and skips
the rebuild if the schema is already current.

Usage:
    python scripts/migrate_spells.py
    python scripts/migrate_spells.py --db path/to/custom/spells.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.eq2db._meta import create_table as _create_meta_table
from backend.eq2db.spells import _SQL, DB_PATH, SpellCatalogue

strip_roman = SpellCatalogue.strip_roman
_passes_spellcheck = SpellCatalogue._passes_spellcheck

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _already_migrated(conn: sqlite3.Connection) -> bool:
    """Return True if the new columns are already present."""
    cols = _column_names(conn, "spells")
    return "base_name" in cols and "passes_spellcheck" in cols and "raw_json" not in cols


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

_BATCH = 10_000  # rows per INSERT batch


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"ERROR: {db_path} does not exist. Run download_spells.py first.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous  = NORMAL;")
    conn.execute("PRAGMA cache_size   = -65536;")  # 64 MB page cache

    if _already_migrated(conn):
        print("Schema is already up to date — nothing to do.")
        conn.close()
        return

    old_cols = _column_names(conn, "spells")
    has_raw = "raw_json" in old_cols

    total = conn.execute("SELECT COUNT(*) FROM spells").fetchone()[0]
    print(f"DB path:      {db_path}")
    print(f"Rows to migrate: {total:,}")
    print(f"Has raw_json: {has_raw}")
    print()

    # ---- 1. Create the new table ----------------------------------------
    print("Step 1/4  Creating spells_new …")
    conn.execute("DROP TABLE IF EXISTS spells_new;")
    conn.execute(_SQL["schema_spells"].replace("spells", "spells_new", 1))
    conn.commit()

    # ---- 2. Migrate rows in batches ---------------------------------------
    print(f"Step 2/4  Migrating rows in batches of {_BATCH:,} …")

    # We need the old column set to build a safe SELECT
    old_select = [
        "id",
        "name",
        "name_lower" if "name_lower" in old_cols else "lower(name) AS name_lower",
        "tier",
        "tier_name",
        "type",
        "typeid",
        "level",
        "given_by",
        "crc",
        "beneficial",
        "cast_secs",
        "recast_secs",
        "recovery_secs",
        "target_type",
        "aoe_radius",
        "max_targets",
        "description",
        "icon_id",
        "icon_backdrop",
        "last_update",
    ]
    select_sql = "SELECT " + ", ".join(old_select) + " FROM spells ORDER BY id"

    insert_sql = """
        INSERT INTO spells_new (
            id, name, name_lower, base_name, base_name_lower,
            tier, tier_name, type, typeid, level, given_by, crc, beneficial,
            passes_spellcheck,
            cast_secs, recast_secs, recovery_secs,
            target_type, aoe_radius, max_targets,
            description, icon_id, icon_backdrop, last_update
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?
        )
    """

    t0 = time.monotonic()
    written = 0
    batch = []

    cursor = conn.execute(select_sql)
    while True:
        chunk = cursor.fetchmany(_BATCH)
        if not chunk:
            break

        for r in chunk:
            (
                sid,
                name,
                name_lower,
                tier,
                tier_name,
                typ,
                typeid,
                level,
                given_by,
                crc,
                beneficial,
                cast_secs,
                recast_secs,
                recovery_secs,
                target_type,
                aoe_radius,
                max_targets,
                description,
                icon_id,
                icon_backdrop,
                last_update,
            ) = r

            name = name or ""
            name_lower = (name_lower or name).lower()
            base = strip_roman(name)
            base_lower = base.lower()

            # Build a minimal dict for the eligibility check
            eligibility = {
                "level": level,
                "type": typ,
                "given_by": given_by,
            }
            psc = _passes_spellcheck(eligibility)

            batch.append(
                (
                    sid,
                    name,
                    name_lower,
                    base,
                    base_lower,
                    tier,
                    tier_name,
                    typ,
                    typeid,
                    level,
                    given_by,
                    crc,
                    beneficial,
                    psc,
                    cast_secs,
                    recast_secs,
                    recovery_secs,
                    target_type,
                    aoe_radius,
                    max_targets,
                    description,
                    icon_id,
                    icon_backdrop,
                    last_update,
                )
            )

        conn.executemany(insert_sql, batch)
        conn.commit()
        written += len(batch)
        batch = []
        elapsed = time.monotonic() - t0
        pct = 100.0 * written / total if total else 0
        print(f"  {written:>8,} / {total:,}  ({pct:.1f}%)  {elapsed:.1f}s")

    if batch:
        conn.executemany(insert_sql, batch)
        conn.commit()
        written += len(batch)

    print(f"  Migrated {written:,} rows total.")

    # ---- 3. Swap tables --------------------------------------------------
    print("Step 3/4  Swapping tables …")
    conn.execute("DROP TABLE spells;")
    conn.execute("ALTER TABLE spells_new RENAME TO spells;")
    conn.commit()

    # ---- 4. Rebuild indexes + meta table ---------------------------------
    print("Step 4/4  Building indexes …")
    _create_meta_table(conn)
    conn.executescript(_SQL["indexes_spells"])
    conn.commit()

    # VACUUM to reclaim space freed by raw_json removal
    print("Running VACUUM (may take a minute) …")
    conn.execute("VACUUM;")

    conn.close()

    size_mb = db_path.stat().st_size / 1_048_576
    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s.  DB size: {size_mb:.1f} MB")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate spells.db to the new schema.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"Path to spells.db (default: {DB_PATH})")
    args = parser.parse_args()
    migrate(args.db)
