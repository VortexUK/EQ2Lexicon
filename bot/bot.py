import logging

import discord
from discord.ext import commands

from census.config import DISCORD_SYNC_GUILD_IDS

_log = logging.getLogger(__name__)


class EQ2Bot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        from bot.cogs.items import ItemsCog
        from bot.cogs.guild import GuildCog
        from bot.cogs.spellcheck import SpellcheckCog
        from bot.cogs.aacheck import AaCheckCog
        from bot.cogs.fun import FunCog

        await self.add_cog(ItemsCog(self))
        await self.add_cog(GuildCog(self))
        await self.add_cog(SpellcheckCog(self))
        await self.add_cog(AaCheckCog(self))
        await self.add_cog(FunCog(self))
        for guild_id in DISCORD_SYNC_GUILD_IDS:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        await self.tree.sync()
        _log.info("Slash commands synced.")

    async def on_ready(self) -> None:
        assert self.user is not None
        _log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
