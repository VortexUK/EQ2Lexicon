"""Apply the audited zone overviews back into raids.db.

Reads every JSON file in ``data/raids/zone_overview_outbox/`` (or one via
``--in``). For each entry: updates ``raid_zones.overview_md`` and stamps
``source=SOURCE_MANUAL`` + ``last_edited_by='ai-audit'`` so a future
re-scrape can't bring the per-boss leakage back.

Per-entry shape::

    {"zone_name": "...", "cleaned_md": "..."}

``cleaned_md`` may be an empty string — interpret that as "the overview
was entirely per-boss content; null the field so the UI falls back to no
overview". Whitespace-only is treated the same.
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

from backend.eq2db import raids as raids_db  # noqa: E402

_OUTBOX = _REPO / "data" / "raids" / "zone_overview_outbox"


def _load_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entries" in data:
        return data["entries"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: top-level must be {{...entries: [...]}} or a list")


def apply_entries(entries: list[dict], *, dry_run: bool = False) -> dict:
    n_updated = 0
    n_nulled = 0
    n_zones_unknown = 0
    n_unchanged = 0

    conn = raids_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        for entry in entries:
            zone_name = entry["zone_name"]
            cleaned = (entry.get("cleaned_md") or "").strip()

            zrow = conn.execute(
                "SELECT id, overview_md FROM raid_zones WHERE zone_name_lower = ?",
                (zone_name.lower(),),
            ).fetchone()
            if zrow is None:
                print(f"  [warn] zone not in raids.db: {zone_name!r}")
                n_zones_unknown += 1
                continue

            new_value: str | None = cleaned if cleaned else None
            if new_value == (zrow["overview_md"] or None):
                n_unchanged += 1
                continue

            if dry_run:
                label = "null" if new_value is None else f"{len(new_value):,} chars"
                print(f"  [dry-run] would set {zone_name!r} overview_md = {label}")
                continue

            now = int(time.time())
            conn.execute(
                """
                UPDATE raid_zones SET
                    overview_md     = ?,
                    source          = ?,
                    last_edited_at  = ?,
                    last_edited_by  = ?
                WHERE id = ?
                """,
                (new_value, raids_db.SOURCE_MANUAL, now, "ai-audit", zrow["id"]),
            )
            if new_value is None:
                n_nulled += 1
            else:
                n_updated += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "updated": n_updated,
        "nulled": n_nulled,
        "unchanged": n_unchanged,
        "zones_unknown": n_zones_unknown,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="path", type=Path, help="Single JSON file to apply.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    files = [args.path] if args.path else sorted(_OUTBOX.glob("chunk_*.json"))
    if not files:
        print("No outbox JSON found.", file=sys.stderr)
        print(f"Looked in: {_OUTBOX}")
        return 1

    totals = {"updated": 0, "nulled": 0, "unchanged": 0, "zones_unknown": 0}
    for path in files:
        print(f"\nReading {path.name}")
        stats = apply_entries(_load_entries(path), dry_run=args.dry_run)
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
