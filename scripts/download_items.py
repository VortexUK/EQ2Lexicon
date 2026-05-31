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

from backend.census.config import SERVICE_ID, WORLD

load_dotenv(override=True)

from backend.census.client import BASE_URL
from backend.eq2db.items import DB_PATH, get_meta, init_db, item_count, set_meta, upsert_items

PAGE_SIZE = 100  # items per request
CONCURRENCY = 1  # parallel requests (sequential — most reliable against Census timeouts)
WRITE_EVERY = 1000  # upsert to DB after this many items accumulated
REPORT_EVERY = 1000  # print progress every N items written
RETRY_MAX = 5
RETRY_SLEEP = 15.0  # seconds before first retry (doubles each attempt)


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
    url = f"{BASE_URL}/s:{service_id}/json/get/eq2/item/"
    params = {"c:start": start, "c:limit": PAGE_SIZE, "c:sort": "id:-1"}
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
                        # Empty or malformed body — treat like a transient error
                        if attempt < RETRY_MAX:
                            print(
                                f"  [retry {attempt}/{RETRY_MAX}] start={start}: bad JSON body, sleeping {delay:.0f}s…"
                            )
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
                    return data.get("item_list") or []
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < RETRY_MAX:
                    print(
                        f"  [retry {attempt}/{RETRY_MAX}] start={start}: {type(exc).__name__}, sleeping {delay:.0f}s…"
                    )
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
    service_id = SERVICE_ID
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
            print(f"Census total items: {total:,}  (limit: {item_limit or 'none'})")
        else:
            print("Could not get total count — will stop when pages run dry.")

        written = 0
        buffer: list[dict] = []
        reached_end = False

        while True:
            if item_limit and written >= item_limit:
                break

            batch_starts = [offset + i * PAGE_SIZE for i in range(CONCURRENCY)]

            pages = await asyncio.gather(*[_fetch_page(session, sem, service_id, s) for s in batch_starts])

            # Separate successful pages from failed ones
            # None = error (skip but don't treat as end), list = success
            successful = [(s, p) for s, p in zip(batch_starts, pages) if p is not None]
            failed = [s for s, p in zip(batch_starts, pages) if p is None]

            if failed:
                print(f"  [warn] {len(failed)} page(s) failed and will be skipped: offsets {failed}")

            for _, page in successful:
                buffer.extend(page)

            # End-of-data detection.
            # A page returns 0 items when we've gone past the last record.
            # IMPORTANT: the Census API occasionally returns an empty item_list for
            # mid-dataset offsets under load — that must NOT be treated as end-of-data.
            # Guard: only declare end if the empty page's start offset is plausibly
            # near the real end (within 2 full batches of the known total), OR if we
            # have no total estimate and ALL successful pages came back empty.
            empty_starts = [s for s, p in successful if len(p) == 0]
            if empty_starts:
                if total:
                    # Trust the total; ignore empty pages that are clearly mid-dataset
                    if min(empty_starts) >= total - 2 * CONCURRENCY * PAGE_SIZE:
                        reached_end = True
                    else:
                        print(
                            f"  [warn] empty page(s) at offset(s) {empty_starts} "
                            f"but total={total:,} — treating as transient gap, continuing"
                        )
                else:
                    # No total available — require every successful page to be empty
                    if len(empty_starts) == len(successful):
                        reached_end = True

            # Always advance offset for successful (non-all-failed) batches
            if successful and not reached_end:
                offset += CONCURRENCY * PAGE_SIZE
                # Save offset after every successful batch so interruptions resume correctly
                set_meta(conn, "download_offset", str(offset))

            # Flush buffer to DB
            if len(buffer) >= WRITE_EVERY or reached_end:
                if buffer:
                    upsert_items(buffer, conn)
                    written += len(buffer)
                    buffer = []
                    total_in_db = item_count(conn)
                    print(f"  Written this run: {written:,}  |  Total in DB: {total_in_db:,}  |  Offset: {offset:,}")

            if reached_end:
                break
            # If ALL pages failed (not just some), pause before retrying
            if not successful:
                print(f"  All pages in batch failed — sleeping 30s before retry…")
                await asyncio.sleep(30)
                continue

    # Flush remainder
    if buffer:
        upsert_items(buffer, conn)
        written += len(buffer)

    if reached_end:
        set_meta(conn, "download_offset", "0")  # reset so next run starts fresh
        print("\nReached end of Census data — offset reset for next run.")

    conn.close()
    final = item_count(init_db(DB_PATH))
    print(f"\nDone. Written this run: {written:,}  |  Total in DB: {final:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max items to download this run (for testing)")
    parser.add_argument("--restart", action="store_true", help="Ignore saved offset and start from 0")
    args = parser.parse_args()
    asyncio.run(main(args.restart, args.limit))
