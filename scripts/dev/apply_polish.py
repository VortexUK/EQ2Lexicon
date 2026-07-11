"""Apply polished strategy markdown back into ``raids.db``.

Reads every JSON file in ``data/raids/polish_outbox/`` (or a single file via
``--in <path>``) and writes each entry's ``polished_md`` into the matching
``raid_encounters`` row, stamped as ``source=SOURCE_MANUAL`` so a future
re-scrape won't clobber the polished version.

Per-entry expected shape::

    {
      "zone_name": "Veeshan's Peak",
      "mob_name":  "Druushk",
      "polished_md": "...the polished markdown..."
    }

Wrapped in either ``{"entries": [...]}`` (matches the inbox shape) OR a
bare list. Both are accepted so agents can use whichever feels natural.

The encounter helper already records a revision row for every actual change
to ``strategy_md`` — no extra audit work needed here. ``edited_by`` is
stamped as ``ai-polish`` so the revision history can distinguish AI passes
from human edits later.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from backend.eq2db.raids import catalogue as raids_db  # noqa: E402

_OUTBOX = _REPO / "data" / "raids" / "polish_outbox"


def _load_entries(path: Path) -> list[dict]:
    """Accept either a wrapped dict or a bare list at the top level."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entries" in data:
        return data["entries"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: top-level must be either {{...entries: [...]}} or a list, got {type(data).__name__}")


def apply_entries(entries: list[dict], *, dry_run: bool = False) -> dict:
    """Write each ``polished_md`` into raids.db keyed by ``(zone_name, mob_name)``.

    Returns counts for the CLI summary. ``zones_unknown`` / ``encs_unknown``
    catch mismatches where an agent kept a name that doesn't exist in raids.db
    (rename typo, dropped row, etc) so they're visible in the report rather
    than silently lost."""
    n_applied = 0
    n_nulled = 0
    n_zones_unknown = 0
    n_encs_unknown = 0

    conn = raids_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        for entry in entries:
            polished = (entry.get("polished_md") or "").strip()
            zone_name = entry["zone_name"]
            mob_name = entry["mob_name"]

            zrow = conn.execute(
                "SELECT id FROM raid_zones WHERE zone_name_lower = ?",
                (zone_name.lower(),),
            ).fetchone()
            if zrow is None:
                print(f"  [warn] zone not in raids.db: {zone_name!r}")
                n_zones_unknown += 1
                continue

            erow = conn.execute(
                "SELECT id, mob_name FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
                (zrow["id"], mob_name.lower()),
            ).fetchone()
            if erow is None:
                print(f"  [warn] encounter not found: {zone_name} / {mob_name!r}")
                n_encs_unknown += 1
                continue

            if not polished:
                # Empty polished_md means "the original was entirely anecdote /
                # easter-egg / out-of-era — null the field". Targeted UPDATE
                # rather than going through upsert_raid_encounter so the row
                # ends up with strategy_md = NULL (the helper's revision logic
                # treats a None strategy_md as "no change" and skips it).
                if dry_run:
                    print(f"  [dry-run] would null {zone_name} / {mob_name!r}")
                    continue
                now = int(time.time())
                conn.execute(
                    """
                    UPDATE raid_encounters SET
                        strategy_md    = NULL,
                        source         = ?,
                        last_edited_at = ?,
                        last_edited_by = ?
                    WHERE id = ?
                    """,
                    (raids_db.SOURCE_MANUAL, now, "ai-polish", erow["id"]),
                )
                conn.commit()
                n_nulled += 1
                continue

            if dry_run:
                print(f"  [dry-run] would polish {zone_name} / {mob_name!r} ({len(polished):,} chars)")
                continue

            raids_db.upsert_raid_encounter(
                conn,
                raid_zone_id=zrow["id"],
                mob_name=erow["mob_name"],
                strategy_md=polished,
                source=raids_db.SOURCE_MANUAL,
                edited_by="ai-polish",
                edit_note="AI polish: structure + emphasis pass",
            )
            n_applied += 1
    finally:
        conn.close()

    return {
        "applied": n_applied,
        "nulled": n_nulled,
        "zones_unknown": n_zones_unknown,
        "encs_unknown": n_encs_unknown,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--in",
        dest="path",
        type=Path,
        help="Single JSON file to apply. If omitted, applies every chunk_*.json in data/raids/polish_outbox/.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write to raids.db.")
    args = parser.parse_args(argv)

    if args.path:
        files = [args.path]
    else:
        files = sorted(_OUTBOX.glob("chunk_*.json"))

    if not files:
        print("No polished JSON files found.", file=sys.stderr)
        print(f"Looked in: {_OUTBOX}")
        return 1

    totals = {"applied": 0, "nulled": 0, "zones_unknown": 0, "encs_unknown": 0}
    for path in files:
        print(f"\nReading {path.name}")
        entries = _load_entries(path)
        stats = apply_entries(entries, dry_run=args.dry_run)
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v
        print(f"  {stats}")

    label = "(dry-run) " if args.dry_run else ""
    print(f"\n{label}Apply totals:")
    for k, v in totals.items():
        print(f"  {k:20s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
