#!/usr/bin/env python3
"""
Fetch a character's AA data and render the chosen tree locally.

Usage:
    python scripts/preview_aacheck.py Menludiir              # list available trees
    python scripts/preview_aacheck.py Menludiir Templar      # render by name
    python scripts/preview_aacheck.py Menludiir 25           # render by ID
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from backend.census.config import SERVICE_ID, WORLD

load_dotenv()

from backend.census.client import CensusClient
from backend.image.aa_tree import render_tree

_TREES_DIR = Path(__file__).resolve().parent.parent / "data" / "AAs" / "trees"


def _load_tree_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for path in _TREES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            aa_list = data.get("alternateadvancement_list") or []
            if aa_list:
                names[int(path.stem)] = aa_list[0].get("name", path.stem)
        except Exception:
            pass
    return names


def _resolve_tree(query: str, available_ids: set[int], tree_names: dict[int, str]) -> list[int]:
    """Return matching tree IDs from available_ids for a name or numeric ID."""
    if query.isdigit():
        tid = int(query)
        return [tid] if tid in available_ids else []
    q = query.lower()
    return [tid for tid in available_ids if q in tree_names.get(tid, "").lower()]


async def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "Menludiir"
    world = WORLD

    tree_names = _load_tree_names()

    client = CensusClient(service_id=SERVICE_ID)
    try:
        char_aas = await client.get_character_aas(name, world)
    finally:
        await client.close()

    if char_aas is None:
        print(f"Character not found: {name} on {world}")
        return

    print(f"Character : {char_aas.character_name}")
    available = char_aas.tree_ids
    for tid in sorted(available):
        print(f"  [{tid}]  {tree_names.get(tid, '?')}")

    if len(sys.argv) < 3:
        print("\nPass a tree name or ID as the second argument to render it.")
        return

    matches = _resolve_tree(sys.argv[2], available, tree_names)
    if not matches:
        print(f"No matching tree found for '{sys.argv[2]}'.")
        return
    if len(matches) > 1:
        print(f"Ambiguous — multiple matches:")
        for tid in matches:
            print(f"  [{tid}]  {tree_names.get(tid, '?')}")
        return

    tree_id = matches[0]
    aa_data = char_aas.for_tree(tree_id)
    print(f"\nTree      : {tree_names.get(tree_id, tree_id)}  (id={tree_id})")
    print(f"Nodes     : {len(aa_data)}  total points : {sum(aa_data.values())}")

    img, tree_type = render_tree(tree_id, aa_data)
    out = Path("preview_aacheck.png")
    img.save(out)
    print(f"Saved     : {out.resolve()}  (type={tree_type})")

    if sys.platform == "win32":
        os.startfile(str(out))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(out)], check=False)
    else:
        subprocess.run(["xdg-open", str(out)], check=False)


asyncio.run(main())
