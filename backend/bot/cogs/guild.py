from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import resolve_guild_context
from backend.bot.messaging import send_plan
from backend.bot.render import build_guild_table, plan_code_block

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot


class GuildCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot

    @app_commands.command(name="guild", description="Show a member summary for an EverQuest 2 guild")
    @app_commands.describe(name="Guild name — optional when this server is linked via /lexicon link")
    async def guild(self, interaction: discord.Interaction, name: str | None = None) -> None:
        ctx = await resolve_guild_context(interaction.guild_id)
        target = name or ctx.guild_name
        if target is None:
            await interaction.response.send_message(
                "Give a guild name, or link this server to one with `/lexicon link`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)

        data = await self.bot.census.get_guild(target, ctx.world)
        if data is None or not data.members:
            await interaction.followup.send(
                f"No guild found for **{target}** on **{ctx.world}**.",
                ephemeral=True,
            )
            return

        table = build_guild_table(data)
        plan = plan_code_block(
            table,
            filename=f"{data.name.replace(' ', '_')}_guild.txt",
            file_header=f"**{data.name}** — {data.world} ({len(data.members)} members)",
        )
        await send_plan(interaction.followup, plan)
