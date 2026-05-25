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
    finally:
        conn.close()

    print(f"Upserted {n} zones.")
    print()
    counts = zones_db.expansion_counts(args.db)
    print(f"Zones per expansion ({len(counts)} expansions):")
    for short, count in counts.items():
        print(f"  {count:5d}  {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
