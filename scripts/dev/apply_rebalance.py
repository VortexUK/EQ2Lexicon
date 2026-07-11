"""Apply the zone↔encounter rebalance back into raids.db.

Reads JSON files from ``data/raids/rebalance_outbox/`` (or one via
``--in``). Each file's ``zones[*]`` entries get:

  * ``raid_zones.overview_md`` updated to ``updated_overview_md`` (or
    NULL when empty), stamped ``source=manual``, ``last_edited_by='ai-rebalance'``.
  * Each encounter's ``raid_encounters.strategy_md`` updated likewise
    (or NULL when empty), via the same ``ai-rebalance`` identity so a
    future audit can find this pass in the revision history.

Per-entry shape::

    {
      "zone_name": "...",
      "updated_overview_md": "...",
      "encounters": [
        { "mob_name": "...", "updated_strategy_md": "..." },
        ...
      ]
    }

Wrapped in ``{"zones": [...]}`` or a bare list — both accepted.
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

_OUTBOX = _REPO / "data" / "raids" / "rebalance_outbox"


def _load_zones(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "zones" in data:
        return data["zones"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: top-level must be {{...zones: [...]}} or a list")


def apply_zones(zones: list[dict], *, dry_run: bool = False) -> dict:
    """Update overview_md AND each encounter's strategy_md atomically per
    zone (single transaction so a partial apply can't leave the zone
    half-rebalanced)."""
    n_overviews_updated = 0
    n_overviews_nulled = 0
    n_overviews_unchanged = 0
    n_encs_updated = 0
    n_encs_nulled = 0
    n_encs_unchanged = 0
    n_zones_unknown = 0
    n_encs_unknown = 0
    now = int(time.time())

    conn = raids_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        for zone in zones:
            zone_name = zone["zone_name"]
            zrow = conn.execute(
                "SELECT id, overview_md FROM raid_zones WHERE zone_name_lower = ?",
                (zone_name.lower(),),
            ).fetchone()
            if zrow is None:
                print(f"  [warn] zone not in raids.db: {zone_name!r}")
                n_zones_unknown += 1
                continue

            # ── Overview ──────────────────────────────────────────────────
            new_overview = (zone.get("updated_overview_md") or "").strip() or None
            current_overview = zrow["overview_md"] or None

            if new_overview != current_overview:
                if dry_run:
                    label = "null" if new_overview is None else f"{len(new_overview):,} chars"
                    print(f"  [dry-run] {zone_name} overview -> {label}")
                else:
                    conn.execute(
                        """
                        UPDATE raid_zones SET
                            overview_md     = ?,
                            source          = ?,
                            last_edited_at  = ?,
                            last_edited_by  = ?
                        WHERE id = ?
                        """,
                        (new_overview, raids_db.SOURCE_MANUAL, now, "ai-rebalance", zrow["id"]),
                    )
                if new_overview is None:
                    n_overviews_nulled += 1
                else:
                    n_overviews_updated += 1
            else:
                n_overviews_unchanged += 1

            # ── Encounters ────────────────────────────────────────────────
            for enc in zone.get("encounters", []):
                mob_name = enc["mob_name"]
                erow = conn.execute(
                    "SELECT id, mob_name, strategy_md FROM raid_encounters "
                    "WHERE raid_zone_id = ? AND mob_name_lower = ?",
                    (zrow["id"], mob_name.lower()),
                ).fetchone()
                if erow is None:
                    print(f"  [warn] encounter not found: {zone_name} / {mob_name!r}")
                    n_encs_unknown += 1
                    continue

                new_strategy = (enc.get("updated_strategy_md") or "").strip() or None
                current_strategy = erow["strategy_md"] or None

                if new_strategy == current_strategy:
                    n_encs_unchanged += 1
                    continue

                if dry_run:
                    label = "null" if new_strategy is None else f"{len(new_strategy):,} chars"
                    print(f"  [dry-run] {zone_name} / {mob_name!r} strategy -> {label}")
                else:
                    conn.execute(
                        """
                        UPDATE raid_encounters SET
                            strategy_md     = ?,
                            source          = ?,
                            last_edited_at  = ?,
                            last_edited_by  = ?
                        WHERE id = ?
                        """,
                        (new_strategy, raids_db.SOURCE_MANUAL, now, "ai-rebalance", erow["id"]),
                    )
                if new_strategy is None:
                    n_encs_nulled += 1
                else:
                    n_encs_updated += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "overviews_updated": n_overviews_updated,
        "overviews_nulled": n_overviews_nulled,
        "overviews_unchanged": n_overviews_unchanged,
        "encs_updated": n_encs_updated,
        "encs_nulled": n_encs_nulled,
        "encs_unchanged": n_encs_unchanged,
        "zones_unknown": n_zones_unknown,
        "encs_unknown": n_encs_unknown,
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

    totals: dict[str, int] = {}
    for path in files:
        print(f"\nReading {path.name}")
        stats = apply_zones(_load_zones(path), dry_run=args.dry_run)
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v
        print(f"  {stats}")

    label = "(dry-run) " if args.dry_run else ""
    print(f"\n{label}Apply totals:")
    for k, v in totals.items():
        print(f"  {k:25s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
