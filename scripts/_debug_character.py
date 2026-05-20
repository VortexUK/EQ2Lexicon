"""Dump raw Census character JSON so we can see the actual field structure."""
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Menludiir"
    world = os.getenv("EQ2_WORLD", "Varsoon")
    service_id = os.getenv("CENSUS_SERVICE_ID", "example")

    import aiohttp
    url = f"https://census.daybreakgames.com/s:{service_id}/json/get/eq2/character/"
    params = {
        "name.first": name,
        "locationdata.world": world,
        "c:resolve": "equipment(displayname,id,iconid,slot,tier)",
        "c:show": "name,type,equipmentslot_list,alternateadvancements",
        "c:limit": "1",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json(content_type=None)

    char_list = data.get("character_list", [])
    if not char_list:
        print("Character not found")
        return

    char = char_list[0]
    print(json.dumps(char, indent=2))


asyncio.run(main())
