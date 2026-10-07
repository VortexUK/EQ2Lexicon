"""Dump current raid_zones.overview_md into N balanced chunks for agent cleanup.

    uv run python scripts/dev/export_zone_overviews.py [--chunks 2]

Reads the raids schema; writes data/raids/zone_overview_inbox/chunk_<n>.json. Workflow
and chunk shapes: docs/runbooks/raid-strategy-agent-polish.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from backend.eq2db.raids import catalogue as raids_db  # noqa: E402

_INBOX = _REPO / "data" / "raids" / "zone_overview_inbox"


def _balanced_chunks(entries: list[dict], n: int) -> list[list[dict]]:
    """Greedy bin-pack by current_md char count. Inlined (rather than reused
    from export_for_polish.py) because that one's secondary sort is on
    encounter-specific keys we don't have here."""
    chunks: list[list[dict]] = [[] for _ in range(n)]
    totals = [0] * n
    by_size = sorted(entries, key=lambda e: -len(e.get("current_md") or ""))
    for entry in by_size:
        smallest = min(range(n), key=lambda i: totals[i])
        chunks[smallest].append(entry)
        totals[smallest] += len(entry.get("current_md") or "")
    return chunks


def _load_entries() -> list[dict]:
    """Every raid_zones row with non-empty overview_md."""
    with raids_db.init_db() as conn:
        return conn.execute(
            """
            SELECT zone_name, expansion_short, source,
                   overview_md AS current_md,
                   -- Bosses list joined-in so the agent can see which mobs
                   -- belong to the zone and recognise per-boss content to
                   -- strip out.
                   (SELECT string_agg(e.mob_name, ', ' ORDER BY e.position)
                      FROM raid_encounters e
                      WHERE e.raid_zone_id = raid_zones.id) AS encounters
            FROM raid_zones
            WHERE overview_md IS NOT NULL AND TRIM(overview_md) != ''
            ORDER BY zone_name
            """
        ).fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=int, default=2, help="Number of output chunks (default 2).")
    args = parser.parse_args(argv)

    entries = _load_entries()
    print(f"Zones with non-empty overview_md: {len(entries)}")
    print(f"Total char count:                 {sum(len(e['current_md']) for e in entries):,}")

    # The encounter list (joined-in for the agent's context) doesn't need
    # to feed into chunk balancing — bin-pack on the overview content only.
    chunks = _balanced_chunks(entries, args.chunks)
    # Restore (zone_name) ordering inside each chunk.
    for chunk in chunks:
        chunk.sort(key=lambda e: e["zone_name"])

    _INBOX.mkdir(parents=True, exist_ok=True)
    for old in _INBOX.glob("chunk_*.json"):
        old.unlink()

    for i, chunk in enumerate(chunks, start=1):
        total_chars = sum(len(e["current_md"]) for e in chunk)
        out = {"chunk_id": i, "total_chars": total_chars, "entries": chunk}
        path = _INBOX / f"chunk_{i}.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  chunk_{i}.json  {len(chunk):3d} zones  {total_chars:,} chars  -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
