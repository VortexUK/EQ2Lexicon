import discord
from discord.ext import commands


class EQ2Bot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        from bot.cogs.items import ItemsCog
        from bot.cogs.guild import GuildCog
        from bot.cogs.spellcheck import SpellcheckCog
        from bot.cogs.aacheck import AaCheckCog
        await self.add_cog(ItemsCog(self))
        await self.add_cog(GuildCog(self))
        await self.add_cog(SpellcheckCog(self))
        await self.add_cog(AaCheckCog(self))
        for guild_id in (648253204760625160, 955890381847928892, 1502314690041221260):
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        await self.tree.sync()
        print("[Bot] Slash commands synced.")

    async def on_ready(self) -> None:
        print(f"[Bot] Logged in as {self.user} (ID: {self.user.id})")
