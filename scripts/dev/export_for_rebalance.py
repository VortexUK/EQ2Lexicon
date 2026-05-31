"""Export zones (with their encounters + current overview) into N balanced
chunks for the rebalance audit.

The previous audit pulled per-boss content OUT of zone overviews. This one
goes the other way: it pulls zone-level content OUT of encounter
strategies (a "Zone Layout" section that crept into a single-encounter
zone's boss strategy is the classic offender) and asks the agent to merge
it into the zone overview.

Chunking is by **zone**, not by encounter — each agent must see all
encounters in a zone at once to intelligently merge zone-level content
without duplicating across overview + strategies.

Input chunk shape::

    {
      "chunk_id": 1,
      "total_chars": 18234,
      "zones": [
        {
          "zone_name": "Trakanon's Lair",
          "expansion_short": "RoK",
          "current_overview_md": "...",      # may be empty / null
          "encounters": [
            {
              "mob_name": "Trakanon",
              "position": 1,
              "wiki_url": "...",
              "current_strategy_md": "..."
            },
            ...
          ]
        },
        ...
      ]
    }

Output chunk shape (the apply script consumes this verbatim)::

    {
      "zones": [
        {
          "zone_name": "Trakanon's Lair",
          "updated_overview_md": "...",      # may be empty -> null the field
          "encounters": [
            { "mob_name": "Trakanon", "updated_strategy_md": "..." },
            ...
          ]
        },
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from backend.eq2db import raids as raids_db  # noqa: E402

_INBOX = _REPO / "data" / "raids" / "rebalance_inbox"


def _zone_payload_size(z: dict) -> int:
    """Total char count across a zone's overview + every encounter strategy.
    Drives the bin-pack so chunks finish in similar wall-clock time."""
    n = len(z.get("current_overview_md") or "")
    for e in z.get("encounters", []):
        n += len(e.get("current_strategy_md") or "")
    return n


def _load_zones() -> list[dict]:
    """Every zone that has at least one encounter with strategy content OR a
    non-empty overview — anything with no content has nothing to rebalance."""
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        zone_rows = conn.execute(
            """
            SELECT z.id, z.zone_name, z.expansion_short, z.overview_md AS current_overview_md
            FROM raid_zones z
            WHERE z.overview_md IS NOT NULL AND TRIM(z.overview_md) != ''
               OR EXISTS (
                    SELECT 1 FROM raid_encounters e
                    WHERE e.raid_zone_id = z.id
                      AND e.strategy_md IS NOT NULL
                      AND TRIM(e.strategy_md) != ''
               )
            ORDER BY z.zone_name
            """
        ).fetchall()

        out: list[dict] = []
        for zrow in zone_rows:
            enc_rows = conn.execute(
                """
                SELECT mob_name, position, wiki_url, strategy_md AS current_strategy_md
                FROM raid_encounters
                WHERE raid_zone_id = ?
                  AND strategy_md IS NOT NULL
                  AND TRIM(strategy_md) != ''
                ORDER BY position, mob_name
                """,
                (zrow["id"],),
            ).fetchall()
            out.append(
                {
                    "zone_name": zrow["zone_name"],
                    "expansion_short": zrow["expansion_short"],
                    "current_overview_md": zrow["current_overview_md"] or "",
                    "encounters": [dict(e) for e in enc_rows],
                }
            )
    return out


def _balanced_chunks(zones: list[dict], n: int) -> list[list[dict]]:
    """Greedy bin-pack zones across N chunks by total payload size. Stable
    secondary sort (alphabetical) within each chunk."""
    chunks: list[list[dict]] = [[] for _ in range(n)]
    totals = [0] * n
    by_size = sorted(zones, key=lambda z: -_zone_payload_size(z))
    for zone in by_size:
        smallest = min(range(n), key=lambda i: totals[i])
        chunks[smallest].append(zone)
        totals[smallest] += _zone_payload_size(zone)
    for chunk in chunks:
        chunk.sort(key=lambda z: z["zone_name"])
    return chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=int, default=4)
    args = parser.parse_args(argv)

    zones = _load_zones()
    total_chars = sum(_zone_payload_size(z) for z in zones)
    total_encs = sum(len(z["encounters"]) for z in zones)
    print(f"Zones with content: {len(zones)}")
    print(f"Encounters:         {total_encs}")
    print(f"Total chars:        {total_chars:,}")

    chunks = _balanced_chunks(zones, args.chunks)
    _INBOX.mkdir(parents=True, exist_ok=True)
    for old in _INBOX.glob("chunk_*.json"):
        old.unlink()

    for i, chunk in enumerate(chunks, start=1):
        size = sum(_zone_payload_size(z) for z in chunk)
        encs = sum(len(z["encounters"]) for z in chunk)
        out = {"chunk_id": i, "total_chars": size, "zones": chunk}
        path = _INBOX / f"chunk_{i}.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  chunk_{i}.json  {len(chunk):3d} zones / {encs:3d} encs  {size:,} chars  -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
