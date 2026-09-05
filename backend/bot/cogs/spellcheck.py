from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import resolve_guild_context
from backend.bot.messaging import send_plan
from backend.bot.render import build_spell_details, build_spell_summary, plan_code_block

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot


class SpellcheckCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot

    @app_commands.command(name="spellcheck", description="Summarise a character's spell tiers")
    @app_commands.describe(
        name="Character first name (e.g. Sihtric)",
        details="Show every spell ordered by tier and level instead of just the summary",
    )
    async def spellcheck(
        self,
        interaction: discord.Interaction,
        name: str,
        details: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        ctx = await resolve_guild_context(interaction.guild_id)

        data = await self.bot.census.get_character_spells(name, ctx.world)
        if data is None:
            await interaction.followup.send(
                f"No character found for **{name}** on **{ctx.world}**.",
                ephemeral=True,
            )
            return
        if not data.entries:
            await interaction.followup.send(
                f"**{data.character_name}** was found but has no spells or combat arts on record.",
                ephemeral=True,
            )
            return

        if details:
            plan = plan_code_block(
                build_spell_details(data),
                filename=f"{data.character_name}_spells.txt",
            )
            await send_plan(interaction.followup, plan)
        else:
            await interaction.followup.send(f"```\n{build_spell_summary(data)}\n```")
