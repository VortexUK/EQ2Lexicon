"""Smoke tests for eq2_zones.cleaned.json. Five sections of independent
checks; failures bubble up to the bottom summary."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

CLEANED = Path(__file__).resolve().parent / "eq2_zones.cleaned.json"
data = json.loads(CLEANED.read_text(encoding="utf-8"))
zones = data["zones"]

VALID_SHORTS = {
    "Vanilla", "BC", "SS", "DoF", "FD", "KoS", "EoF", "RoK", "TSO", "SF",
    "DoV", "AoD", "CoE", "ToV", "AoM", "ToT", "KA", "PoP", "CD", "BoL",
    "RoS", "VoV", "RoR", "BoZ", "SoD", "RoC", "UNK",
}
VALID_TYPES = {
    "solo", "group", "heroic", "raid", "raid_x2", "raid_x3", "raid_x4",
    "solo_or_group", "openworld_public", "contested_raid", "tradeskill",
    "pvp", "city",
}
VALID_CONFIDENCE = {
    "category", "live_update", "live_event", "update_date", "location_prefix",
    "manual_override", "name_keyword", "unknown",
}

failed: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failed.append(label)


print("=" * 70)
print("1. INTERNAL CONSISTENCY")
print("=" * 70)

names = [z.get("name") for z in zones]
check(
    "Every zone has a non-empty name",
    all(n and isinstance(n, str) for n in names),
    f"{sum(1 for n in names if not n)} missing",
)

dupes = [n for n, c in Counter(names).items() if c > 1]
check(
    "Canonical names are unique",
    len(dupes) == 0,
    f"duplicates: {dupes}" if dupes else f"{len(names)} unique",
)

bad_short = [
    z["name"]
    for z in zones
    if z["classification"]["expansion"]["short"] not in VALID_SHORTS
]
check(
    "All expansion shorts are in the catalogue",
    len(bad_short) == 0,
    f"{len(bad_short)} offenders" if bad_short else f"{len(VALID_SHORTS) - 1} valid shorts",
)

bad_conf = [
    z["name"]
    for z in zones
    if z["classification"]["expansion"]["confidence"] not in VALID_CONFIDENCE
]
check(
    "All confidence values are recognised",
    len(bad_conf) == 0,
    f"{len(bad_conf)} offenders",
)

bad_types: list[tuple[str, list[str]]] = []
for z in zones:
    bad = [t for t in z["classification"]["types"] if t not in VALID_TYPES]
    if bad:
        bad_types.append((z["name"], bad))
check(
    "All type tokens are recognised",
    len(bad_types) == 0,
    f"{len(bad_types)} offenders",
)

no_event_name = [
    z["name"]
    for z in zones
    if z["classification"]["is_live_event"] and not z["classification"].get("event_name")
]
check(
    "is_live_event=true implies event_name populated",
    len(no_event_name) == 0,
    f"{len(no_event_name)} offenders",
)

event_name_set: list[str] = [
    z["name"]
    for z in zones
    if not z["classification"]["is_live_event"] and z["classification"].get("event_name")
]
check(
    "is_live_event=false implies event_name empty",
    len(event_name_set) == 0,
    f"{len(event_name_set)} offenders",
)

canonical_set = set(names)
alias_collisions: list[str] = []
for z in zones:
    for a in z.get("aliases") or []:
        if a in canonical_set:
            alias_collisions.append(f"{z['name']} aliases {a}")
check(
    "Alias lists don't reference other canonical zones",
    len(alias_collisions) == 0,
    "; ".join(alias_collisions[:3]),
)

unk = [
    z["name"]
    for z in zones
    if z["classification"]["expansion"]["confidence"] == "unknown"
]
check(
    "No zones have unknown expansion confidence",
    len(unk) == 0,
    ", ".join(unk),
)

# Zones with no types should ALL be cities (hub zones have no combat type)
no_types = [z for z in zones if not z["classification"]["types"]]
non_city_no_types = [
    z["name"] for z in no_types if not z["classification"].get("is_city")
]
check(
    "Every zone with no types is flagged is_city",
    len(non_city_no_types) == 0,
    f"{len(non_city_no_types)} non-city zones with no types: {non_city_no_types[:5]}",
)

print()
print("=" * 70)
print("2. EQ2-KNOWLEDGE SPOT CHECKS")
print("=" * 70)


def find_zone(name: str):
    return next((z for z in zones if z["name"] == name), None)


def assert_zone(name, exp_short, must_have_types=None, also=None):
    z = find_zone(name)
    if not z:
        check(f"Zone '{name}' exists in data", False, "NOT FOUND")
        return
    actual_short = z["classification"]["expansion"]["short"]
    actual_types = set(z["classification"]["types"])
    ok = actual_short == exp_short
    if must_have_types:
        ok = ok and all(t in actual_types for t in must_have_types)
    desc_bits = [f"got {actual_short}", f"types={sorted(actual_types) or 'none'}"]
    if also:
        for k, v in also.items():
            actual_v = z["classification"].get(k)
            if actual_v != v:
                ok = False
                desc_bits.append(f"{k}={actual_v} (expected {v})")
    label = f"'{name}' = {exp_short}"
    if must_have_types:
        label += f", types subset of {must_have_types}"
    check(label, ok, ", ".join(desc_bits))


# RoK icons
assert_zone("Veeshan's Peak", "RoK", ["raid_x4"])
assert_zone("Sebilis", "RoK", ["openworld_public"])
assert_zone("Karnor's Castle", "RoK", ["openworld_public"])
# EoF raids
assert_zone("Mistmoore's Inner Sanctum", "EoF", ["raid_x4"])
assert_zone("Throne of New Tunaria", "EoF", ["raid_x4"])
assert_zone("The Emerald Halls", "EoF", ["raid_x4"])
# KoS signature
assert_zone("Plane of Sky", "KoS")
# SF city
assert_zone("New Halas", "SF")
# CoE LU-attributed
assert_zone("Antechamber of the Automaton", "CoE")
# Manual override
assert_zone("The Fabled Temple of Cazic-Thule", "AoM")
# Dedup canary — the Fabled Deathtoll merge
ft = find_zone("Fabled Deathtoll")
if ft:
    check(
        "'Fabled Deathtoll' absorbed 'The Fabled Deathtoll' as alias",
        "The Fabled Deathtoll" in (ft.get("aliases") or []),
        f"aliases={ft.get('aliases')}",
    )
# Should NOT exist as a separate record any more
check(
    "'The Fabled Deathtoll' no longer exists as a canonical record",
    find_zone("The Fabled Deathtoll") is None,
)

print()
print("=" * 70)
print("3. DISTRIBUTION SANITY")
print("=" * 70)

exp_counts = Counter(z["classification"]["expansion"]["short"] for z in zones)
print(f"  Total zones: {len(zones)}")
print(f"  Distinct expansions present: {len(exp_counts)} (catalogue has {len(VALID_SHORTS) - 1})")

biggest_exp, biggest_n = exp_counts.most_common(1)[0]
check(
    "Vanilla is the largest single-expansion bucket",
    biggest_exp == "Vanilla",
    f"largest is {biggest_exp}={biggest_n}",
)

for x in ["RoC", "SoD", "BoZ"]:
    check(f"Modern expansion '{x}' has at least one zone", exp_counts.get(x, 0) > 0,
          f"count={exp_counts.get(x, 0)}")

# Live events
events = [z for z in zones if z["classification"]["is_live_event"]]
event_breakdown = Counter(z["classification"]["event_name"] for z in events)
print(f"\n  Live-event zones: {len(events)}")
for ev, n in event_breakdown.most_common():
    print(f"    {n:3d}  {ev}")
check(
    "Live-event zone count is plausible (5-100)",
    5 <= len(events) <= 100,
    f"got {len(events)}",
)

ts_zones = [z for z in zones if z["classification"]["is_tradeskill"]]
pvp_zones = [z for z in zones if z["classification"]["is_pvp"]]
print(f"\n  Tradeskill zones: {len(ts_zones)}")
print(f"  PvP zones: {len(pvp_zones)}")
check(
    "Tradeskill zone count is plausible (40-150)",
    40 <= len(ts_zones) <= 150,
    f"got {len(ts_zones)}",
)
check(
    "PvP zone count is very small (0-10)",
    0 <= len(pvp_zones) <= 10,
    f"got {len(pvp_zones)}",
)

type_counts: Counter[str] = Counter()
for z in zones:
    for t in z["classification"]["types"]:
        type_counts[t] += 1
print("\n  Type distribution (multi-type zones count for each):")
for t, n in type_counts.most_common():
    print(f"    {n:5d}  {t}")

r4 = type_counts.get("raid_x4", 0)
r2 = type_counts.get("raid_x2", 0)
if r2 > 0:
    ratio = r4 / r2
    check(
        "x4:x2 raid ratio is plausible (3:1 to 10:1)",
        3 <= ratio <= 10,
        f"got {ratio:.1f} (x4={r4}, x2={r2})",
    )

print()
print("=" * 70)
print("4. CROSS-FIELD COHERENCE")
print("=" * 70)

oo_mismatch = [
    z["name"]
    for z in zones
    if ("openworld_public" in z["classification"]["types"])
    != z["classification"]["is_openworld"]
]
check(
    "openworld_public type ↔ is_openworld flag agree",
    len(oo_mismatch) == 0,
    f"{len(oo_mismatch)} mismatches",
)

ts_mismatch = [
    z["name"]
    for z in zones
    if ("tradeskill" in z["classification"]["types"])
    and not z["classification"]["is_tradeskill"]
]
check(
    "tradeskill type implies is_tradeskill=true",
    len(ts_mismatch) == 0,
    f"{len(ts_mismatch)} mismatches",
)

# is_instance + openworld_public is theoretically weird (instances are
# usually private). Surface count but don't fail.
inst_and_public = [
    z["name"]
    for z in zones
    if z["classification"]["is_instance"]
    and "openworld_public" in z["classification"]["types"]
]
print(f"  is_instance AND openworld_public (theoretically weird): {len(inst_and_public)}")
if inst_and_public:
    for n in inst_and_public[:5]:
        print(f"    {n}")
    if len(inst_and_public) > 5:
        print(f"    ... and {len(inst_and_public) - 5} more")

print()
print("=" * 70)
print("5. EVENT-NAME vs EXPANSION COHERENCE")
print("=" * 70)
# Events recur annually so individual zones can be added in any
# expansion >= the event's introduction year. Assert the zone's
# expansion year is no earlier than the event's introduction year —
# anything earlier would be impossible.
EVENT_INTRO_YEAR = {
    "Tinkerfest": 2010,
    "Bristlebane Day": 2011,
    "Brew Day": 2013,
    "Chronoportal Phenomenon": 2014,
    "Darkpaw Rising": 2023,
    "Frostfell": 2004,
    "Erollisi Day": 2005,
    "Heroes' Festival": 2005,
    "Nights of the Dead": 2005,
}
bad_events = []
for z in zones:
    if not z["classification"]["is_live_event"]:
        continue
    ev = z["classification"]["event_name"]
    intro_year = EVENT_INTRO_YEAR.get(ev)
    actual_year = z["classification"]["expansion"]["year"]
    # Allow 1-year slack: expansions span ~12 months from their
    # release year so an expansion launched in year N legitimately
    # covers content launched in year N+1 (e.g. ToV launched Nov 2013
    # and runs through Nov 2014; a Chronoportal zone launched Apr 2014
    # is correctly attributed to ToV even though intro year is 2014).
    if intro_year and actual_year and actual_year + 1 < intro_year:
        bad_events.append(
            f"{z['name']} ({ev}: zone year {actual_year} too early for event intro {intro_year})"
        )
check(
    "Every live-event zone's expansion year >= event's intro year - 1",
    len(bad_events) == 0,
    "; ".join(bad_events[:3]),
)

print()
print("=" * 70)
if failed:
    print(f"SUMMARY: {len(failed)} failures")
    for f in failed:
        print(f"  FAIL: {f}")
else:
    print("SUMMARY: ALL CHECKS PASSED")
print("=" * 70)
