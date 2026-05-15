import os
import re
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands

from census.client import CensusClient
from census.models import CharacterSpells, SpellEntry

_TIER_ORDER = ["Apprentice", "Journeyman", "Adept", "Expert", "Master", "Grandmaster"]
_COL_SEP = "  "

# Matches trailing Roman numeral suffix, e.g. " VII" or " IV"
_ROMAN_SUFFIX = re.compile(
    r'\s+(?:XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X'
    r'|IX|VIII|VII|VI|V|IV|III|II|I)$',
    re.IGNORECASE,
)


def _base_name(name: str) -> str:
    return _ROMAN_SUFFIX.sub("", name.strip())


def _unique_highest(entries: list[SpellEntry]) -> list[SpellEntry]:
    """For each unique base spell name, keep only the highest-level entry."""
    best: dict[str, SpellEntry] = {}
    for e in entries:
        key = (_base_name(e.name), e.spell_type)
        if key not in best or e.level > best[key].level:
            best[key] = e
    return list(best.values())


def _build_table(data: CharacterSpells) -> str:
    entries = _unique_highest(data.entries)

    count: Counter[str] = Counter(e.tier for e in entries)
    all_tiers = [t for t in _TIER_ORDER if count[t]]

    tier_w  = max(len("Tier"),  max((len(t) for t in all_tiers), default=0))
    count_w = max(len("Count"), max((len(str(count[t])) for t in all_tiers), default=0))

    def _row(tier, n) -> str:
        return tier.ljust(tier_w) + _COL_SEP + str(n).rjust(count_w)

    sep = "─" * (tier_w + count_w + len(_COL_SEP))
    lines = [
        f"{data.character_name} — Spell Summary",
        "",
        _row("Tier", "Count"),
        sep,
    ]
    for tier in all_tiers:
        lines.append(_row(tier, count[tier]))

    lines += [sep, _row("Total", sum(count.values()))]

    return "\n".join(lines)


class SpellcheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.census = CensusClient(service_id=os.getenv("CENSUS_SERVICE_ID", "example"))
        self.world = os.getenv("EQ2_WORLD", "Varsoon")

    async def cog_unload(self) -> None:
        await self.census.close()

    @app_commands.command(name="spellcheck", description="Summarise a character's spell tiers")
    @app_commands.describe(name="Character first name (e.g. Sihtric)")
    async def spellcheck(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)

        data = await self.census.get_character_spells(name, self.world)
        if data is None:
            await interaction.followup.send(
                f"No character found for **{name}** on **{self.world}**.",
                ephemeral=True,
            )
            return
        if not data.entries:
            await interaction.followup.send(
                f"**{data.character_name}** was found but has no spells or combat arts on record.",
                ephemeral=True,
            )
            return

        table = _build_table(data)
        await interaction.followup.send(f"```\n{table}\n```")
