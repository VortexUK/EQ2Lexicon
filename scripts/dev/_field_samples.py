"""Sample values for unmapped fields to decide if they're worth storing."""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

conn = sqlite3.connect(DB_PATH)


def sample_typeinfo_field(field, limit=5000):
    rows = conn.execute(
        "SELECT raw_json FROM items WHERE raw_json IS NOT NULL ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()
    counter = Counter()
    examples = []
    for (raw,) in rows:
        d = json.loads(raw)
        ti = d.get("typeinfo") or {}
        if field in ti:
            v = ti[field]
            counter[str(v)[:60]] += 1
            if len(examples) < 3:
                examples.append((d.get("displayname"), v))
    return counter, examples


def sample_top_field(field, limit=5000):
    rows = conn.execute(
        "SELECT raw_json FROM items WHERE raw_json IS NOT NULL ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()
    counter = Counter()
    examples = []
    for (raw,) in rows:
        d = json.loads(raw)
        if field in d:
            v = d[field]
            counter[str(v)[:60]] += 1
            if len(examples) < 3:
                examples.append((d.get("displayname"), v))
    return counter, examples


fields_ti = [
    "skilltype",
    "knowledgename",
    "spellrange",
    "spellpowercost",
    "spelltarget",
    "spellrecoverytime",
    "charges",
    "resistability",
    "equip_optional",
    "item_list",
    "recipe_list",
]

fields_top = ["individual_drop", "dungeon_item_id", "bonusmod_list"]

for f in fields_ti:
    counter, examples = sample_typeinfo_field(f)
    print(f"\n--- typeinfo.{f} ---")
    for val, cnt in counter.most_common(8):
        print(f"  {cnt:>5}x  {val}")
    for name, val in examples:
        print(f"  e.g. '{name}': {str(val)[:80]}")

for f in fields_top:
    counter, examples = sample_top_field(f)
    print(f"\n--- top.{f} ---")
    for val, cnt in counter.most_common(8):
        print(f"  {cnt:>5}x  {val}")
    for name, val in examples:
        print(f"  e.g. '{name}': {str(val)[:80]}")

conn.close()
