import os

import discord
from discord import app_commands
from discord.ext import commands

from census.client import CensusClient
from census.models import GuildData, GuildMember


def _class_summary(members: list[GuildMember]) -> str:
    from collections import Counter
    counts: Counter[str] = Counter()
    for m in members:
        if m.cls:
            counts[m.cls] += 1
    if not counts:
        return "—"
    return ", ".join(f"{cls} ×{n}" for cls, n in sorted(counts.items(), key=lambda x: -x[1]))


def _level_band(level: int | None) -> str:
    if level is None:
        return "Unknown"
    if level >= 120:
        return "120+"
    if level >= 110:
        return "110–119"
    if level >= 100:
        return "100–109"
    if level >= 90:
        return "90–99"
    return f"<90"


def _build_embed(data: GuildData) -> discord.Embed:
    from collections import Counter

    members = data.members
    total = len(members)

    # Level distribution
    band_counts: Counter[str] = Counter(_level_band(m.level) for m in members)
    level_lines = "\n".join(
        f"**{band}**: {n}"
        for band, n in sorted(band_counts.items(), key=lambda x: x[0], reverse=True)
    ) or "—"

    # Class distribution (top 10)
    cls_counts: Counter[str] = Counter(m.cls for m in members if m.cls)
    class_lines = "\n".join(
        f"**{cls}**: {n}"
        for cls, n in cls_counts.most_common(10)
    ) or "—"

    # AA stats
    aa_values = [m.aa_level for m in members if m.aa_level is not None]
    if aa_values:
        avg_aa = sum(aa_values) / len(aa_values)
        max_aa = max(aa_values)
        aa_text = f"Avg {avg_aa:.0f} · Max {max_aa}"
    else:
        aa_text = "—"

    # Deity distribution
    deity_counts: Counter[str] = Counter(m.deity for m in members if m.deity)
    deity_lines = "\n".join(
        f"**{deity}**: {n}"
        for deity, n in deity_counts.most_common(5)
    ) or "—"

    embed = discord.Embed(
        title=f"{data.name} — {data.world}",
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Members (with data)", value=str(total), inline=True)
    embed.add_field(name="Alternate Advancements", value=aa_text, inline=True)
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(name="Level Distribution", value=level_lines, inline=True)
    embed.add_field(name="Top Classes", value=class_lines, inline=True)
    embed.add_field(name="Top Deities", value=deity_lines, inline=True)
    return embed


class GuildCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.census = CensusClient(service_id=os.getenv("CENSUS_SERVICE_ID", "example"))
        self.world = os.getenv("EQ2_WORLD", "Varsoon")

    async def cog_unload(self) -> None:
        await self.census.close()

    @app_commands.command(name="guild", description="Show a summary of an EverQuest 2 guild")
    @app_commands.describe(name="Guild name (e.g. Exordium)")
    async def guild(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)

        data = await self.census.get_guild(name, self.world)
        if data is None or not data.members:
            await interaction.followup.send(
                f"No guild found for **{name}** on **{self.world}**.",
                ephemeral=True,
            )
            return

        embed = _build_embed(data)
        await interaction.followup.send(embed=embed)
