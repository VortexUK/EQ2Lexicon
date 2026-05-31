"""Ingest scraped EQ2i raid JSON into ``data/raids/raids.db``.

Sibling of ``scrape_eq2i_raids.py`` — that one produces a JSON snapshot of
zone + encounter pages; this one writes that snapshot into the SQLite store
via the existing ``raids_db.upsert_raid_zone`` / ``upsert_raid_encounter``
helpers.

Why split? The scrape is the expensive, network-dependent step (cached on
disk via ``scripts/dev/.eq2i_cache``); the ingest is a fast local pass that
benefits from being re-runnable without re-fetching anything.

Safety: ``upsert_raid_encounter`` already skips ``SOURCE_MANUAL`` rows on a
re-scrape (it refreshes wiki_url/position/last_synced_at but leaves
``strategy_md`` alone), so re-running this script never overwrites a human
edit. The first ever scrape per encounter also records a revision row with
``before_md = NULL`` so the audit history starts clean.

Usage::

    .venv/Scripts/python scripts/dev/ingest_raids_json.py
    # defaults to scripts/dev/eq2i_raids.sample.json

    .venv/Scripts/python scripts/dev/ingest_raids_json.py --in scripts/dev/eq2_raid_data.json
    .venv/Scripts/python scripts/dev/ingest_raids_json.py --in <path> --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Repo root on path so `census.*` imports work when invoked as a script.
_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from backend.eq2db import raids as raids_db  # noqa: E402
from backend.eq2db import zones as zones_db

_DEFAULT_IN = _REPO / "scripts" / "dev" / "eq2i_raids.sample.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_expansion_short(zone_name: str, default: str = "Unknown") -> str:
    """Look up the canonical zone in zones.db to pull its expansion_short.

    The scrape JSON doesn't carry expansion (it's a wiki concept, not an EQ2i
    template field). We sync against zones.db so the raid_zones row mirrors
    the canonical record."""
    z = zones_db.find_by_name(zone_name)
    if z is None:
        return default
    return z.get("expansion_short") or default


def _normalise_md(md: str | None) -> str | None:
    """Treat empty / whitespace-only as None so the upsert helper doesn't
    record a noise revision (after_md='') for encounters whose wiki page has
    no Strategy section yet."""
    if md is None:
        return None
    stripped = md.strip()
    return stripped if stripped else None


# EQ2i uses parenthetical suffixes to disambiguate wiki page titles when a
# name is reused (e.g. "Trakanon" the disambig page vs "Trakanon (Monster)"
# the raid version, "Mayong Mistmoore" vs "Mayong Mistmoore (Instanced)").
# The curated zones_db roster uses the plain name; stripping the suffix at
# ingest time keeps the strategy editor's lookup (mob_name_lower) hitting
# the right row.
import re  # noqa: E402

_DISAMBIG_SUFFIX_RE = re.compile(r"\s*\([^)]+\)\s*$")


def _normalise_mob_name(name: str) -> str:
    """Strip a trailing parenthetical disambiguator from a wiki mob name.

    Examples::

        Trakanon (Monster)            -> Trakanon
        Mayong Mistmoore (Instanced)  -> Mayong Mistmoore
        Druushk                        -> Druushk
    """
    return _DISAMBIG_SUFFIX_RE.sub("", name).strip()


def _dedupe_encounters_for_zone(encounters: list[dict]) -> list[dict]:
    """Collapse encounters whose normalised mob_name collides. When two
    scrape entries normalise to the same canonical name (e.g. a disambig
    landing page + the actual mob page), keep the one with the longer
    ``strategy_md`` — the empty/stub entry would otherwise overwrite the
    real content."""
    by_norm: dict[str, dict] = {}
    for enc in encounters:
        norm = _normalise_mob_name(enc.get("mob_name") or "")
        if not norm:
            continue
        prev = by_norm.get(norm.lower())
        cur_len = len((enc.get("strategy_md") or "").strip())
        if prev is None or cur_len > len((prev.get("strategy_md") or "").strip()):
            by_norm[norm.lower()] = enc
    return list(by_norm.values())


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest(json_path: Path, *, dry_run: bool = False) -> dict:
    """Read the scrape JSON and write rows into raids.db.

    Returns a counts dict for the CLI summary."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    zones = data.get("zones") or []

    n_zones_inserted = 0
    n_zones_skipped_manual = 0
    n_encs_inserted = 0
    n_encs_with_strategy = 0
    n_encs_empty = 0

    conn = raids_db.init_db()
    try:
        for z in zones:
            zone_name = z["zone_name"]
            expansion_short = _resolve_expansion_short(zone_name)
            wiki_url = z.get("wiki_url")

            # Zone-level metadata. The scrape produces background_md +
            # overview_md but also section headers like "Access". The
            # scraper already maps "access" → it'd live in
            # z['metadata'] or similar; for v1 we just pull
            # background/overview through.
            zone_kwargs = {
                "zone_name": zone_name,
                "expansion_short": expansion_short,
                "wiki_url": wiki_url,
                "background_md": _normalise_md(z.get("background_md")),
                "overview_md": _normalise_md(z.get("overview_md") or z.get("strategy_md")),
                "access_md": _normalise_md(z.get("access_md")),
                "level_range": (z.get("metadata") or {}).get("level_range"),
                "zdiff": (z.get("metadata") or {}).get("difficulty") or (z.get("metadata") or {}).get("zdiff"),
                "lockout_min": (z.get("metadata") or {}).get("lockout_min"),
                "lockout_max": (z.get("metadata") or {}).get("lockout_max"),
                "source": raids_db.SOURCE_SCRAPE,
            }

            if dry_run:
                print(f"  [dry-run] would upsert zone {zone_name!r} ({expansion_short})")
            else:
                zone_id = raids_db.upsert_raid_zone(conn, **zone_kwargs)
                n_zones_inserted += 1

                # Dedupe by normalised name within the zone, then iterate.
                # Normalising both the row's mob_name AND collapsing
                # collisions means a wiki disambig pair (e.g. "Trakanon"
                # the empty landing page + "Trakanon (Monster)" the raid
                # version) lands as a single canonically-named row.
                for enc in _dedupe_encounters_for_zone(z.get("encounters") or []):
                    md = _normalise_md(enc.get("strategy_md"))
                    raids_db.upsert_raid_encounter(
                        conn,
                        raid_zone_id=zone_id,
                        mob_name=_normalise_mob_name(enc["mob_name"]),
                        position=int(enc.get("position") or 0),
                        strategy_md=md,
                        wiki_url=enc.get("wiki_url"),
                        source=raids_db.SOURCE_SCRAPE,
                        edit_note="initial scrape",
                    )
                    n_encs_inserted += 1
                    if md:
                        n_encs_with_strategy += 1
                    else:
                        n_encs_empty += 1
    finally:
        conn.close()

    return {
        "zones_inserted": n_zones_inserted,
        "zones_skipped_manual": n_zones_skipped_manual,
        "encounters_inserted": n_encs_inserted,
        "encounters_with_strategy": n_encs_with_strategy,
        "encounters_empty": n_encs_empty,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--in",
        dest="json_path",
        type=Path,
        default=_DEFAULT_IN,
        help=f"Path to the scrape JSON. Default: {_DEFAULT_IN.relative_to(_REPO)}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + summarise without writing anything to raids.db.",
    )
    args = parser.parse_args(argv)

    if not args.json_path.exists():
        print(f"Input file not found: {args.json_path}", file=sys.stderr)
        print("Run scripts/dev/scrape_eq2i_raids.py first (use --all-raids for the full dataset).")
        return 1

    print(f"Reading {args.json_path}")
    stats = ingest(args.json_path, dry_run=args.dry_run)
    label = "(dry-run) " if args.dry_run else ""
    print(f"\n{label}Ingest complete:")
    for k, v in stats.items():
        print(f"  {k:30s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
