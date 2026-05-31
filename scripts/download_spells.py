#!/usr/bin/env python3
"""
Download all spells from the Census /spell/ collection into data/spells/spells.db.

Mirrors scripts/download_items.py in structure:
  - Resumes automatically from saved offset (stored in DB _meta table)
  - Safe to re-run: uses INSERT OR REPLACE keyed on spell ID
  - ~167,000 spells total; takes a few minutes on a good connection

Usage:
    python scripts/download_spells.py                  # full download / resume
    python scripts/download_spells.py --limit 1000     # stop after N spells (testing)
    python scripts/download_spells.py --restart        # ignore saved offset, start from 0
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

from backend.eq2db.spells import DB_PATH, get_meta, init_db, set_meta, spell_count, upsert_spells

BASE_URL = "https://census.daybreakgames.com"
PAGE_SIZE = 100  # Census silently caps responses at 100 regardless of c:limit
CONCURRENCY = 1  # sequential is most reliable against Census timeouts
WRITE_EVERY = 2000  # flush to DB after accumulating this many spells
RETRY_MAX = 5
RETRY_SLEEP = 15.0  # seconds before first retry (doubles each attempt)


async def _fetch_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    service_id: str,
    start: int,
) -> list[dict] | None:
    url = f"{BASE_URL}/s:{service_id}/json/get/eq2/spell/"
    params = {"c:start": start, "c:limit": PAGE_SIZE, "c:sort": "id:1"}
    delay = RETRY_SLEEP

    async with sem:
        for attempt in range(1, RETRY_MAX + 1):
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 429:
                        print(f"  [429] start={start}, sleeping {delay:.0f}s…")
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    if resp.status != 200:
                        print(f"  [error] start={start}: HTTP {resp.status}")
                        if attempt < RETRY_MAX:
                            await asyncio.sleep(delay)
                            delay *= 2
                            continue
                        return None
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        if attempt < RETRY_MAX:
                            print(f"  [retry {attempt}] start={start}: bad JSON, sleeping {delay:.0f}s…")
                            await asyncio.sleep(delay)
                            delay *= 2
                            continue
                        return None
                    if "error" in data or "errorCode" in data:
                        msg = data.get("error") or data.get("errorCode") or "unknown"
                        print(f"  [api-error] start={start}: {msg}, sleeping {delay:.0f}s…")
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return data.get("spell_list") or []
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < RETRY_MAX:
                    print(f"  [retry {attempt}] start={start}: {type(exc).__name__}, sleeping {delay:.0f}s…")
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    print(f"  [failed] start={start}: {type(exc).__name__} after {RETRY_MAX} attempts — skipping")
                    return None
    return None


async def _get_total(session: aiohttp.ClientSession, service_id: str) -> int | None:
    url = f"{BASE_URL}/s:{service_id}/json/get/eq2/spell/"
    try:
        async with session.get(
            url, params={"c:count": 1, "c:limit": 1}, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                count = data.get("count")
                if count is not None:
                    return int(count)
    except Exception:
        pass
    return None


async def main(restart: bool, spell_limit: int | None) -> None:
    service_id = SERVICE_ID
    if service_id == "example":
        print("WARNING: using 'example' service ID — rate limits will be low.")

    conn = init_db(DB_PATH)
    existing = spell_count(conn)
    print(f"DB:            {DB_PATH}")
    print(f"Existing rows: {existing:,}")

    if restart:
        set_meta(conn, "download_offset", "0")
        offset = 0
        print("Restarting from offset 0.")
    else:
        saved = get_meta(conn, "download_offset")
        offset = int(saved) if saved else 0
        print(f"{'Resuming from' if offset else 'Starting at'} offset {offset:,}")

    sem = asyncio.Semaphore(CONCURRENCY)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        total = await _get_total(session, service_id)
        if total:
            print(f"Census total spells: {total:,}  (limit: {spell_limit or 'none'})")
        else:
            print("Could not get total count — will stop when pages run dry.")

        written = 0
        buffer: list[dict] = []
        reached_end = False

        while True:
            if spell_limit and written >= spell_limit:
                break

            page = await _fetch_page(session, sem, service_id, offset)

            if page is None:
                print(f"  Page at offset {offset:,} failed — sleeping 30s before retry…")
                await asyncio.sleep(30)
                continue

            if not page:
                # Empty page — check if we're genuinely at the end
                if total and offset < total - PAGE_SIZE * 2:
                    print(
                        f"  [warn] empty page at offset {offset:,} but total={total:,} "
                        "— treating as transient gap, continuing"
                    )
                    offset += PAGE_SIZE
                    set_meta(conn, "download_offset", str(offset))
                    continue
                reached_end = True
            else:
                buffer.extend(page)
                offset += PAGE_SIZE
                set_meta(conn, "download_offset", str(offset))

            if len(buffer) >= WRITE_EVERY or reached_end:
                if buffer:
                    upsert_spells(buffer, conn)
                    written += len(buffer)
                    buffer = []
                    total_in_db = spell_count(conn)
                    print(f"  Written this run: {written:,}  |  Total in DB: {total_in_db:,}  |  Offset: {offset:,}")

            if reached_end:
                break

    if buffer:
        upsert_spells(buffer, conn)
        written += len(buffer)

    if reached_end:
        set_meta(conn, "download_offset", "0")
        print("\nReached end of Census data — offset reset for next run.")

    conn.close()
    final = spell_count(init_db(DB_PATH))
    print(f"\nDone. Written this run: {written:,}  |  Total in DB: {final:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max spells to download this run (for testing)")
    parser.add_argument("--restart", action="store_true", help="Ignore saved offset and start from 0")
    args = parser.parse_args()
    asyncio.run(main(args.restart, args.limit))
