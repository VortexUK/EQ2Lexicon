"""Seed ``data/parses/parses.db`` with synthetic raid kills for demo screenshots.

Strictly a dev helper. Not for prod, not in CI. Auto-detects the user's primary
character from ``data/users.db``, Census-resolves the guild, then inserts a
hand-tuned mix of kills (some full clears, some partial, timestamps spread
across the last ~30 days) so the ``/raids`` UI lights up end-to-end.

Re-runnable: tracks inserted IDs in ``data/parses/.seed_raid_kills.json`` and
deletes them on the next run before re-seeding. Run with ``--unseed`` to remove
the rows without re-inserting (good for clean before-merge teardown).

Usage:
    .venv/Scripts/python scripts/dev/seed_raid_kills.py
    .venv/Scripts/python scripts/dev/seed_raid_kills.py --unseed
    .venv/Scripts/python scripts/dev/seed_raid_kills.py --guild "Exordium"  # override auto-detect
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

# Repo root on path so the script can import as a module.
_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv  # noqa: E402

from backend.census.client import CensusClient  # noqa: E402
from backend.eq2db import zones as zones_db  # noqa: E402
from backend.server.db import DB_PATH as USERS_DB_PATH  # noqa: E402
from backend.server.parses.db import DB_PATH as PARSES_DB_PATH  # noqa: E402

load_dotenv(_REPO / ".env")

_MANIFEST = PARSES_DB_PATH.parent / ".seed_raid_kills.json"

# ---------------------------------------------------------------------------
# Seed plan — which encounters to kill in which zones.
# Encounter positions are taken from the curated roster (see
# scripts/dev/eq2_raid_bosses.review.txt). "all" means full clear.
# ---------------------------------------------------------------------------

SEED_PLAN: list[dict] = [
    # Mid-progression: cleared first two floors, stuck on the final tier.
    {"zone": "The Emerald Halls", "positions": [1, 2, 3, 4, 5, 6, 7, 8], "kills_per": (2, 5)},
    # Partial — early raid attempts.
    {"zone": "Veeshan's Peak", "positions": [1, 2, 3, 5, 8], "kills_per": (1, 3)},
    # Full clear — demonstrates the "complete" success-coloured progress state.
    {"zone": "Trakanon's Lair", "positions": "all", "kills_per": (4, 8)},
    # Sparse — recent first-time kills.
    {"zone": "Shard of Hate", "positions": [1, 2, 3], "kills_per": (1, 2)},
]

# Timestamps span the last 30 days — newest kill ~12 hours ago so "Last killed"
# reads as a recent ago value rather than "just now".
NOW = int(time.time())
WINDOW_OLDEST = NOW - 86_400 * 30
WINDOW_NEWEST = NOW - 86_400 // 2

# Fixed RNG seed = reproducible demos. Want fresh data each run? bump this.
RNG = random.Random(0xEA2D)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_primary_character() -> tuple[str, str] | None:
    """Return ``(discord_id, character_name)`` of the user's primary claim.

    Picks the oldest approved+primary claim if multiple exist (single-user dev
    DBs effectively only have one). Returns None if no primary is set.
    """
    if not USERS_DB_PATH.exists():
        return None
    with sqlite3.connect(USERS_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT discord_id, character_name FROM character_claims
            WHERE status = 'approved' AND is_primary = 1
            ORDER BY requested_at ASC LIMIT 1
            """,
        ).fetchone()
    return (row[0], row[1]) if row else None


async def _resolve_guild_via_census(character_name: str, world: str, attempts: int = 4) -> str | None:
    """Census lookup with retries — the TLE server's API returns sparse
    payloads intermittently (the guild field is missing on some responses for
    a character that is genuinely in a guild). Returns None if every attempt
    still came back without a guild."""
    client = CensusClient(service_id=os.getenv("CENSUS_SERVICE_ID", "example"))
    try:
        for i in range(attempts):
            try:
                guild = await client.get_character_guild_name(character_name, world)
            except Exception as exc:
                print(f"  [attempt {i + 1}/{attempts}] Census error: {exc}")
                guild = None
            if guild:
                return guild
            if i < attempts - 1:
                await asyncio.sleep(1.0 + i * 0.5)
        return None
    finally:
        await client.close()


def _pick_mob_name(encounter: dict) -> str:
    """For a curator encounter, pick one of its mobs as the parse title.

    Solo encounters return the single mob (== encounter_name). Group encounters
    pick one at random — exactly mirrors how ACT reports group fights (it logs
    one of the engaged mobs as the encounter title)."""
    mobs = encounter.get("mobs") or [{"mob_name": encounter["encounter_name"]}]
    return RNG.choice(mobs)["mob_name"]


def _resolve_seed_targets() -> list[tuple[str, dict, tuple[int, int]]]:
    """Expand SEED_PLAN into a flat ``[(zone, encounter, kills_per_range), …]`` list."""
    out: list[tuple[str, dict, tuple[int, int]]] = []
    for entry in SEED_PLAN:
        zone_name = entry["zone"]
        bosses = zones_db.catalogue.list_bosses_for_zone(zone_name)
        if not bosses:
            print(f"  [warn] zone not in curated roster, skipping: {zone_name}")
            continue
        if entry["positions"] == "all":
            chosen = bosses
        else:
            wanted = set(entry["positions"])
            chosen = [b for b in bosses if b["position"] in wanted]
        for enc in chosen:
            out.append((zone_name, enc, entry["kills_per"]))
    return out


def _generate_rows(guild_name: str, discord_id: str) -> list[dict]:
    """Build the encounter rows to insert. One row per (encounter × kill count).

    Returns dicts (rather than tuples) so the INSERT below can use named
    parameters — the encounters table has more NOT-NULL columns than the
    progress-pipeline needs, so explicit naming reads better than a wide
    positional tuple."""
    rows: list[dict] = []
    seq = 0
    for zone_name, enc, kills_per in _resolve_seed_targets():
        kill_count = RNG.randint(*kills_per)
        for _ in range(kill_count):
            seq += 1
            title = _pick_mob_name(enc)
            started_at = RNG.randint(WINDOW_OLDEST, WINDOW_NEWEST)
            duration_s = RNG.randint(120, 600)  # 2–10 minute raid encounter
            rows.append(
                {
                    "act_encid": f"seed-{discord_id}-{seq}-{started_at}",  # UNIQUE
                    "title": title,
                    "zone": zone_name,
                    "started_at": started_at,
                    "ended_at": started_at + duration_s,
                    "duration_s": duration_s,
                    "success_level": 1,  # win
                    "source_dsn": "seed",
                    "uploaded_by": discord_id,
                    "guild_name": guild_name,
                    "ingested_at": NOW,
                }
            )
    return rows


def _delete_previous(conn: sqlite3.Connection) -> int:
    """Remove rows from the previous seed run, if any."""
    if not _MANIFEST.exists():
        return 0
    try:
        ids = json.loads(_MANIFEST.read_text())
    except Exception:
        ids = []
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(f"DELETE FROM encounters WHERE id IN ({placeholders})", ids)
    conn.commit()
    deleted = cur.rowcount
    _MANIFEST.unlink(missing_ok=True)
    return deleted


def _write_manifest(ids: list[int]) -> None:
    _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    _MANIFEST.write_text(json.dumps(ids))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unseed", action="store_true", help="Delete previous seed rows without re-inserting.")
    parser.add_argument("--guild", help="Override the auto-detected guild name (e.g. for testing other guilds).")
    args = parser.parse_args()

    if not PARSES_DB_PATH.exists():
        print(f"parses.db not found at {PARSES_DB_PATH} — nothing to seed/unseed.")
        return 1

    # ─── Unseed-only path ─────────────────────────────────────────────────────
    if args.unseed:
        with sqlite3.connect(PARSES_DB_PATH) as conn:
            n = _delete_previous(conn)
        print(f"Removed {n} previously-seeded rows.")
        return 0

    # ─── Resolve the target guild ─────────────────────────────────────────────
    if args.guild:
        primary = _read_primary_character()
        if not primary:
            print("--guild given but no primary character found — using 'local' as uploader.")
            discord_id = "local"
        else:
            discord_id, _ = primary
        guild_name = args.guild
    else:
        primary = _read_primary_character()
        if not primary:
            print("No primary character set in data/users.db. Either set one in the UI first or pass --guild.")
            return 1
        discord_id, character_name = primary
        world = os.getenv("EQ2_WORLD", "Varsoon")
        print(f"Resolving guild for primary character '{character_name}' on {world} …")
        guild_name = await _resolve_guild_via_census(character_name, world)
        if not guild_name:
            print(
                "Census didn't return a guild after retries. The character may not be in one, "
                'or Census is dropping the guild field. Re-run with --guild "<Name>" to skip the lookup.'
            )
            return 1
        print(f"  -> guild_name = {guild_name!r}")

    # ─── Insert (clearing any previous seed first) ────────────────────────────
    rows = _generate_rows(guild_name, discord_id)
    if not rows:
        print("Nothing to insert (seed plan resolved to zero rows).")
        return 1

    with sqlite3.connect(PARSES_DB_PATH) as conn:
        cleared = _delete_previous(conn)
        inserted_ids: list[int] = []
        for r in rows:
            cur = conn.execute(
                """
                INSERT INTO encounters
                    (act_encid, title, zone, started_at, ended_at, duration_s,
                     success_level, source_dsn, uploaded_by, guild_name, ingested_at)
                VALUES
                    (:act_encid, :title, :zone, :started_at, :ended_at, :duration_s,
                     :success_level, :source_dsn, :uploaded_by, :guild_name, :ingested_at)
                """,
                r,
            )
            if cur.lastrowid is not None:
                inserted_ids.append(int(cur.lastrowid))
        conn.commit()

    _write_manifest(inserted_ids)
    if cleared:
        print(f"Cleared {cleared} rows from the previous seed run.")
    print(f"Inserted {len(inserted_ids)} synthetic kills for guild {guild_name!r} (uploaded_by={discord_id!r}).")
    print(f"Manifest: {_MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
