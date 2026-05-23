import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from census.client import CensusClient
from census.config import SERVICE_ID
from image.tooltip import render_tooltip

_log = logging.getLogger(__name__)


class ItemsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.census = CensusClient(service_id=SERVICE_ID)

    async def cog_unload(self) -> None:
        await self.census.close()

    @app_commands.command(name="item", description="Look up an EverQuest 2 item by name")
    @app_commands.describe(name="Exact item display name (e.g. Faded Black Hood)")
    async def item(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)

        _log.debug("item query=%r params=%s", name, self.census._build_params(name))
        item_data = await self.census.get_item(name)
        _log.debug("item result=%s", "found: " + item_data.name if item_data else "not found")
        if item_data is None:
            await interaction.followup.send(
                f"No item found for **{name}**. Check the spelling or try a more specific name.",
                ephemeral=True,
            )
            return

        try:
            img = render_tooltip(item_data)
        except Exception as exc:
            await interaction.followup.send(f"Failed to render tooltip: {exc}", ephemeral=True)
            raise

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        content = f"```\n{item_data.game_link}\n```" if item_data.game_link else None
        await interaction.followup.send(content=content, file=discord.File(buf, filename="item.png"))  # type: ignore[arg-type]
