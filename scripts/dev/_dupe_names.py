import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

conn = sqlite3.connect(DB_PATH)

total_names = conn.execute("SELECT COUNT(DISTINCT displayname_lower) FROM items").fetchone()[0]
shared_names = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT displayname_lower FROM items
        GROUP BY displayname_lower HAVING COUNT(*) > 1
    )
""").fetchone()[0]
affected_rows = conn.execute("""
    SELECT COUNT(*) FROM items
    WHERE displayname_lower IN (
        SELECT displayname_lower FROM items
        GROUP BY displayname_lower HAVING COUNT(*) > 1
    )
""").fetchone()[0]

print(f"Distinct names:        {total_names:,}")
print(f"Names shared by 2+ IDs:{shared_names:,}")
print(f"Total rows affected:   {affected_rows:,}")
print()
print("Top 20 most duplicated names:")
rows = conn.execute("""
    SELECT displayname, COUNT(*) as cnt, GROUP_CONCAT(id, ', ') as ids
    FROM items
    GROUP BY displayname_lower HAVING cnt > 1
    ORDER BY cnt DESC LIMIT 20
""").fetchall()
for name, cnt, ids in rows:
    ids_preview = ids if len(ids) < 80 else ids[:77] + "..."
    print(f"  count={cnt:>4}  name={name}")
    print(f"           ids: {ids_preview}")

conn.close()
