#!/usr/bin/env python3
"""
Download AA tree JSON files from the Census API for all adventurer subclasses.

Reads data/AAs/adventurer.json to discover tree IDs, then fetches each one
from the Census API and saves it to data/AAs/trees/{id}.json.  Files that
already exist are skipped, so the script is safe to re-run after interruption.

Also writes data/AAs/index.json mapping class name -> list of tree IDs.

Usage:
    python scripts/download_aa_trees.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from backend.census.config import SERVICE_ID, WORLD

load_dotenv()

import aiohttp

from backend.census.client import BASE_URL

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "AAs"
TREES_DIR = DATA_DIR / "trees"
ADVENTURER_FILE = DATA_DIR / "adventurer.json"
INDEX_FILE = DATA_DIR / "index.json"


def _load_adventurer() -> list[dict]:
    with ADVENTURER_FILE.open() as f:
        data = json.load(f)
    return data["constants_list"][0]["adventureclass_list"]


def _build_index(class_list: list[dict]) -> dict[str, list[int]]:
    """Map each subclass name -> sorted list of unique tree IDs."""
    index: dict[str, list[int]] = {}
    for cls in class_list:
        if cls.get("issubclass") != "true":
            continue
        tree_ids = [t["id"] for t in cls.get("alternateadvancementtree_list", [])]
        index[cls["name"]] = sorted(set(tree_ids))
    return index


def _all_tree_ids(index: dict[str, list[int]]) -> list[int]:
    ids: set[int] = set()
    for tree_ids in index.values():
        ids.update(tree_ids)
    return sorted(ids)


async def _download_tree(
    session: aiohttp.ClientSession,
    service_id: str,
    tree_id: int,
) -> bool:
    """Fetch one tree and save it. Returns True if downloaded, False if skipped."""
    dest = TREES_DIR / f"{tree_id}.json"
    if dest.exists():
        # Skip only if the file contains real data (not a rate-limit error response)
        try:
            content = json.loads(dest.read_text(encoding="utf-8"))
            if "error" not in content:
                print(f"  [skip] {tree_id}.json already exists")
                return False
            print(f"  [retry] {tree_id}.json was an error response, re-fetching")
        except Exception:
            pass  # Corrupt file — re-fetch

    url = f"{BASE_URL}/s:{service_id}/json/get/eq2/alternateadvancement/{tree_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                print(f"  [error] tree {tree_id}: HTTP {resp.status}")
                return False
            data = await resp.json(content_type=None)
    except Exception as exc:
        print(f"  [error] tree {tree_id}: {type(exc).__name__}: {exc}")
        return False

    if "error" in data:
        print(f"  [flood] tree {tree_id}: rate limited — waiting 30s...")
        await asyncio.sleep(30)
        return False

    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  [ok]   {tree_id}.json")
    return True


async def main() -> None:
    service_id = SERVICE_ID

    TREES_DIR.mkdir(parents=True, exist_ok=True)

    class_list = _load_adventurer()
    index = _build_index(class_list)
    tree_ids = _all_tree_ids(index)

    # Write index
    INDEX_FILE.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"Written {INDEX_FILE.name} ({len(index)} classes, {len(tree_ids)} unique trees)")

    downloaded = skipped = errors = 0
    async with aiohttp.ClientSession() as session:
        for tree_id in tree_ids:
            result = await _download_tree(session, service_id, tree_id)
            if result is True:
                downloaded += 1
            elif result is False:
                dest = TREES_DIR / f"{tree_id}.json"
                if dest.exists():
                    skipped += 1
                else:
                    errors += 1

    print(f"\nDone. Downloaded: {downloaded}  Skipped: {skipped}  Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
