#!/usr/bin/env python3
"""
Backfill the `effects` column for spells that have NULL effects.

Fetches the missing spells from Census one page at a time (batches of
50 IDs using ?id=A,B,C) and updates only the affected rows.

Usage:
    python scripts/backfill_spell_effects.py
    python scripts/backfill_spell_effects.py --dry-run   # just show count
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.census.config import SERVICE_ID, WORLD

load_dotenv(override=True)

from backend.eq2db.spells import DB_PATH, catalogue

BASE_URL = "https://census.daybreakgames.com"
BATCH_SIZE = 50  # IDs per Census request (well within URL limits)
RETRY_MAX = 5
RETRY_SLEEP = 15.0


async def _fetch_one(
    session: aiohttp.ClientSession,
    service_id: str,
    spell_id: int,
) -> dict | None:
    url = f"{BASE_URL}/s:{service_id}/json/get/eq2/spell/{spell_id}"
    delay = RETRY_SLEEP
    for attempt in range(1, RETRY_MAX + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 429:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if resp.status != 200:
                    if attempt < RETRY_MAX:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return None
                data = await resp.json(content_type=None)
                if "error" in data or "errorCode" in data:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                spells = data.get("spell_list") or []
                return spells[0] if spells else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < RETRY_MAX:
                await asyncio.sleep(delay)
                delay *= 2
            else:
                print(f"  [failed] id={spell_id}: {type(exc).__name__}")
                return None
    return None


async def main(dry_run: bool) -> None:
    service_id = SERVICE_ID
    if service_id == "example":
        print("WARNING: using 'example' service ID — rate limits will be low.")

    conn = catalogue.init_db(DB_PATH)

    null_rows = conn.execute("SELECT id FROM spells WHERE effects IS NULL ORDER BY id").fetchall()
    ids = [r[0] for r in null_rows]
    print(f"Spells with NULL effects: {len(ids):,}")

    if not ids:
        print("Nothing to do.")
        conn.close()
        return

    if dry_run:
        print("Dry run — exiting without changes.")
        conn.close()
        return

    updated = 0
    no_effects = 0
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        for i, spell_id in enumerate(ids, 1):
            spell = await _fetch_one(session, service_id, spell_id)
            if spell is None:
                print(f"  [skip] id={spell_id} — no data from Census")
                continue

            row = catalogue.spell_to_row(spell)
            conn.execute(
                "UPDATE spells SET effects = :effects WHERE id = :id",
                {"effects": row["effects"], "id": row["id"]},
            )
            if row["effects"]:
                updated += 1
            else:
                no_effects += 1

            if i % 50 == 0 or i == len(ids):
                conn.commit()
                print(f"  {i:,} / {len(ids):,}  — updated {updated:,}  (no effects: {no_effects:,})")

    conn.commit()
    conn.close()
    print(f"\nDone. Updated: {updated:,}  |  Genuinely no effects: {no_effects:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Just show count, no changes")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
