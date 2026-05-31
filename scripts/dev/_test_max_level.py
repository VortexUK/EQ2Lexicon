"""Quick test: verify SERVER_MAX_LEVEL filtering logic against the live DB."""

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Simulate max level = 70
os.environ["SERVER_MAX_LEVEL"] = "70"

import importlib

import backend.eq2db.items as db

importlib.reload(db)  # pick up the env var we just set

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row

NAME = "focus: potency"

all_rows = conn.execute(
    "SELECT id, displayname, level_to_use, tierid, tier FROM items"
    " WHERE displayname_lower = ? ORDER BY level_to_use DESC, tierid DESC",
    (NAME,),
).fetchall()

print(f"All '{NAME}' variants ({len(all_rows)} total):")
valid = [r for r in all_rows if r["level_to_use"] is None or r["level_to_use"] <= 70]
invalid = [r for r in all_rows if r["level_to_use"] is not None and r["level_to_use"] > 70]
print(f"  Valid for level<=70: {len(valid)}   |   Invalid: {len(invalid)}")
print()

if valid:
    r = valid[0]
    print(
        f"Expected winner (highest valid): id={r['id']} level={r['level_to_use']} tier={r['tier']} tierid={r['tierid']}"
    )
elif invalid:
    r = invalid[0]
    print(
        f"No valid items — fallback winner: id={r['id']} level={r['level_to_use']} tier={r['tier']} tierid={r['tierid']}"
    )

conn.close()
