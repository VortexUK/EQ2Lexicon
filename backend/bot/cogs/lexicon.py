"""/lexicon — link a Discord server to an EQ2 guild.

The registry these commands edit (users.db discord_guild_links) drives every
world-aware bot command's context (backend/bot/guild_context.py) and the
Phase 3 voice-attendance poller. Gated on Discord's own manage_guild
permission: default_permissions hides the group from regular members in the
UI, and interaction_check enforces it server-side (admins can loosen the UI
default per-integration; the check is the hard floor). Deliberately NO
site-officer verification — the Discord server's admin is the right trust
boundary for "what does this Discord server map to".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import FALLBACK_WORLD, resolve_guild_context
from backend.census.config import ALLOWED_SERVERS
from backend.server.db.discord_links import store as links_store

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot

_log = logging.getLogger(__name__)

_WORLD_CHOICES = [app_commands.Choice(name=w, value=w) for w in sorted(ALLOWED_SERVERS)]


class LexiconCog(commands.Cog):
    lexicon = app_commands.Group(
        name="lexicon",
        description="Link this Discord server to an EverQuest 2 guild",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Hard permission floor — default_permissions is only a UI default.

        interaction.permissions is resolved by Discord and delivered in the
        interaction payload — authoritative, no reliance on the bot's role
        cache (owner/Administrator implicitly pass)."""
        return interaction.permissions.manage_guild

    @lexicon.command(name="link", description="Link this Discord server to an EQ2 guild")
    @app_commands.describe(world="EQ2 server", guild="Guild name (validated against Census)")
    @app_commands.choices(world=_WORLD_CHOICES)
    async def link(self, interaction: discord.Interaction, world: app_commands.Choice[str], guild: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        data = await self.bot.census.get_guild(guild, world.value)
        if data is None or not data.members:
            await interaction.followup.send(
                f"No guild named **{guild}** found on **{world.value}** — check the spelling.",
                ephemeral=True,
            )
            return
        # Store the canonical Census casing so joins against attendance
        # sessions (which store canonical names too) match exactly.
        await links_store.upsert_link(str(interaction.guild_id), world.value, data.name, str(interaction.user.id))
        _log.info("[bot] linked discord guild %s -> %s / %s", interaction.guild_id, world.value, data.name)
        await interaction.followup.send(
            f"Linked this server to **{data.name}** on **{world.value}**. "
            f"Commands like `/guild` now default to it; set the raid voice channel with `/lexicon voice`.",
            ephemeral=True,
        )

    @lexicon.command(name="voice", description="Set (or clear) the raid voice channel for attendance")
    @app_commands.describe(channel="The raid voice channel — leave empty to turn voice tracking off")
    async def voice(self, interaction: discord.Interaction, channel: discord.VoiceChannel | None = None) -> None:
        updated = await links_store.set_voice_channel(str(interaction.guild_id), str(channel.id) if channel else None)
        if not updated:
            await interaction.response.send_message(
                "This server isn't linked to an EQ2 guild yet — run `/lexicon link` first.",
                ephemeral=True,
            )
            return
        msg = (
            f"Raid voice channel set to {channel.mention}. During live raid sessions the bot "
            f"records who's in it for the attendance tab."
            if channel
            else "Voice tracking turned off."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @lexicon.command(name="status", description="Show this server's EQ2 link")
    async def status(self, interaction: discord.Interaction) -> None:
        ctx = await resolve_guild_context(interaction.guild_id)
        if not ctx.linked:
            await interaction.response.send_message(
                f"Not linked — commands fall back to **{FALLBACK_WORLD}** with no default guild. "
                f"Run `/lexicon link` to set one.",
                ephemeral=True,
            )
            return
        voice = f"<#{ctx.voice_channel_id}>" if ctx.voice_channel_id else "not set (`/lexicon voice`)"
        lines = [
            f"Linked to **{ctx.guild_name}** on **{ctx.world}**.",
            f"Raid voice channel: {voice}",
        ]
        lines.append(await self._live_session_line(ctx))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _live_session_line(self, ctx) -> str:
        """Live-session probe — the /lexicon status verification aid for the
        voice poller. Purely informational; never fails the command."""
        try:
            import time

            from backend.server.db.attendance import store as attendance_store

            session = await attendance_store.find_live_session(ctx.world, ctx.guild_name, int(time.time()))
            return "Attendance session: **live now**" if session else "Attendance session: none live"
        except Exception:  # pragma: no cover — purely informational
            return ""

    @lexicon.command(name="unlink", description="Remove this server's EQ2 link")
    async def unlink(self, interaction: discord.Interaction) -> None:
        removed = await links_store.delete_link(str(interaction.guild_id))
        await interaction.response.send_message(
            "Link removed." if removed else "This server wasn't linked.",
            ephemeral=True,
        )
