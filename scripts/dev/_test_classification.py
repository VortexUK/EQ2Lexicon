import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH, init_db

print("Running init_db (will trigger backfill)...")
conn = init_db(DB_PATH)

cols = [r[1] for r in conn.execute("PRAGMA table_info(items)")]
print("classification_list in schema:", "classification_list" in cols)

row = conn.execute("SELECT classification_list FROM items WHERE id = 1449280771").fetchone()
print("Rosewood classification_list:", row[0] if row else "NOT FOUND")

null_count = conn.execute("SELECT COUNT(*) FROM items WHERE classification_list IS NULL").fetchone()[0]
populated = conn.execute("SELECT COUNT(*) FROM items WHERE classification_list IS NOT NULL").fetchone()[0]
mat_count = conn.execute("SELECT COUNT(*) FROM items WHERE classification_list LIKE '%\"materials\"%'").fetchone()[0]
empty_arr = conn.execute("SELECT COUNT(*) FROM items WHERE classification_list = '[]'").fetchone()[0]
print(f"NULL: {null_count:,} | Populated: {populated:,} | Has 'materials': {mat_count:,} | Empty []: {empty_arr:,}")

# Quick search test
rows = conn.execute(
    "SELECT id, displayname FROM items WHERE classification_list LIKE '%\"materials\"%' LIMIT 5"
).fetchall()
print("\nSample material items:")
for r in rows:
    print(f"  {r[0]}  {r[1]}")

conn.close()
