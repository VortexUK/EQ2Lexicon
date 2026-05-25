"""
Build ``data/zones/zones.db`` from ``scripts/dev/eq2_zones.cleaned.json``.

Idempotent — re-run after editing the source / overrides / aliases files
and re-running ``scripts/dev/clean_eq2_zones.py``. Each upsert replaces
the zone row + its types + its aliases atomically; removed types/aliases
disappear cleanly.

Stamps two ``_meta`` keys on every build so the DB is self-describing:

  * ``built_at``       — ISO-8601 UTC timestamp
  * ``built_from``     — path of the cleaned JSON consumed

Usage:

    .venv/Scripts/python scripts/build_zones_db.py
    .venv/Scripts/python scripts/build_zones_db.py --source path/to/other.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# Allow running as a top-level script (no `python -m`).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from census import zones_db  # noqa: E402

DEFAULT_SOURCE = ROOT / "scripts" / "dev" / "eq2_zones.cleaned.json"
DEFAULT_RAID_DATA = ROOT / "scripts" / "dev" / "eq2_raid_data.json"
DEFAULT_BOSS_OVERRIDES = ROOT / "scripts" / "dev" / "eq2_raid_bosses.overrides.json"


def _load_boss_overrides(path: Path) -> dict[str, dict]:
    """Load the per-zone boss-overrides file. Format:

        {
          "_doc": [...],
          "Zone Name": {
            "exclude": ["Mob to drop", ...],
            // Future: "include": [{"mob_name": ..., "wiki_url": ...}]
          }
        }

    Keys starting with '_' are ignored (in-file documentation).
    Missing file returns {}; that's a valid state when no manual
    overrides have been needed yet.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"WARN: {path.name} is invalid JSON ({exc}); ignoring.")
        return {}
    if not isinstance(data, dict):
        print(f"WARN: {path.name} is not a JSON object; ignoring.")
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def _load_bosses_into_db(
    raid_data_path: Path,
    overrides_path: Path,
    conn,
) -> tuple[int, int, int, list[str]]:
    """Populate zone_bosses from the scraped raid-data JSON.

    Returns (zones_with_bosses, total_bosses, bosses_excluded_by_overrides,
    unmatched_zones).
    """
    if not raid_data_path.exists():
        return (0, 0, 0, [])
    data = json.loads(raid_data_path.read_text(encoding="utf-8"))
    overrides = _load_boss_overrides(overrides_path)
    if overrides:
        print(f"Loaded boss overrides for {len(overrides)} zone(s) from {overrides_path.name}")

    zones_with: int = 0
    total: int = 0
    excluded: int = 0
    unmatched: list[str] = []

    for z in data.get("zones") or []:
        zone_name = z.get("zone_name")
        if not zone_name:
            continue
        zone_row = conn.execute("SELECT id FROM zones WHERE name = ?", (zone_name,)).fetchone()
        if zone_row is None:
            # Scraped a zone we don't know about (shouldn't happen
            # since the scrape reads from zones.db, but cover the
            # case where the scrape predates a zone-data refresh).
            unmatched.append(zone_name)
            continue
        zone_id = int(zone_row[0])

        # Apply per-zone exclude list
        exclude_lower: set[str] = set()
        ov = overrides.get(zone_name)
        if ov:
            for name in ov.get("exclude") or []:
                exclude_lower.add(name.lower().strip())

        bosses_in: list[dict] = []
        for idx, enc in enumerate(z.get("encounters") or [], start=1):
            mob_name = enc.get("mob_name")
            if not mob_name:
                continue
            if mob_name.lower() in exclude_lower:
                excluded += 1
                continue
            bosses_in.append(
                {
                    "mob_name": mob_name,
                    "position": idx,
                    "wiki_url": enc.get("wiki_url"),
                }
            )

        n = zones_db.replace_bosses_for_zone(conn, zone_id, bosses_in)
        if n:
            zones_with += 1
            total += n
    conn.commit()
    return (zones_with, total, excluded, unmatched)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Cleaned-JSON zones file to load (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=zones_db.DB_PATH,
        help=f"SQLite output path (default: {zones_db.DB_PATH})",
    )
    parser.add_argument(
        "--raid-data",
        type=Path,
        default=DEFAULT_RAID_DATA,
        help=f"Scraped raid-data JSON to populate zone_bosses (default: {DEFAULT_RAID_DATA}). "
        "Missing file is OK — bosses table just stays empty.",
    )
    parser.add_argument(
        "--boss-overrides",
        type=Path,
        default=DEFAULT_BOSS_OVERRIDES,
        help=f"Per-zone boss-exclude overrides JSON (default: {DEFAULT_BOSS_OVERRIDES}).",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"ERR: source file not found: {args.source}", file=sys.stderr)
        print(
            "Run scripts/dev/clean_eq2_zones.py first to produce it.",
            file=sys.stderr,
        )
        return 1

    payload = json.loads(args.source.read_text(encoding="utf-8"))
    zones = payload.get("zones") or []
    if not zones:
        print(f"ERR: source file has no zones: {args.source}", file=sys.stderr)
        return 1

    print(f"Loading {len(zones)} zones from {args.source.name}")
    print(f"Target DB:  {args.db}")

    conn = zones_db.init_db(args.db)
    try:
        n = zones_db.upsert_zones(zones, conn)
        zones_db.set_meta(conn, "built_at", dt.datetime.now(dt.timezone.utc).isoformat())
        zones_db.set_meta(conn, "built_from", str(args.source))
        zones_db.set_meta(conn, "source_count", str(len(zones)))

        # Boss list — optional, falls through cleanly when the
        # scrape output file isn't present yet.
        bosses_zones, bosses_total, bosses_excluded, unmatched = _load_bosses_into_db(
            args.raid_data,
            args.boss_overrides,
            conn,
        )
        if bosses_zones:
            zones_db.set_meta(conn, "bosses_built_from", str(args.raid_data))
            zones_db.set_meta(conn, "bosses_zones", str(bosses_zones))
            zones_db.set_meta(conn, "bosses_total", str(bosses_total))
    finally:
        conn.close()

    print(f"Upserted {n} zones.")
    if bosses_zones:
        print(
            f"Loaded {bosses_total} bosses across {bosses_zones} zones from "
            f"{args.raid_data.name}" + (f" ({bosses_excluded} excluded by overrides)" if bosses_excluded else "")
        )
        if unmatched:
            print(f"WARN: {len(unmatched)} zones in raid-data not found in zones.db:")
            for name in unmatched[:5]:
                print(f"  - {name}")
            if len(unmatched) > 5:
                print(f"  ... and {len(unmatched) - 5} more")
    elif not args.raid_data.exists():
        print(f"No raid-data file at {args.raid_data} — zone_bosses table left empty.")
        print("Run scripts/dev/scrape_eq2i_raids.py --all-raids to produce it.")
    print()
    counts = zones_db.expansion_counts(args.db)
    print(f"Zones per expansion ({len(counts)} expansions):")
    for short, count in counts.items():
        print(f"  {count:5d}  {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
