#!/usr/bin/env python3
"""
Dump the raw Census API JSON for an item — useful for checking field names.

Usage:
    python scripts/inspect_item.py "Faded Black Hood"
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from census.client import CensusClient


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_item.py <item name>")
        sys.exit(1)

    name = " ".join(sys.argv[1:])
    service_id = os.getenv("CENSUS_SERVICE_ID", "tovortex")

    client = CensusClient(service_id=service_id)
    try:
        data = await client.get_raw_item(name)
        if data:
            print(json.dumps(data, indent=2, default=str))
        else:
            print("No data returned.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
