"""Export current scraped strategies into N balanced JSON chunks for
parallel-agent polishing.

Reads ``raids.db`` for every encounter that has non-empty ``strategy_md``,
groups by zone, then splits across ``--chunks`` files balanced by total
char count. Writes each chunk to ``data/raids/polish_inbox/chunk_<n>.json``.

Each chunk's shape::

    {
      "chunk_id": 1,
      "total_chars": 18234,
      "entries": [
        {
          "zone_name": "Veeshan's Peak",
          "mob_name":  "Druushk",
          "position":  1,
          "wiki_url":  "https://eq2.fandom.com/wiki/Druushk",
          "current_md": "...the scraped strategy markdown..."
        },
        ...
      ]
    }

The polish agents write their output files to ``data/raids/polish_outbox/``
following the matching shape (see ``apply_polish.py``)."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from backend.eq2db import raids as raids_db  # noqa: E402

_INBOX = _REPO / "data" / "raids" / "polish_inbox"


def _load_entries() -> list[dict]:
    """Pull every (zone, mob, current_md) with non-empty strategy from
    raids.db, sorted by zone then position for stable ordering."""
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT z.zone_name, e.mob_name, e.position, e.wiki_url, e.strategy_md, e.source
            FROM raid_encounters e
            JOIN raid_zones z ON z.id = e.raid_zone_id
            WHERE e.strategy_md IS NOT NULL AND TRIM(e.strategy_md) != ''
            ORDER BY z.zone_name, e.position, e.mob_name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _balanced_chunks(entries: list[dict], n: int, body_key: str = "current_md") -> list[list[dict]]:
    """Greedy bin-packing: sort entries by char count desc, then place each
    into the chunk with the smallest current total. Tends to produce chunks
    within ~10% of each other in total chars.

    ``body_key`` is the dict key whose value's length drives the balance —
    pass the field that actually holds the markdown."""
    chunks: list[list[dict]] = [[] for _ in range(n)]
    totals = [0] * n
    by_size = sorted(entries, key=lambda e: -len(e.get(body_key) or ""))
    for entry in by_size:
        smallest = min(range(n), key=lambda i: totals[i])
        chunks[smallest].append(entry)
        totals[smallest] += len(entry.get(body_key) or "")
    # Within each chunk, restore (zone, position) order for readability.
    for chunk in chunks:
        chunk.sort(key=lambda e: (e["zone_name"], e["position"], e["mob_name"]))
    return chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=int, default=6, help="Number of output chunks (default 6).")
    parser.add_argument(
        "--skip-manual",
        action="store_true",
        default=True,
        help="Skip rows whose source is already 'manual' (default: skip — don't re-polish hand edits).",
    )
    parser.add_argument(
        "--include-manual",
        action="store_false",
        dest="skip_manual",
        help="Override --skip-manual; include even hand-edited rows.",
    )
    args = parser.parse_args(argv)

    entries = _load_entries()
    if args.skip_manual:
        entries = [e for e in entries if e.get("source") != raids_db.SOURCE_MANUAL]

    # Drop the source column from output (agents don't need it).
    for e in entries:
        e.pop("source", None)
        e["current_md"] = e.pop("strategy_md")

    print(f"Total polishable encounters: {len(entries)}")
    print(f"Total char count:           {sum(len(e['current_md']) for e in entries):,}")

    chunks = _balanced_chunks(entries, args.chunks)
    _INBOX.mkdir(parents=True, exist_ok=True)
    # Clear any prior chunks so an older run doesn't leak into the new spawn.
    for old in _INBOX.glob("chunk_*.json"):
        old.unlink()

    for i, chunk in enumerate(chunks, start=1):
        total_chars = sum(len(e["current_md"]) for e in chunk)
        out = {"chunk_id": i, "total_chars": total_chars, "entries": chunk}
        path = _INBOX / f"chunk_{i}.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  chunk_{i}.json  {len(chunk):3d} entries  {total_chars:,} chars  -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
