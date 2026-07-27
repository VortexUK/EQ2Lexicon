"""Seed fake boss-kill parses into the local parses.db so the character
Rankings tab (and the rankings page) can be exercised without real ACT
uploads.

Creates winning raid encounters against real curated bosses from zones.db
(so titles canonicalise exactly like production uploads), with:

  * the target character parsing on every boss — kill counts, a spread of
    scores (median ≠ best), and best percentiles landing in different WCL
    colour bands (pink / purple / blue / grey) across bosses;
  * same-class peers filling out the class pool the percentiles rank against;
  * other-class filler raiders so every kill counts ≥8 players (raid scope).

All rows are stamped uploaded_by='fake-seed' — re-run with --wipe to remove
every trace.

Usage:
    uv run python scripts/dev/seed_fake_parses.py --character Menludiir
    uv run python scripts/dev/seed_fake_parses.py --character Menludiir --cls Templar
    uv run python scripts/dev/seed_fake_parses.py --wipe

The rankings dataset is cached for 60s — restart the dev backend (or wait a
minute) after seeding, then open /character/<name>?tab=rankings.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # EQ2_WORLD / DB_*_PATH from .env, same as the dev backend

from backend.eq2db.zones import catalogue as zones_db  # noqa: E402
from backend.server.parses.db import store as parses_store  # noqa: E402

UPLOADER = "fake-seed"
GUILD = "Fake Data Co"

# Same-class peers whose bests shape the target's percentile per boss.
PEERS = ["Peerless", "Middling", "Contender", "Bencher", "Startlet", "Duelist", "Reserve", "Vanguard"]
# Other-class filler raiders (name, cls) — pad every kill to raid scope and
# give the rankings page some cross-class flavour.
FILLERS = [
    ("Slashy", "Berserker"),
    ("Stabbin", "Swashbuckler"),
    ("Boomlok", "Wizard"),
    ("Dotsy", "Warlock"),
    ("Shieldy", "Guardian"),
    ("Tuneful", "Troubador"),
    ("Wardz", "Mystic"),
    ("Groveler", "Fury"),
]

# Per-boss shaping: (target parses as fraction of the class record,
# peer-best fractions). The target's best lands in a different WCL colour
# band on each boss; extra parses below the best make Med % interesting.
BOSS_SHAPES = [
    {"target": (1.0, 0.82, 0.65), "peers": (0.93, 0.88, 0.71, 0.55, 0.4)},  # record holder → gold/pink
    {"target": (0.9, 0.74), "peers": (1.0, 0.85, 0.66, 0.5, 0.33, 0.21)},  # upper-mid → purple-ish
    {"target": (0.62, 0.5, 0.44, 0.38), "peers": (1.0, 0.9, 0.8, 0.7, 0.35)},  # mid → blue/green
    {"target": (0.3,), "peers": (1.0, 0.92, 0.83, 0.75, 0.6, 0.5, 0.45)},  # bottom → grey
]


def _curated_bosses(limit: int) -> list[tuple[str, str]]:
    """(zone, mob_title) tuples from zones.db curated encounters. Falls back
    to heuristic-friendly capitalised fakes if the local db has no curation."""
    conn = sqlite3.connect(f"file:{zones_db.path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """SELECT z.name, m.mob_name_lower
               FROM zone_encounter_mobs m
               JOIN zone_encounters e ON e.id = m.encounter_id
               JOIN zones z ON z.id = e.zone_id
               WHERE length(m.mob_name_lower) >= 6
               GROUP BY e.id ORDER BY z.name, e.id LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    if rows:
        return [(zone, mob[0].upper() + mob[1:]) for zone, mob in rows]
    print("! zones.db has no curated encounters — using heuristic boss names")
    return [
        ("Deathtoll", "Tarinax the Destroyer"),
        ("Deathtoll", "Xerkizh The Creator"),
        ("The Fabled Vaults of El'Arad", "Amitrios"),
        ("Freethinker Hideout", "Zylphax the Shredder"),
    ][:limit]


def wipe(conn: sqlite3.Connection) -> None:
    n = conn.execute(
        "DELETE FROM combatants WHERE encounter_id IN (SELECT id FROM encounters WHERE uploaded_by = ?)",
        (UPLOADER,),
    ).rowcount
    m = conn.execute("DELETE FROM encounters WHERE uploaded_by = ?", (UPLOADER,)).rowcount
    conn.commit()
    print(f"wiped {m} fake encounters ({n} combatant rows)")


def _insert_kill(
    conn: sqlite3.Connection,
    *,
    world: str,
    zone: str,
    boss: str,
    started_at: int,
    duration_s: int,
    combatants: list[tuple[str, str, float, float]],  # (name, cls, encdps, enchps)
) -> None:
    total = int(sum(c[2] for c in combatants) * duration_s)
    cur = conn.execute(
        """INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at,
               duration_s, total_damage, encdps, kills, deaths, success_level,
               source_dsn, uploaded_by, guild_name, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 1, ?, ?, ?, ?)""",
        (
            world,
            f"fake-{uuid.uuid4().hex[:12]}",
            boss,
            zone,
            started_at,
            started_at + duration_s,
            duration_s,
            total,
            sum(c[2] for c in combatants),
            UPLOADER,
            UPLOADER,
            GUILD,
            int(time.time()),
        ),
    )
    enc_id = cur.lastrowid
    for name, cls, dps, hps in combatants:
        conn.execute(
            """INSERT INTO combatants (encounter_id, name, ally, started_at, ended_at,
                   duration_s, damage, dps, encdps, enchps, level, guild_name, cls,
                   ilvl, is_player)
               VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 70, ?, ?, ?, 1)""",
            (
                enc_id,
                name,
                started_at,
                started_at + duration_s,
                duration_s,
                int(dps * duration_s),
                dps,
                dps,
                hps,
                GUILD,
                cls,
                round(random.uniform(38, 62), 1),
            ),
        )


def seed(conn: sqlite3.Connection, *, character: str, cls: str, world: str) -> None:
    rng = random.Random(42)
    bosses = _curated_bosses(len(BOSS_SHAPES))
    now = int(time.time())
    for bi, ((zone, boss), shape) in enumerate(zip(bosses, BOSS_SHAPES)):
        record = rng.uniform(28_000, 45_000)  # the class record for this boss
        hps_record = record * 0.6
        # Peer kills: one kill each, spread over the last month.
        for pi, frac in enumerate(shape["peers"]):
            others = rng.sample(FILLERS, 6)
            _insert_kill(
                conn,
                world=world,
                zone=zone,
                boss=boss,
                started_at=now - rng.randint(3, 30) * 86_400 - pi * 3600,
                duration_s=rng.randint(240, 660),
                combatants=[
                    (PEERS[pi], cls, record * frac, hps_record * frac * rng.uniform(0.2, 0.5)),
                    *[(n, c, record * rng.uniform(0.4, 1.1), 800.0) for n, c in others],
                ],
            )
        # Target kills: newest, one per target fraction (best ≠ median ≠ fastest).
        for ti, frac in enumerate(shape["target"]):
            others = rng.sample(FILLERS, 7)
            _insert_kill(
                conn,
                world=world,
                zone=zone,
                boss=boss,
                started_at=now - ti * 86_400 - bi * 7200,
                duration_s=rng.randint(220, 700),
                combatants=[
                    (character, cls, record * frac, hps_record * rng.uniform(0.3, 0.9)),
                    *[(n, c, record * rng.uniform(0.4, 1.1), 800.0) for n, c in others],
                ],
            )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM encounters WHERE uploaded_by = ?", (UPLOADER,)).fetchone()[0]
    print(f"seeded {n} fake winning kills for {character} ({cls}) on {world} across {len(bosses)} bosses:")
    for zone, boss in bosses:
        print(f"  {zone} — {boss}")
    print("\nrankings cache TTL is 60s — restart the dev backend (or wait a minute), then open:")
    print(f"  /character/{character}?tab=rankings")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--character", help="target character name (as you'd browse them on the site)")
    ap.add_argument("--cls", default="Templar", help="target character's class (default Templar)")
    ap.add_argument(
        "--world", default=os.getenv("EQ2_WORLD", "Varsoon"), help="world stamp (default EQ2_WORLD/Varsoon)"
    )
    ap.add_argument("--wipe", action="store_true", help="remove all fake-seed rows and exit")
    args = ap.parse_args()

    conn = parses_store.init_db()
    # Some long-lived local parses.dbs carry a stale FK on combatants that
    # references "encounters_old" (a scar from an old table-rebuild
    # migration; the app never enables FK enforcement on parses
    # connections, so it's dormant there). Keep it dormant here too.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        if args.wipe:
            wipe(conn)
            return
        if not args.character:
            ap.error("--character is required (or use --wipe)")
        # Idempotence: a fresh seed replaces any previous fake batch.
        wipe(conn)
        seed(conn, character=args.character, cls=args.cls, world=args.world)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
