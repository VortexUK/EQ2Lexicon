#!/usr/bin/env python3
"""
Render an item tooltip to preview.png without needing Discord.

Usage:
    python scripts/preview_item.py "Faded Black Hood"
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from backend.census.config import SERVICE_ID, WORLD

load_dotenv()

from backend.census.client import CensusClient
from backend.image.tooltip import render_tooltip


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/preview_item.py <item name>")
        sys.exit(1)

    name = " ".join(sys.argv[1:])
    service_id = SERVICE_ID

    client = CensusClient(service_id=service_id)
    try:
        print(f"Looking up: {name!r}")
        item = await client.get_item(name)
        if item is None:
            print("Item not found.")
            sys.exit(1)

        print(f"Found: {item.name!r}  quality={item.quality}  stats={len(item.stats)}  effects={len(item.effects)}")

        img = render_tooltip(item)
        out = Path("preview.png")
        img.save(out)
        print(f"Saved: {out.resolve()}")

        # Open the image with the default viewer
        if sys.platform == "win32":
            os.startfile(str(out))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(out)], check=False)
        else:
            subprocess.run(["xdg-open", str(out)], check=False)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
