#!/usr/bin/env python3
"""
Print a guild member table to the console without needing Discord.

Usage:
    python scripts/preview_guild.py "Exordium"
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from census.client import CensusClient
from bot.cogs.guild import _build_table


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/preview_guild.py <guild name>")
        sys.exit(1)

    name = " ".join(sys.argv[1:])
    service_id = os.getenv("CENSUS_SERVICE_ID", "example")
    world = os.getenv("EQ2_WORLD", "Varsoon")

    client = CensusClient(service_id=service_id)
    try:
        data = await client.get_guild(name, world)
        if data is None:
            print("Guild not found.")
            sys.exit(1)
        if not data.members:
            print(f"Guild '{data.name}' found but no members had resolved data.")
            sys.exit(1)
        print(_build_table(data))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
