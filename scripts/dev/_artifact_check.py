import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

conn = sqlite3.connect(DB_PATH)

# Tier column
rows = conn.execute(
    "SELECT tier, tierid, COUNT(*) FROM items WHERE tier LIKE '%rtifact%' GROUP BY tier, tierid"
).fetchall()
print("Tier column matches:", rows or "none")

# Items with artifact in the name
name_hits = conn.execute("SELECT COUNT(*) FROM items WHERE displayname_lower LIKE '%artifact%'").fetchone()[0]
print(f"Items with 'artifact' in name: {name_hits:,}")

# Any raw_json mentioning artifact as a tier value
sample = conn.execute("SELECT raw_json FROM items WHERE raw_json LIKE '%rtifact%' LIMIT 10").fetchall()
print(f"raw_json hits: {len(sample)}")
for (raw,) in sample:
    d = json.loads(raw)
    print(f"  tier={d.get('tier')!r}  tierid={d.get('tierid')}  name={d.get('displayname')}")

conn.close()
