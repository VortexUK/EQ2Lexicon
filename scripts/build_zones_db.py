"""
Load zone metadata from ``scripts/dev/eq2_zones.cleaned.json`` into the
Postgres ``zones`` schema (zones / zone_types / zone_aliases).

Idempotent — re-run after editing the source file. Each upsert replaces the
zone row + its types + its aliases; zone ids are preserved across rebuilds
(builder-assigned: existing name → existing id, new name → max+1), which is
what keeps the curator-managed boss rosters attached. Zones that vanished
from the source JSON are pruned (and their curator data cascades) — the
prune count is printed so an unexpected removal is visible.

Stamps these ``_meta`` keys on every build so the schema is self-describing:

  * ``built_at``           — ISO-8601 UTC timestamp
  * ``built_from``         — path of the cleaned JSON consumed

Boss rosters (``zone_encounters`` / ``zone_encounter_mobs``) and the
``featured_*`` curation tables are **not** written by this script — they are
web-editable by admins and contributors via the per-zone editor in the
raids UI, and a metadata rebuild never touches them.

Usage (needs DATABASE_URL / SUPABASE_DB_URL in the environment):

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

from backend.eq2db import zones as zones_db  # noqa: E402

DEFAULT_SOURCE = ROOT / "scripts" / "dev" / "eq2_zones.cleaned.json"
DEFAULT_DUNGEONS = ROOT / "scripts" / "dev" / "eq2_zones.dungeons.json"


# ---------------------------------------------------------------------------
# Dungeons curation
# ---------------------------------------------------------------------------
#
# Per-expansion lists of "max-level group instances" — the endgame heroic
# tier. Each named zone gets a `('dungeon', zone_id)` row inserted into
# zone_types as an *overlay* on its existing group/solo_or_group typing,
# so `list_by_expansion(short, type_filter='dungeon')` returns the curated
# set for that expansion. Powers the Dungeons category on the rankings page.


def _load_dungeons_into_db(path: Path, conn) -> tuple[int, list[str]]:
    """Read the dungeons curation file, insert `dungeon` zone_types rows for
    each canonical zone listed. Returns ``(rows_added, unmatched_names)``.

    Unmatched names are reported — typos in the curation file are the most
    likely failure mode here, and silent drops would let them rot."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Skip `_doc` and any other _-prefixed keys (used to inline docs).
    by_expansion = {k: v for k, v in payload.items() if not k.startswith("_")}

    rows_added = 0
    unmatched: list[str] = []
    for _expansion, names in by_expansion.items():
        for name in names:
            row = conn.execute(
                "SELECT id FROM zones WHERE name = %s",
                (name,),
            ).fetchone()
            if row is None:
                unmatched.append(name)
                continue
            zone_id = row["id"]
            # ON CONFLICT DO NOTHING so re-running the builder is idempotent —
            # PRIMARY KEY (zone_id, type) prevents duplicates anyway, but
            # being explicit keeps the intent clear.
            conn.execute(
                "INSERT INTO zone_types (zone_id, type) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (zone_id, "dungeon"),
            )
            rows_added += 1
    conn.commit()
    return rows_added, unmatched


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Cleaned-JSON zones file to load (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--schema",
        default=zones_db.SCHEMA,
        help=f"Target Postgres schema (default: {zones_db.SCHEMA!r}; override for scratch loads).",
    )
    parser.add_argument(
        "--dungeons",
        type=Path,
        default=DEFAULT_DUNGEONS,
        help=f"Per-expansion 'max-level group instance' list, source of the "
        f"`dungeon` overlay in zone_types (default: {DEFAULT_DUNGEONS}). "
        "Missing file is OK — no dungeon rows added.",
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
    print(f"Target schema:  {args.schema}")

    cat = zones_db.ZoneCatalogue(args.schema)
    conn = cat.init_db()
    try:
        n = cat.upsert_zones(zones, conn)
        # Prune zones that no longer exist in the source. This CASCADEs the
        # pruned zones' types/aliases AND any curator boss rosters attached
        # to them — correct for a genuinely-removed zone, but always report
        # the count so an unexpected prune is caught at the console.
        removed = cat.delete_zones_not_named(conn, [z["name"] for z in zones])
        zones_db.set_meta(conn, "built_at", dt.datetime.now(dt.timezone.utc).isoformat())
        zones_db.set_meta(conn, "built_from", str(args.source))
        zones_db.set_meta(conn, "source_count", str(len(zones)))

        # Dungeon overlay — `('dungeon', zone_id)` rows in zone_types for
        # each curated max-level group instance. Optional — no rows added
        # when the file is absent.
        dungeons_added = 0
        dungeons_unmatched: list[str] = []
        if args.dungeons.exists():
            dungeons_added, dungeons_unmatched = _load_dungeons_into_db(args.dungeons, conn)
            zones_db.set_meta(conn, "dungeons_built_from", str(args.dungeons))
            zones_db.set_meta(conn, "dungeons_added", str(dungeons_added))
    finally:
        conn.close()

    print(f"Upserted {n} zones.")
    if removed:
        print(f"PRUNED {removed} zone(s) no longer present in the source JSON (curator rosters on them cascaded).")
    if dungeons_added:
        print(f"Tagged {dungeons_added} zones as 'dungeon' from {args.dungeons.name}")
        if dungeons_unmatched:
            print(f"WARN: {len(dungeons_unmatched)} dungeon names not found in the zones schema (typos?):")
            for name in dungeons_unmatched[:5]:
                print(f"  - {name}")
            if len(dungeons_unmatched) > 5:
                print(f"  ... and {len(dungeons_unmatched) - 5} more")
    elif not args.dungeons.exists():
        print(f"No dungeons file at {args.dungeons} — no 'dungeon' tags added.")
    print()
    counts = cat.expansion_counts()
    print(f"Zones per expansion ({len(counts)} expansions):")
    for short, count in counts.items():
        print(f"  {count:5d}  {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
