#!/usr/bin/env python3
"""
Quick schema analysis — checks what top-level and typeinfo keys exist
in the DB's raw_json column vs what the current schema already covers.

Usage:
    python scripts/analyse_schema.py           # sample 5000 random items
    python scripts/analyse_schema.py --all     # scan every row (slow on large DBs)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

KNOWN_FLAT = {
    "id",
    "displayname",
    "gamelink",
    "description",
    "last_update",
    "tier",
    "tierid",
    "type",
    "typeid",
    "itemlevel",
    "leveltouse",
    "planar_level",
    "iconid",
    "maxstacksize",
    "maxcharges",
    "associatedquest",
    "autoquest",
    "modifiers",
    "typeinfo",
    "effect_list",
    "adornmentslot_list",
    "adornment_list",
    "classification_list",
    "slot_list",
    "setbonus_list",
    "flags",
    "requiredskill",
    "setbonus_info",
    "unique_equipment_group",
    "_extended",
}

KNOWN_TI = {
    "minarmorclass",
    "maxarmorclass",
    "mindamage",
    "maxdamage",
    "damage",
    "damagetype",
    "damagetypeid",
    "damagerating",
    "delay",
    "wieldstyle",
    "spellname",
    "tier",
    "spellcasttime",
    "spellrecasttime",
    "spellduration",
    "minrange",
    "range",
    "duration",
    "satiation",
    "foodlevel",
    "color",
    "slots",
    "statusreduction",
}


def main(scan_all: bool) -> None:
    conn = sqlite3.connect(DB_PATH)

    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    offset_row = conn.execute("SELECT value FROM _meta WHERE key='download_offset'").fetchone()
    print(f"Items in DB  : {total:,}")
    print(f"Saved offset : {offset_row[0] if offset_row else 'none'}")
    print()

    if scan_all:
        rows = conn.execute("SELECT raw_json FROM items").fetchall()
        label = f"all {total:,}"
    else:
        limit = min(5000, total)
        rows = conn.execute("SELECT raw_json FROM items ORDER BY RANDOM() LIMIT ?", (limit,)).fetchall()
        label = f"random sample of {limit:,}"

    items = [json.loads(r[0]) for r in rows]
    n = len(items)
    print(f"Analysing {label} items…\n")

    # ── Top-level keys ──────────────────────────────────────────────────────
    top_keys: Counter = Counter()
    for item in items:
        top_keys.update(item.keys())

    new_top = {k for k in top_keys if k not in KNOWN_FLAT}
    print("=== TOP-LEVEL KEYS ===")
    for k, cnt in top_keys.most_common():
        pct = cnt / n * 100
        flag = "  *** NEW ***" if k in new_top else ""
        print(f"  {k:<42} {cnt:>6} / {n}  ({pct:5.1f}%){flag}")

    # ── typeinfo sub-keys ───────────────────────────────────────────────────
    ti_keys: Counter = Counter()
    for item in items:
        ti = item.get("typeinfo") or {}
        if isinstance(ti, dict):
            ti_keys.update(ti.keys())

    new_ti = {k for k in ti_keys if k not in KNOWN_TI}
    print("\n=== TYPEINFO SUB-KEYS ===")
    for k, cnt in ti_keys.most_common():
        pct = cnt / n * 100
        flag = "  *** NEW ***" if k in new_ti else ""
        print(f"  {k:<42} {cnt:>6} ({pct:5.1f}%){flag}")

    # ── summary ────────────────────────────────────────────────────────────
    print(f"\n=== SUMMARY ===")
    if new_top:
        print(f"  {len(new_top)} new top-level key(s) not in schema: {sorted(new_top)}")
    else:
        print("  No new top-level keys.")
    if new_ti:
        print(f"  {len(new_ti)} new typeinfo key(s) not in schema: {sorted(new_ti)}")
    else:
        print("  No new typeinfo keys.")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Scan all rows instead of a random sample")
    args = parser.parse_args()
    main(args.all)
