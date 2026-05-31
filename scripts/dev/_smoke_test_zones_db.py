"""Smoke tests for data/zones/zones.db — driven via the public
census.zones_db API + raw SQL for integrity checks."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.eq2db import zones as zones_db  # noqa: E402

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
        "SELECT COUNT(*) FROM zone_types t WHERE NOT EXISTS (SELECT 1 FROM zones z WHERE z.id = t.zone_id)"
    ).fetchone()[0]
    orphan_aliases = conn.execute(
        "SELECT COUNT(*) FROM zone_aliases a WHERE NOT EXISTS (SELECT 1 FROM zones z WHERE z.id = a.zone_id)"
    ).fetchone()[0]
    check("zone_types has no orphan zone_ids", orphan_types == 0, f"{orphan_types} orphans")
    check("zone_aliases has no orphan zone_ids", orphan_aliases == 0, f"{orphan_aliases} orphans")

    # ── Indexes exist ─────────────────────────────────────────────────
    print("\n--- INDEXES ---")
    expected_indexes = {
        "idx_zones_name_lower",
        "idx_zones_expansion",
        "idx_zones_event",
        "idx_zones_tradeskill",
        "idx_zone_types_type",
        "idx_zone_types_zone",
        "idx_zone_aliases_lower",
        "idx_zone_aliases_zone",
    }
    actual = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
    }
    missing = expected_indexes - actual
    check("All expected indexes exist", not missing, f"missing: {missing}" if missing else "")


# ── find_by_name: canonical match ─────────────────────────────────────
print("\n--- find_by_name ---")
z = zones_db.find_by_name("Sebilis")
check(
    "find_by_name('Sebilis') returns RoK openworld",
    z and z["expansion_short"] == "RoK",
    f"got {z['expansion_short'] if z else None}, types={z['types'] if z else None}",
)

z = zones_db.find_by_name("SEBILIS")
check("find_by_name case-insensitive", z is not None and z["name"] == "Sebilis", f"got {z['name'] if z else None}")

# ── find_by_name: alias resolution ────────────────────────────────────
z = zones_db.find_by_name("The Fabled Deathtoll")
check(
    "alias 'The Fabled Deathtoll' resolves to canonical 'Fabled Deathtoll'",
    z is not None and z["name"] == "Fabled Deathtoll",
    f"got {z['name'] if z else None}",
)
if z:
    check("resolved canonical has alias listed", "The Fabled Deathtoll" in z["aliases"], f"aliases={z['aliases']}")

z = zones_db.find_by_name("not a real zone")
check("find_by_name returns None for unknown", z is None, "")

# ── list_by_expansion ────────────────────────────────────────────────
print("\n--- list_by_expansion ---")
eof_raids = zones_db.list_by_expansion("EoF", type_filter="raid_x4")
check("EoF raid_x4 returns 5 zones (matches the JSON smoke test)", len(eof_raids) == 5, f"got {len(eof_raids)}")
names = sorted(z["name"] for z in eof_raids)
expected_names = sorted(
    [
        "Freethinker Hideout",
        "Mistmoore's Inner Sanctum",
        "The Clockwork Menace Factory",
        "The Emerald Halls",
        "Throne of New Tunaria",
    ]
)
check("EoF raid_x4 names match expected set", names == expected_names, f"got {names}")

rok_groups = zones_db.list_by_expansion("RoK", type_filter="group")
check("RoK group returns 11 zones (matches JSON smoke test)", len(rok_groups) == 11, f"got {len(rok_groups)}")

vanilla_all = zones_db.list_by_expansion("Vanilla")
# Count bumps when overrides reattribute a non-Vanilla zone back to Vanilla
# (e.g. Deathfist Citadel — the standalone entry the wiki marks "introduced
# in LU33" was actually a Vanilla zone with a LU33 revamp). Tweak the
# expected number as overrides land.
check("Vanilla (no filter) returns 201 zones", len(vanilla_all) == 201, f"got {len(vanilla_all)}")

# ── Dungeons overlay (max-level group instances per expansion) ────────
print("\n--- 'dungeon' overlay ---")
eof_dungeons = zones_db.list_by_expansion("EoF", type_filter="dungeon")
eof_dungeon_names = sorted(z["name"] for z in eof_dungeons)
expected_eof_dungeons = sorted(
    [
        "Crypt of Valdoon",
        "Shard of Fear",
        "The Acadechism",
        "The Estate of Unrest",
        "The Obelisk of Blight",
    ]
)
check(
    "EoF has 5 'dungeon'-tagged zones (max-level group instances)",
    len(eof_dungeons) == 5,
    f"got {len(eof_dungeons)}",
)
check(
    "EoF dungeon names match the curated set",
    eof_dungeon_names == expected_eof_dungeons,
    f"got {eof_dungeon_names}",
)

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
check(
    "contested_raid returns exactly 1 (The Temple of Scale)",
    len(contested) == 1 and contested[0]["name"] == "The Temple of Scale",
    f"got {[z['name'] for z in contested]}",
)

raid_x3 = zones_db.list_by_type("raid_x3")
check(
    "raid_x3 returns exactly 1 (A Meeting of the Minds)",
    len(raid_x3) == 1 and raid_x3[0]["name"] == "A Meeting of the Minds",
    f"got {[z['name'] for z in raid_x3]}",
)

cities = zones_db.list_by_type("city")
check("city type has 13 zones", len(cities) == 13, f"got {len(cities)}")

# ── zone_encounters / zone_encounter_mobs ─────────────────────────────
# Curated from scripts/dev/eq2_raid_bosses.review.txt — EoF + RoK only
# in current scope. 14 zones, 55 encounters as of the curator pass.
print("\n--- zone_encounters / zone_encounter_mobs ---")
with sqlite3.connect(DB) as conn:
    n_enc = conn.execute("SELECT COUNT(*) FROM zone_encounters").fetchone()[0]
    n_mobs = conn.execute("SELECT COUNT(*) FROM zone_encounter_mobs").fetchone()[0]
print(f"  zone_encounters rows: {n_enc}   zone_encounter_mobs rows: {n_mobs}")

if n_enc > 0:
    # FK integrity
    with sqlite3.connect(DB) as conn:
        orphan_enc = conn.execute(
            "SELECT COUNT(*) FROM zone_encounters e WHERE NOT EXISTS (SELECT 1 FROM zones z WHERE z.id = e.zone_id)"
        ).fetchone()[0]
        orphan_mobs = conn.execute(
            "SELECT COUNT(*) FROM zone_encounter_mobs m "
            "WHERE NOT EXISTS (SELECT 1 FROM zone_encounters e WHERE e.id = m.encounter_id)"
        ).fetchone()[0]
    check("zone_encounters has no orphan zone_ids", orphan_enc == 0, f"{orphan_enc} orphans")
    check("zone_encounter_mobs has no orphan encounter_ids", orphan_mobs == 0, f"{orphan_mobs} orphans")
    check("every encounter has at least one mob", n_mobs >= n_enc, f"{n_enc} enc, {n_mobs} mobs")

    # Spot-check Veeshan's Peak — canonical 13-boss roster
    vp = zones_db.list_bosses_for_zone("Veeshan's Peak")
    check("Veeshan's Peak has 13 encounters", len(vp) == 13, f"got {len(vp)}")
    # Stages: every encounter should be tagged with Wing 1/2/3
    stages = {b["stage"] for b in vp}
    check(
        "Veeshan's Peak encounters tagged with Wing 1/2/3",
        stages == {"Wing 1", "Wing 2", "Wing 3"},
        f"got {sorted(s or 'None' for s in stages)}",
    )

    # Multi-mob encounter shape: Temple of Kor-Sha's
    # "Uthtak the Cruel, Aktar the Dark" must come back as ONE encounter
    # with TWO mobs.
    tks = zones_db.list_bosses_for_zone("The Temple of Kor-Sha")
    check("Temple of Kor-Sha has 5 encounters", len(tks) == 5, f"got {len(tks)}")
    group_enc = next((b for b in tks if "Uthtak" in b["encounter_name"]), None)
    check(
        "Uthtak/Aktar is a 2-mob encounter (not two separate rows)",
        group_enc is not None and len(group_enc["mobs"]) == 2,
        f"mobs={[m['mob_name'] for m in group_enc['mobs']] if group_enc else None}",
    )

    # "Zarda and Kodux" — the curator used 'and' instead of comma
    zk = next((b for b in tks if "Zarda" in b["encounter_name"]), None)
    check(
        "'Zarda and Kodux' split into 2 mobs",
        zk is not None and len(zk["mobs"]) == 2,
        f"mobs={[m['mob_name'] for m in zk['mobs']] if zk else None}",
    )

    # Emerald Halls multi-stage: 13 encounters across 3 floors
    eh = zones_db.list_bosses_for_zone("The Emerald Halls")
    check("Emerald Halls has 13 encounters", len(eh) == 13, f"got {len(eh)}")
    floors = {b["stage"] for b in eh}
    check(
        "Emerald Halls has First/Second/Third Floor stages",
        floors == {"First Floor", "Second Floor", "Third Floor"},
        f"got {sorted(s or 'None' for s in floors)}",
    )

    # find_by_name hydrates the bosses array on every zone fetch
    vp_zone = zones_db.find_by_name("Veeshan's Peak")
    check(
        "find_by_name('Veeshan's Peak') hydrates 13 bosses",
        vp_zone is not None and len(vp_zone["bosses"]) == 13,
        f"bosses count: {len(vp_zone['bosses']) if vp_zone else 'n/a'}",
    )

    # Reverse lookup: a mob in a 4-mob group should resolve to its zone
    pr = zones_db.find_zones_by_boss("Adkar Vyx")
    check(
        "find_zones_by_boss resolves single-mob name to its zone",
        len(pr) == 1 and pr[0]["name"] == "The Protector's Realm",
        f"got {[z['name'] for z in pr]}",
    )
    # Reverse lookup INSIDE a group encounter (Aktar is in a 2-mob group)
    ak = zones_db.find_zones_by_boss("Aktar the Dark")
    check(
        "find_zones_by_boss works on group-encounter mob",
        len(ak) == 1 and ak[0]["name"] == "The Temple of Kor-Sha",
        f"got {[z['name'] for z in ak]}",
    )

    # Position ordering is preserved as curator wrote it
    fh = zones_db.list_bosses_for_zone("Freethinker Hideout")
    expected_order = ["Zylphax the Shredder", "Othysis Muravian", "Treyloth D'Kulvith", "Malkonis D'Morte"]
    actual_order = [b["encounter_name"] for b in fh]
    check(
        "Freethinker Hideout encounter order matches curator",
        actual_order == expected_order,
        f"got {actual_order}",
    )

    # Case-insensitive lookup
    aliased = zones_db.list_bosses_for_zone("VEESHAN'S PEAK")
    check(
        "list_bosses_for_zone is case-insensitive",
        len(aliased) == len(vp),
        f"got {len(aliased)} vs canonical {len(vp)}",
    )

    # _meta bookkeeping
    with sqlite3.connect(DB) as conn:
        bz = zones_db.get_meta(conn, "bosses_zones")
        bt = zones_db.get_meta(conn, "bosses_total")
    check("bosses_zones meta populated", bz is not None and int(bz) > 0, f"got {bz}")
    check(
        "bosses_total meta matches actual encounter count",
        bt is not None and int(bt) == n_enc,
        f"meta={bt}, table count={n_enc}",
    )
else:
    print("  (skipping boss-specific checks — curated bosses not loaded yet)")

# ── expansion_counts ──────────────────────────────────────────────────
print("\n--- expansion_counts ---")
counts = zones_db.expansion_counts()
check("expansion_counts returns 26 expansions", len(counts) == 26, f"got {len(counts)}")
check(
    "Vanilla is the largest bucket",
    max(counts, key=counts.get) == "Vanilla",
    f"largest is {max(counts, key=counts.get)}",
)
check("Sum of expansion_counts equals total zones", sum(counts.values()) == 1124, f"sum={sum(counts.values())}")

print()
print("=" * 70)
if failed:
    print(f"SUMMARY: {len(failed)} failures")
    for f in failed:
        print(f"  FAIL: {f}")
else:
    print("SUMMARY: ALL CHECKS PASSED")
print("=" * 70)
