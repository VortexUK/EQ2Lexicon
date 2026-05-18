#!/usr/bin/env python3
"""
Download all items from the Census API into data/items/items.db.

Resumes automatically from where it left off (offset stored in DB).
Safe to re-run: uses INSERT OR REPLACE keyed on item ID.

Usage:
    python scripts/download_items.py                  # full download / resume
    python scripts/download_items.py --limit 1000     # stop after N items (testing)
    python scripts/download_items.py --restart        # ignore saved offset, start from 0
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
load_dotenv(override=True)

from census.db import DB_PATH, get_meta, init_db, item_count, set_meta, upsert_items

BASE_URL     = "https://census.daybreakgames.com"
PAGE_SIZE    = 100      # items per request
CONCURRENCY  = 10       # parallel requests
WRITE_EVERY  = 1000     # upsert to DB after this many items accumulated
REPORT_EVERY = 1000     # print progress every N items written
RETRY_MAX    = 5
RETRY_SLEEP  = 10.0     # seconds before first retry (doubles each attempt)


async def _fetch_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    service_id: str,
    start: int,
) -> list[dict] | None:
    """
    Fetch one page. Returns:
      list[dict]  — success (may be shorter than PAGE_SIZE at end of data)
      None        — all retries exhausted; caller should skip and continue
    """
    url    = f"{BASE_URL}/s:{service_id}/json/get/eq2/item/"
    params = {"c:start": start, "c:limit": PAGE_SIZE}
    delay  = RETRY_SLEEP

    async with sem:
        for attempt in range(1, RETRY_MAX + 1):
            try:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
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
                    data = await resp.json(content_type=None)
                    if "error" in data:
                        print(f"  [flood] start={start}: sleeping {delay:.0f}s…")
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return data.get("item_list") or []
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < RETRY_MAX:
                    print(f"  [retry {attempt}/{RETRY_MAX}] start={start}: {type(exc).__name__}, sleeping {delay:.0f}s…")
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    print(f"  [failed] start={start}: {type(exc).__name__} after {RETRY_MAX} attempts — skipping")
                    return None
    return None


async def _get_total(session: aiohttp.ClientSession, service_id: str) -> int | None:
    url = f"{BASE_URL}/s:{service_id}/json/get/eq2/item/"
    try:
        async with session.get(
            url,
            params={"c:count": 1, "c:limit": 1},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                count = data.get("count")
                if count is not None:
                    return int(count)
    except Exception:
        pass
    return None


async def main(restart: bool, item_limit: int | None) -> None:
    service_id = os.getenv("CENSUS_SERVICE_ID", "example")
    if service_id == "example":
        print("WARNING: using 'example' service ID — rate limits will be low.")

    conn = init_db(DB_PATH)
    existing = item_count(conn)
    print(f"DB: {DB_PATH}")
    print(f"Existing rows: {existing:,}")

    # Resume offset
    if restart:
        set_meta(conn, "download_offset", "0")
        offset = 0
        print("Restarting from offset 0.")
    else:
        saved = get_meta(conn, "download_offset")
        offset = int(saved) if saved else 0
        if offset:
            print(f"Resuming from offset {offset:,}")
        else:
            print("Starting from offset 0.")

    sem       = asyncio.Semaphore(CONCURRENCY)
    headers   = {"User-Agent": "EQ2CensusBot/1.0"}
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        total = await _get_total(session, service_id)
        if total:
            print(f"Census total items: {total:,}  (limit: {item_limit or 'none'})")
        else:
            print("Could not get total count — will stop when pages run dry.")

        written     = 0
        buffer: list[dict] = []
        reached_end = False

        while True:
            if item_limit and written >= item_limit:
                break

            batch_starts = [offset + i * PAGE_SIZE for i in range(CONCURRENCY)]

            pages = await asyncio.gather(
                *[_fetch_page(session, sem, service_id, s) for s in batch_starts]
            )

            # Separate successful pages from failed ones
            # None = error (skip but don't treat as end), list = success
            successful = [(s, p) for s, p in zip(batch_starts, pages) if p is not None]
            failed     = [s for s, p in zip(batch_starts, pages) if p is None]

            if failed:
                print(f"  [warn] {len(failed)} page(s) failed and will be skipped: offsets {failed}")

            for _, page in successful:
                buffer.extend(page)

            # Check if we've reached the end of data (a successful page shorter than PAGE_SIZE)
            if any(len(p) < PAGE_SIZE for _, p in successful):
                reached_end = True

            # Flush buffer to DB and save offset
            if len(buffer) >= WRITE_EVERY or reached_end:
                if buffer:
                    upsert_items(buffer, conn)
                    written += len(buffer)
                    buffer = []
                    # Save the next offset to resume from
                    next_offset = offset + CONCURRENCY * PAGE_SIZE
                    set_meta(conn, "download_offset", str(next_offset))
                    total_in_db = item_count(conn)
                    print(f"  Written this run: {written:,}  |  Total in DB: {total_in_db:,}  |  Offset: {next_offset:,}")

            if reached_end:
                break
            # If ALL pages failed (not just some), pause before retrying
            if not successful:
                print(f"  All pages in batch failed — sleeping 30s before retry…")
                await asyncio.sleep(30)
                continue

            offset += CONCURRENCY * PAGE_SIZE

    # Flush remainder
    if buffer:
        upsert_items(buffer, conn)
        written += len(buffer)
        set_meta(conn, "download_offset", str(offset))

    if reached_end:
        set_meta(conn, "download_offset", "0")  # reset so next run starts fresh
        print("\nReached end of Census data — offset reset for next run.")

    conn.close()
    final = item_count(init_db(DB_PATH))
    print(f"\nDone. Written this run: {written:,}  |  Total in DB: {final:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int,  default=None,  help="Max items to download this run (for testing)")
    parser.add_argument("--restart", action="store_true",       help="Ignore saved offset and start from 0")
    args = parser.parse_args()
    asyncio.run(main(args.restart, args.limit))
