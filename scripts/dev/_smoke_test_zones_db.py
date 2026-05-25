"""Smoke tests for data/zones/zones.db — driven via the public
census.zones_db API + raw SQL for integrity checks."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from census import zones_db  # noqa: E402

DB = zones_db.DB_PATH
failed: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failed.append(label)


print("=" * 70)
print(f"DB SMOKE: {DB}")
print("=" * 70)
check("DB file exists", DB.exists(), str(DB))

with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row

    # ── _meta provenance keys ─────────────────────────────────────────
    print("\n--- META ---")
    for key in ("built_at", "built_from", "source_count"):
        val = zones_db.get_meta(conn, key)
        check(f"meta key {key!r} populated", val is not None, val or "(none)")

    # ── Counts ────────────────────────────────────────────────────────
    print("\n--- COUNTS ---")
    n_zones = conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0]
    n_types = conn.execute("SELECT COUNT(*) FROM zone_types").fetchone()[0]
    n_aliases = conn.execute("SELECT COUNT(*) FROM zone_aliases").fetchone()[0]
    check(f"zones table has 1124 rows (matches cleaned JSON)", n_zones == 1124, f"got {n_zones}")
    check(f"zone_types non-empty", n_types > 1000, f"got {n_types}")
    check(f"zone_aliases populated from merge file", n_aliases >= 2, f"got {n_aliases}")

    # ── FK integrity ──────────────────────────────────────────────────
    print("\n--- FK INTEGRITY ---")
    orphan_types = conn.execute(
        "SELECT COUNT(*) FROM zone_types t "
        "WHERE NOT EXISTS (SELECT 1 FROM zones z WHERE z.id = t.zone_id)"
    ).fetchone()[0]
    orphan_aliases = conn.execute(
        "SELECT COUNT(*) FROM zone_aliases a "
        "WHERE NOT EXISTS (SELECT 1 FROM zones z WHERE z.id = a.zone_id)"
    ).fetchone()[0]
    check("zone_types has no orphan zone_ids", orphan_types == 0, f"{orphan_types} orphans")
    check("zone_aliases has no orphan zone_ids", orphan_aliases == 0, f"{orphan_aliases} orphans")

    # ── Indexes exist ─────────────────────────────────────────────────
    print("\n--- INDEXES ---")
    expected_indexes = {
        "idx_zones_name_lower", "idx_zones_expansion", "idx_zones_event",
        "idx_zones_tradeskill", "idx_zone_types_type", "idx_zone_types_zone",
        "idx_zone_aliases_lower", "idx_zone_aliases_zone",
    }
    actual = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )}
    missing = expected_indexes - actual
    check("All expected indexes exist", not missing, f"missing: {missing}" if missing else "")


# ── find_by_name: canonical match ─────────────────────────────────────
print("\n--- find_by_name ---")
z = zones_db.find_by_name("Sebilis")
check("find_by_name('Sebilis') returns RoK openworld", z and z["expansion_short"] == "RoK",
      f"got {z['expansion_short'] if z else None}, types={z['types'] if z else None}")

z = zones_db.find_by_name("SEBILIS")
check("find_by_name case-insensitive", z is not None and z["name"] == "Sebilis",
      f"got {z['name'] if z else None}")

# ── find_by_name: alias resolution ────────────────────────────────────
z = zones_db.find_by_name("The Fabled Deathtoll")
check("alias 'The Fabled Deathtoll' resolves to canonical 'Fabled Deathtoll'",
      z is not None and z["name"] == "Fabled Deathtoll",
      f"got {z['name'] if z else None}")
if z:
    check("resolved canonical has alias listed",
          "The Fabled Deathtoll" in z["aliases"],
          f"aliases={z['aliases']}")

z = zones_db.find_by_name("not a real zone")
check("find_by_name returns None for unknown", z is None, "")

# ── list_by_expansion ────────────────────────────────────────────────
print("\n--- list_by_expansion ---")
eof_raids = zones_db.list_by_expansion("EoF", type_filter="raid_x4")
check("EoF raid_x4 returns 5 zones (matches the JSON smoke test)",
      len(eof_raids) == 5, f"got {len(eof_raids)}")
names = sorted(z["name"] for z in eof_raids)
expected_names = sorted([
    "Freethinker Hideout",
    "Mistmoore's Inner Sanctum",
    "The Clockwork Menace Factory",
    "The Emerald Halls",
    "Throne of New Tunaria",
])
check("EoF raid_x4 names match expected set", names == expected_names,
      f"got {names}")

rok_groups = zones_db.list_by_expansion("RoK", type_filter="group")
check("RoK group returns 11 zones (matches JSON smoke test)",
      len(rok_groups) == 11, f"got {len(rok_groups)}")

vanilla_all = zones_db.list_by_expansion("Vanilla")
check("Vanilla (no filter) returns 200 zones",
      len(vanilla_all) == 200, f"got {len(vanilla_all)}")

# ── list_by_event ─────────────────────────────────────────────────────
print("\n--- list_by_event ---")
tinkerfest = zones_db.list_by_event("Tinkerfest")
check("Tinkerfest returns >=5 zones", len(tinkerfest) >= 5, f"got {len(tinkerfest)}")
for z in tinkerfest:
    if not z["is_live_event"]:
        check(f"all Tinkerfest results are flagged is_live_event ({z['name']})", False)
        break
else:
    check("all Tinkerfest results are flagged is_live_event", True)

# ── list_by_type ──────────────────────────────────────────────────────
print("\n--- list_by_type ---")
contested = zones_db.list_by_type("contested_raid")
check("contested_raid returns exactly 1 (The Temple of Scale)",
      len(contested) == 1 and contested[0]["name"] == "The Temple of Scale",
      f"got {[z['name'] for z in contested]}")

raid_x3 = zones_db.list_by_type("raid_x3")
check("raid_x3 returns exactly 1 (A Meeting of the Minds)",
      len(raid_x3) == 1 and raid_x3[0]["name"] == "A Meeting of the Minds",
      f"got {[z['name'] for z in raid_x3]}")

cities = zones_db.list_by_type("city")
check("city type has 13 zones", len(cities) == 13, f"got {len(cities)}")

# ── expansion_counts ──────────────────────────────────────────────────
print("\n--- expansion_counts ---")
counts = zones_db.expansion_counts()
check("expansion_counts returns 26 expansions", len(counts) == 26, f"got {len(counts)}")
check("Vanilla is the largest bucket",
      max(counts, key=counts.get) == "Vanilla",
      f"largest is {max(counts, key=counts.get)}")
check("Sum of expansion_counts equals total zones",
      sum(counts.values()) == 1124,
      f"sum={sum(counts.values())}")

print()
print("=" * 70)
if failed:
    print(f"SUMMARY: {len(failed)} failures")
    for f in failed:
        print(f"  FAIL: {f}")
else:
    print("SUMMARY: ALL CHECKS PASSED")
print("=" * 70)
