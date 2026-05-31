import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.eq2db.items import DB_PATH

conn = sqlite3.connect(DB_PATH)
rows = conn.execute("""
    SELECT id, tier, raw_json FROM items
    WHERE displayname_lower = 'soulfire longsword'
    ORDER BY tierid DESC, last_update DESC
""").fetchall()

for id_, tier, raw in rows:
    d = json.loads(raw)
    print(f"=== ID={id_}  Tier={tier} ===")
    mods = d.get("modifiers") or {}
    if mods:
        print("  Modifiers:")
        for k, v in mods.items():
            print(f"    {k}: {v}")
    else:
        print("  Modifiers: (none)")
    effects = d.get("effect_list") or []
    if effects:
        print("  Effects:")
        for e in effects:
            desc = e.get("description", "")
            if desc:
                print(f"    {desc}")
    print()

conn.close()
