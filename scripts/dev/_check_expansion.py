import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

conn = sqlite3.connect(DB_PATH)

# Check classification_list — is it ever populated?
print("=== classification_list samples (where not empty) ===")
rows = conn.execute("SELECT raw_json FROM items WHERE raw_json IS NOT NULL LIMIT 5000").fetchall()
shown = 0
for (raw,) in rows:
    d = json.loads(raw)
    cl = d.get("classification_list")
    if cl:
        print(f"  name={d.get('displayname')}")
        print(f"  classification_list={cl}")
        print()
        shown += 1
        if shown >= 5:
            break
if not shown:
    print("  (none found in sample of 5000)")

# Check _extended for anything expansion-like
print("\n=== _extended keys across sample ===")
ext_keys = set()
for (raw,) in rows:
    ext = json.loads(raw).get("_extended") or {}
    ext_keys.update(ext.keys())
print(f"  keys: {sorted(ext_keys)}")

# item_level distribution — useful proxy for expansion
print("\n=== item_level distribution (top 20 values) ===")
lvl_rows = conn.execute("""
    SELECT item_level, COUNT(*) as cnt
    FROM items
    WHERE item_level IS NOT NULL
    GROUP BY item_level
    ORDER BY item_level DESC
    LIMIT 20
""").fetchall()
for lvl, cnt in lvl_rows:
    print(f"  item_level={lvl:<6} count={cnt:,}")

conn.close()
