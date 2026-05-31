import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

conn = sqlite3.connect(DB_PATH)

print("=== WEAPONS: same name+tier+level, different damage_rating ===")
rows = conn.execute("""
    SELECT displayname, tier, tierid, level_to_use,
           COUNT(*) as variants,
           COUNT(DISTINCT damage_rating) as distinct_ratings,
           MIN(damage_rating) as min_rating,
           MAX(damage_rating) as max_rating
    FROM items
    WHERE damage_rating IS NOT NULL
    GROUP BY displayname_lower, tierid, level_to_use
    HAVING distinct_ratings > 1
    ORDER BY variants DESC, displayname
    LIMIT 30
""").fetchall()
print(f"Found {len(rows)} weapon names with stat variants at same level (showing top 30):\n")
print(f"  {'Name':<45} {'Tier':<12} {'Lvl':>5} {'Variants':>8} {'Ratings':>8}  Range")
print("  " + "-" * 100)
for name, tier, tierid, lvl, variants, distinct, minr, maxr in rows:
    print(f"  {name:<45} {str(tier):<12} {str(lvl):>5} {variants:>8} {distinct:>8}  {minr:.2f} -> {maxr:.2f}")

print("\n=== ARMOUR: same name+tier+level, different armor_class_max ===")
rows = conn.execute("""
    SELECT displayname, tier, tierid, level_to_use,
           COUNT(*) as variants,
           COUNT(DISTINCT armor_class_max) as distinct_ac,
           MIN(armor_class_max) as min_ac,
           MAX(armor_class_max) as max_ac
    FROM items
    WHERE armor_class_max IS NOT NULL
    GROUP BY displayname_lower, tierid, level_to_use
    HAVING distinct_ac > 1
    ORDER BY variants DESC, displayname
    LIMIT 30
""").fetchall()
print(f"Found {len(rows)} armour names with stat variants at same level (showing top 30):\n")
print(f"  {'Name':<45} {'Tier':<12} {'Lvl':>5} {'Variants':>8} {'AC vals':>8}  Range")
print("  " + "-" * 100)
for name, tier, tierid, lvl, variants, distinct, minac, maxac in rows:
    print(f"  {name:<45} {str(tier):<12} {str(lvl):>5} {variants:>8} {distinct:>8}  {minac} -> {maxac}")

conn.close()
