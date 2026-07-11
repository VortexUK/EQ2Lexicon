import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH, catalogue

conn = catalogue.init_db()
conn.execute("""
    UPDATE items
    SET tier_display = CASE
        WHEN tier IS NOT NULL AND tier != '' THEN tier
        ELSE 'COMMON'
    END
""")
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM items WHERE tier_display IS NOT NULL").fetchone()[0]
print(f"Backfilled {count:,} rows")

# Verify
print("\nSample of previously-null tiers now resolved:")
rows = conn.execute("""
    SELECT tierid, tier, tier_display, COUNT(*) as cnt
    FROM items WHERE tier IS NULL OR tier = ''
    GROUP BY tierid, tier, tier_display
    ORDER BY tierid DESC
""").fetchall()
for r in rows:
    print(f"  tierid={r[0]}  tier={str(r[1]):<8}  tier_display={r[2]}  count={r[3]:,}")
conn.close()
