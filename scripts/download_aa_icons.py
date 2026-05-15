#!/usr/bin/env python3
"""
Download AA node icon PNGs from eq2wire for all tree files.

Scans every file in data/AAs/trees/, collects unique icon IDs, then
fetches https://u.eq2wire.com/images/aa/{id}.png into data/AAs/icons/.
Files that already exist are skipped.

Usage:
    python scripts/download_aa_icons.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp

TREES_DIR = Path(__file__).resolve().parent.parent / "data" / "AAs" / "trees"
ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "AAs" / "icons"
ICON_BASE = "https://u.eq2wire.com/images/aa/{id}.png"

# Limit concurrent downloads to avoid hammering the server
_CONCURRENCY = 10


def _collect_icon_ids() -> set[int]:
    ids: set[int] = set()
    for path in TREES_DIR.glob("*.json"):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        for tree in data.get("alternateadvancement_list") or []:
            for node in tree.get("alternateadvancementnode_list") or []:
                icon = node.get("icon")
                if isinstance(icon, dict) and icon.get("id") is not None:
                    icon_id = int(icon["id"])
                    if icon_id > 0:
                        ids.add(icon_id)
    return ids


async def _download_icon(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    icon_id: int,
) -> str:
    dest = ICONS_DIR / f"{icon_id}.png"
    if dest.exists():
        return "skip"

    url = ICON_BASE.format(id=icon_id)
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 404:
                    print(f"  [404]  {icon_id}.png")
                    return "missing"
                if resp.status != 200:
                    print(f"  [error] {icon_id}.png: HTTP {resp.status}")
                    return "error"
                data = await resp.read()
        except Exception as exc:
            print(f"  [error] {icon_id}.png: {type(exc).__name__}: {exc}")
            return "error"

    dest.write_bytes(data)
    print(f"  [ok]   {icon_id}.png")
    return "ok"


async def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    print("Scanning tree files for icon IDs...")
    icon_ids = _collect_icon_ids()
    print(f"Found {len(icon_ids)} unique icon IDs\n")

    sem = asyncio.Semaphore(_CONCURRENCY)
    counts = {"ok": 0, "skip": 0, "missing": 0, "error": 0}

    async with aiohttp.ClientSession() as session:
        tasks = [_download_icon(session, sem, id_) for id_ in sorted(icon_ids)]
        results = await asyncio.gather(*tasks)

    for r in results:
        counts[r] += 1

    print(
        f"\nDone.  Downloaded: {counts['ok']}  "
        f"Skipped: {counts['skip']}  "
        f"Missing: {counts['missing']}  "
        f"Errors: {counts['error']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
