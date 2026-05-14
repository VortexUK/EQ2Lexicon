import discord
from discord.ext import commands


class EQ2Bot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        from bot.cogs.items import ItemsCog
        await self.add_cog(ItemsCog(self))
        await self.tree.sync()
        print("[Bot] Slash commands synced.")

    async def on_ready(self) -> None:
        print(f"[Bot] Logged in as {self.user} (ID: {self.user.id})")
