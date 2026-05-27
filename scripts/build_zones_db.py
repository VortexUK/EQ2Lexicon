"""
Build ``data/zones/zones.db`` from ``scripts/dev/eq2_zones.cleaned.json``
+ ``scripts/dev/eq2_raid_bosses.review.txt`` (the hand-curated raid roster).

Idempotent — re-run after editing any of the source files. Each upsert
replaces the zone row + its types + its aliases atomically; encounters
and group mobs are also fully rebuilt per zone so removed/edited entries
drop cleanly.

Stamps these ``_meta`` keys on every build so the DB is self-describing:

  * ``built_at``           — ISO-8601 UTC timestamp
  * ``built_from``         — path of the cleaned JSON consumed
  * ``bosses_built_from``  — path of the curated review.txt consumed
  * ``bosses_zones``       — number of zones populated with bosses
  * ``bosses_total``       — total encounters loaded (not individual mobs)

Usage:

    .venv/Scripts/python scripts/build_zones_db.py
    .venv/Scripts/python scripts/build_zones_db.py --source path/to/other.json
    .venv/Scripts/python scripts/build_zones_db.py --curated-bosses path/to/curated.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# Allow running as a top-level script (no `python -m`).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from census import zones_db  # noqa: E402

DEFAULT_SOURCE = ROOT / "scripts" / "dev" / "eq2_zones.cleaned.json"
DEFAULT_CURATED = ROOT / "scripts" / "dev" / "eq2_raid_bosses.review.txt"
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
                "SELECT id FROM zones WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                unmatched.append(name)
                continue
            zone_id = row[0]
            # INSERT OR IGNORE so re-running the builder is idempotent —
            # PRIMARY KEY (zone_id, type) prevents duplicates anyway, but
            # being explicit keeps the intent clear.
            conn.execute(
                "INSERT OR IGNORE INTO zone_types (zone_id, type) VALUES (?, ?)",
                (zone_id, "dungeon"),
            )
            rows_added += 1
    conn.commit()
    return rows_added, unmatched


# ---------------------------------------------------------------------------
# Curated review.txt parser
# ---------------------------------------------------------------------------
#
# Expected format (produced from clean_eq2_zones output and then
# hand-edited by the curator):
#
#     ### Zone Name  [N]  (raid_x4)
#         First Floor:
#         - Mob 1
#         - Mob 2, Mob 3, Mob 4
#         Second Floor:
#         - Mob 5
#
# Conventions:
#   * `### Zone Name [N] (type)` — zone header. `[N]` and `(type)` are
#     informational; we count actual bullets and read the type from
#     zones.db, so edits to those bracketed bits don't matter.
#   * `(!!!)` suffix marks a zone as known-empty — we skip it silently.
#   * Indented line ending in `:` (no leading `-`) is a STAGE LABEL
#     applied to every encounter that follows, until the next stage
#     label or the next zone.
#   * Indented `- ...` is an encounter. Mob names within can be
#     comma-separated OR ` and `-separated for group spawns.
#   * Blank lines are separators.
#   * Top-level banner lines (===... and the totals line) are ignored.
#

_ZONE_HEADER_RE = re.compile(r"^###\s+(?P<name>.+?)\s+\[\d+\]\s*\((?P<type>[^)]+)\)\s*(?P<empty>\(!!!\))?\s*$")
# Stage label: any indented line that doesn't start with '-'. Trailing
# colon is optional — the curator uses "First Floor:" for Emerald
# Halls but "Wing 1" (no colon) for Veeshan's Peak; both should work.
_STAGE_LINE_RE = re.compile(r"^\s+(?P<stage>[^-\s].*?):?\s*$")
_BULLET_LINE_RE = re.compile(r"^\s*-\s+(?P<content>.+?)\s*$")
# Splits a bullet's content into individual mob names. Handles:
#   "Adkar Vyx"                                  -> ["Adkar Vyx"]
#   "Uthtak the Cruel, Aktar the Dark"           -> two
#   "Zarda and Kodux"                            -> two
#   "Foo, Bar, Baz and Qux"                      -> four (mixed)
# Splits on commas first, then any remaining " and " inside a token.
_AND_SPLIT_RE = re.compile(r"\s+and\s+", flags=re.IGNORECASE)


def _split_encounter_mobs(content: str) -> list[str]:
    """Split a bullet's mob list into individual names.

    The curator uses two separators interchangeably:
      * ", "   between most names
      * " and " for the last pair (e.g. "Foo, Bar, and Baz" or
        "Foo and Bar")
    """
    parts: list[str] = []
    for chunk in content.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Split each comma-chunk on " and " too, in case the curator
        # used the Oxford pattern or skipped commas before "and".
        for piece in _AND_SPLIT_RE.split(chunk):
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return parts


def parse_curated_bosses(path: Path) -> list[dict]:
    """Parse the curated review.txt file into structured zone records.

    Returns a list of:
        {
            "zone_name": str,
            "encounters": [
                {
                    "encounter_name": str,    # the curator's text verbatim
                    "position": int,          # 1-based, in file order
                    "stage": str | None,
                    "mobs": [
                        {"mob_name": str, "position": int},
                        ...
                    ],
                },
                ...
            ],
        }

    Zones whose header carries ``(!!!)`` (curator marked empty) are
    skipped. Empty file or missing file returns ``[]``.
    """
    if not path.exists():
        return []

    out: list[dict] = []
    current_zone: dict | None = None
    current_stage: str | None = None
    encounter_position: int = 0

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        # Section/banner lines (===... or TOTALS) are ignored — they
        # carry no per-zone signal.
        if not raw_line.strip() or raw_line.startswith("="):
            continue

        # Zone header
        m = _ZONE_HEADER_RE.match(raw_line)
        if m:
            if current_zone is not None:
                out.append(current_zone)
            if m.group("empty"):
                # Curator marked it empty — skip.
                current_zone = None
            else:
                current_zone = {
                    "zone_name": m.group("name").strip(),
                    "encounters": [],
                }
                current_stage = None
                encounter_position = 0
            continue

        # Only process body lines once we're inside a zone block
        if current_zone is None:
            continue

        # Stage label
        m = _STAGE_LINE_RE.match(raw_line)
        if m:
            current_stage = m.group("stage").strip()
            continue

        # Encounter bullet
        m = _BULLET_LINE_RE.match(raw_line)
        if m:
            content = m.group("content").strip()
            if not content:
                continue
            mob_names = _split_encounter_mobs(content)
            if not mob_names:
                continue
            encounter_position += 1
            current_zone["encounters"].append(
                {
                    "encounter_name": content,
                    "position": encounter_position,
                    "stage": current_stage,
                    "mobs": [{"mob_name": name, "position": idx} for idx, name in enumerate(mob_names)],
                }
            )

    if current_zone is not None:
        out.append(current_zone)
    return out


def _load_curated_bosses_into_db(
    curated_path: Path,
    conn,
) -> tuple[int, int, list[str]]:
    """Populate zone_encounters + zone_encounter_mobs from the
    hand-curated review.txt.

    Returns (zones_with_bosses, total_encounters, unmatched_zone_names).
    Zones in the file that don't exist in zones.db are reported so the
    user knows about typos.
    """
    parsed = parse_curated_bosses(curated_path)
    if not parsed:
        return (0, 0, [])

    zones_with: int = 0
    total: int = 0
    unmatched: list[str] = []

    for z in parsed:
        zone_name = z["zone_name"]
        zone_row = conn.execute("SELECT id FROM zones WHERE name = ?", (zone_name,)).fetchone()
        if zone_row is None:
            unmatched.append(zone_name)
            continue
        zone_id = int(zone_row[0])
        encounters = z["encounters"]
        if not encounters:
            continue
        n = zones_db.replace_bosses_for_zone(conn, zone_id, encounters)
        if n:
            zones_with += 1
            total += n
    conn.commit()
    return (zones_with, total, unmatched)


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
        "--db",
        type=Path,
        default=zones_db.DB_PATH,
        help=f"SQLite output path (default: {zones_db.DB_PATH})",
    )
    parser.add_argument(
        "--curated-bosses",
        type=Path,
        default=DEFAULT_CURATED,
        help=f"Hand-curated raid-bosses review.txt to populate "
        f"zone_encounters (default: {DEFAULT_CURATED}). "
        "Missing file is OK — encounters table just stays empty.",
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
    print(f"Target DB:  {args.db}")

    conn = zones_db.init_db(args.db)
    bosses_zones = 0
    bosses_total = 0
    unmatched: list[str] = []
    try:
        n = zones_db.upsert_zones(zones, conn)
        zones_db.set_meta(conn, "built_at", dt.datetime.now(dt.timezone.utc).isoformat())
        zones_db.set_meta(conn, "built_from", str(args.source))
        zones_db.set_meta(conn, "source_count", str(len(zones)))

        # Raid-boss roster from the hand-curated review.txt. Optional —
        # the encounters tables just stay empty when the file is absent.
        if args.curated_bosses.exists():
            bosses_zones, bosses_total, unmatched = _load_curated_bosses_into_db(
                args.curated_bosses,
                conn,
            )
            zones_db.set_meta(conn, "bosses_built_from", str(args.curated_bosses))
            zones_db.set_meta(conn, "bosses_zones", str(bosses_zones))
            zones_db.set_meta(conn, "bosses_total", str(bosses_total))

        # Dungeon overlay — `('dungeon', zone_id)` rows in zone_types for
        # each curated max-level group instance. Same optional pattern as
        # the bosses file.
        dungeons_added = 0
        dungeons_unmatched: list[str] = []
        if args.dungeons.exists():
            dungeons_added, dungeons_unmatched = _load_dungeons_into_db(args.dungeons, conn)
            zones_db.set_meta(conn, "dungeons_built_from", str(args.dungeons))
            zones_db.set_meta(conn, "dungeons_added", str(dungeons_added))
    finally:
        conn.close()

    print(f"Upserted {n} zones.")
    if bosses_zones:
        print(f"Loaded {bosses_total} encounters across {bosses_zones} zones from {args.curated_bosses.name}")
        if unmatched:
            print(f"WARN: {len(unmatched)} zones in curated file not found in zones.db:")
            for name in unmatched[:5]:
                print(f"  - {name}")
            if len(unmatched) > 5:
                print(f"  ... and {len(unmatched) - 5} more")
    elif not args.curated_bosses.exists():
        print(f"No curated bosses file at {args.curated_bosses} — zone_encounters table left empty.")

    if dungeons_added:
        print(f"Tagged {dungeons_added} zones as 'dungeon' from {args.dungeons.name}")
        if dungeons_unmatched:
            print(f"WARN: {len(dungeons_unmatched)} dungeon names not found in zones.db (typos?):")
            for name in dungeons_unmatched[:5]:
                print(f"  - {name}")
            if len(dungeons_unmatched) > 5:
                print(f"  ... and {len(dungeons_unmatched) - 5} more")
    elif not args.dungeons.exists():
        print(f"No dungeons file at {args.dungeons} — no 'dungeon' tags added.")
    print()
    counts = zones_db.expansion_counts(args.db)
    print(f"Zones per expansion ({len(counts)} expansions):")
    for short, count in counts.items():
        print(f"  {count:5d}  {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
