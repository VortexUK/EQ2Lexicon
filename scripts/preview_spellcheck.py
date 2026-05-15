#!/usr/bin/env python3
"""
Print a character spell summary to the console without needing Discord.

Usage:
    python scripts/preview_spellcheck.py Sihtric
    python scripts/preview_spellcheck.py Sihtric --debug
    python scripts/preview_spellcheck.py Sihtric --details
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from census.client import CensusClient
from bot.cogs.spellcheck import _TIER_ORDER, _build_details, _build_table, _unique_highest


async def main() -> None:
    args = sys.argv[1:]
    debug   = "--debug"   in args
    details = "--details" in args
    name_parts = [a for a in args if not a.startswith("--")]

    if not name_parts:
        print("Usage: python scripts/preview_spellcheck.py <character name> [--debug]")
        sys.exit(1)

    name = " ".join(name_parts)
    service_id = os.getenv("CENSUS_SERVICE_ID", "example")
    world = os.getenv("EQ2_WORLD", "Varsoon")

    client = CensusClient(service_id=service_id)
    try:
        data = await client.get_character_spells(name, world)
        if data is None:
            print("Character not found.")
            sys.exit(1)
        if not data.entries:
            print(f"{data.character_name} has no spells or arts on record.")
            sys.exit(1)

        if debug:
            unique = _unique_highest(data.entries)
            tier_order = {t: i for i, t in enumerate(_TIER_ORDER)}
            unique.sort(key=lambda e: (tier_order.get(e.tier, 99), e.spell_type, e.name))
            col = 40
            print(f"{'Spell':<{col}}  {'Type':<7}  {'Lvl':>3}  Tier")
            print("─" * (col + 25))
            for e in unique:
                print(f"{e.name:<{col}}  {e.spell_type:<7}  {e.level:>3}  {e.tier}")
            print(f"\n{len(unique)} unique spells/arts counted\n")

        if details:
            print(_build_details(data))
        else:
            print(_build_table(data))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
