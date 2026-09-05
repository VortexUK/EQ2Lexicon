import asyncio
import logging
from collections.abc import Coroutine

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.cogs.aacheck import AaCheckCog
from backend.bot.cogs.fun import FunCog
from backend.bot.cogs.guild import GuildCog
from backend.bot.cogs.items import ItemsCog
from backend.bot.cogs.lexicon import LexiconCog
from backend.bot.cogs.spellcheck import SpellcheckCog
from backend.bot.cogs.voice_attendance import VoiceAttendanceCog
from backend.census.client import CensusClient
from backend.census.config import DISCORD_SYNC_GUILD_IDS, SERVICE_ID

_log = logging.getLogger(__name__)


class EQ2Bot(commands.Bot):
    census: CensusClient

    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Privileged: needed so voice states resolve to Members for the
        # voice-attendance poller. MUST also be toggled in the dev portal
        # (Bot → Privileged Gateway Intents → SERVER MEMBERS INTENT) or
        # login fails — main.py catches that and keeps the web half alive.
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        # Background tasks the bot owns (not tasks.loop — those live in
        # their cogs and are cancelled by cog_unload). Tracked so close()
        # can cancel them: an untracked loop is exactly how the web side
        # once ended up with a reload that hung forever.
        self._bg_tasks: set[asyncio.Task] = set()

    def create_background_task(self, coro: Coroutine, *, name: str) -> asyncio.Task:
        """Spawn a tracked background task, cancelled on close()."""
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def setup_hook(self) -> None:
        from backend.core.logging_config import configure_logging

        configure_logging()
        # The bot shares users.db with the web half but starts concurrently
        # with it — the web lifespan owns init_db, so run it defensively
        # here too (idempotent CREATE IF NOT EXISTS + guarded migrations)
        # before any cog can touch the registry.
        from backend.server import db as users_db

        await asyncio.to_thread(users_db.init_db)

        self.census = CensusClient(service_id=SERVICE_ID)
        self.tree.error(self._on_app_command_error)

        await self.add_cog(ItemsCog(self))
        await self.add_cog(GuildCog(self))
        await self.add_cog(SpellcheckCog(self))
        await self.add_cog(AaCheckCog(self))
        await self.add_cog(FunCog(self))
        await self.add_cog(LexiconCog(self))
        await self.add_cog(VoiceAttendanceCog(self))
        for guild_id in DISCORD_SYNC_GUILD_IDS:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        await self.tree.sync()
        _log.info("Slash commands synced to %d guild(s)", len(DISCORD_SYNC_GUILD_IDS))

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """One place every slash-command failure lands: permission failures
        get a clear ephemeral, everything else is logged with the traceback
        and answered generically (cog-local handlers may already have sent a
        specific message — the extra generic ephemeral is accepted)."""
        original = getattr(error, "original", error)
        cmd = interaction.command.qualified_name if interaction.command else "<unknown>"
        if isinstance(error, app_commands.CheckFailure):
            msg = "You don't have permission to use this command here."
        elif isinstance(error, app_commands.TransformerError) and error.type is discord.AppCommandOptionType.channel:
            # A picked channel that won't resolve = the bot can't see it
            # (not in its cache without View Channel). Seen live 2026-09-05
            # with a role-restricted raid voice channel.
            msg = (
                "I can't access that channel — give the bot **View Channel** permission "
                "on it (channel → Permissions), then try again."
            )
        else:
            _log.error("[bot] /%s failed", cmd, exc_info=original)
            msg = "Something went wrong running that command — it has been logged."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass  # interaction expired / already answered — nothing useful left to do

    async def close(self) -> None:
        for task in self._bg_tasks:
            task.cancel()
        try:
            if self._bg_tasks:
                await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            # A failed login closes the bot BEFORE setup_hook ran — census
            # may not exist yet (seen live 2026-09-05 with a bad token).
            if (census := getattr(self, "census", None)) is not None:
                await census.close()
        finally:
            # discord.py >= 2.0 removes cogs during close(), firing each
            # cog_unload — which is where tasks.loop pollers cancel.
            await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        _log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        # Gateway presence list — a server where slash commands work but
        # which is MISSING here was invited without the `bot` scope: no
        # channel cache, no voice states, channel options won't resolve.
        _log.info(
            "Gateway presence in %d guild(s): %s",
            len(self.guilds),
            ", ".join(f"{g.name} ({g.id})" for g in self.guilds) or "<none>",
        )
