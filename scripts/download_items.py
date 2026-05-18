#!/usr/bin/env python3
"""
Download all items from the Census API into data/items/items.db.

Safe to re-run: uses INSERT OR REPLACE keyed on item ID.
Progress is printed every REPORT_EVERY items.

Usage:
    python scripts/download_items.py                  # full download
    python scripts/download_items.py --limit 1000     # stop after N items (testing)
    python scripts/download_items.py --start 5000     # resume from offset
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
load_dotenv()

from census.db import DB_PATH, init_db, item_count, upsert_items

BASE_URL     = "https://census.daybreakgames.com"
PAGE_SIZE    = 100      # items per request
CONCURRENCY  = 10       # parallel requests
WRITE_EVERY  = 1000     # upsert to DB after this many items accumulated
REPORT_EVERY = 1000     # print progress every N items written
RETRY_MAX    = 3
RETRY_SLEEP  = 5.0


async def _fetch_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    service_id: str,
    start: int,
) -> list[dict]:
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
                        print(f"  [429] rate limited at start={start}, sleeping {delay:.0f}s…")
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    if resp.status != 200:
                        print(f"  [error] start={start}: HTTP {resp.status}")
                        return []
                    data = await resp.json(content_type=None)
                    if "error" in data:
                        print(f"  [flood] start={start}: {data['error']}, sleeping {delay:.0f}s…")
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return data.get("item_list") or []
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < RETRY_MAX:
                    print(f"  [retry {attempt}] start={start}: {type(exc).__name__}, sleeping {delay:.0f}s…")
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    print(f"  [error] start={start}: {type(exc).__name__} after {RETRY_MAX} attempts")
                    return []
    return []


async def _get_total(session: aiohttp.ClientSession, service_id: str) -> int | None:
    """Try to get the total item count via c:count=1."""
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


async def main(start_offset: int, item_limit: int | None) -> None:
    service_id = os.getenv("CENSUS_SERVICE_ID", "example")
    if service_id == "example":
        print("WARNING: using 'example' service ID — rate limits will be low.")

    conn = init_db(DB_PATH)
    existing = item_count(conn)
    print(f"DB: {DB_PATH}")
    print(f"Existing rows: {existing:,}")

    sem       = asyncio.Semaphore(CONCURRENCY)
    headers   = {"User-Agent": "EQ2CensusBot/1.0"}
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        total = await _get_total(session, service_id)
        if total:
            print(f"Census total items: {total:,}")
            if item_limit:
                total = min(total, item_limit)
            pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
            print(f"Pages to fetch: {pages_needed:,}  ({PAGE_SIZE}/page, {CONCURRENCY} concurrent)")
        else:
            print("Could not get total count — will stop when pages run dry.")
            pages_needed = None

        offset      = start_offset
        written     = 0
        buffer: list[dict] = []

        while True:
            if item_limit and written >= item_limit:
                break

            # Build a batch of CONCURRENCY page offsets
            batch_starts = [offset + i * PAGE_SIZE for i in range(CONCURRENCY)]
            if pages_needed is not None:
                batch_starts = [s for s in batch_starts if s < start_offset + (item_limit or 999_999_999)]

            pages = await asyncio.gather(
                *[_fetch_page(session, sem, service_id, s) for s in batch_starts]
            )

            for page in pages:
                buffer.extend(page)

            # Flush buffer to DB
            if len(buffer) >= WRITE_EVERY or any(len(p) < PAGE_SIZE for p in pages):
                if buffer:
                    upsert_items(buffer, conn)
                    written += len(buffer)
                    if written % REPORT_EVERY < len(buffer) or written == len(buffer):
                        total_in_db = item_count(conn)
                        print(f"  Written this run: {written:,}  |  Total in DB: {total_in_db:,}")
                    buffer = []

            # Stop if any page came back short (end of data)
            if any(len(p) < PAGE_SIZE for p in pages):
                break
            # Stop if all pages were empty
            if all(len(p) == 0 for p in pages):
                break

            offset += CONCURRENCY * PAGE_SIZE

    # Flush any remaining buffer
    if buffer:
        upsert_items(buffer, conn)
        written += len(buffer)

    conn.close()
    final = item_count(init_db(DB_PATH))
    print(f"\nDone. Written this run: {written:,}  |  Total in DB: {final:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",  type=int, default=0,    help="Start offset")
    parser.add_argument("--limit",  type=int, default=None, help="Max items to download (for testing)")
    args = parser.parse_args()
    asyncio.run(main(args.start, args.limit))
