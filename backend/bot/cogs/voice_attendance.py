"""Voice-attendance poller — the Phase 3 cross-check.

Every ~2 minutes, for each Discord guild with a raid voice channel
configured (/lexicon voice), check whether the linked EQ2 guild has a LIVE
attendance session (one the parser's uploads would merge into right now).
If so, snapshot who's connected to the channel and record kind='voice'
observations (character_name carries the Discord user id — reserved by the
attendance schema). The site's per-player rollup then shows 🎧 and flags
AWOL-but-in-voice players.

Idle cost: one registry query per tick + one indexed session probe per
configured guild — no Discord API calls and no writes unless a session is
live. Requires the privileged members intent (dev-portal toggle) so voice
states resolve to Members.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from backend.server.db.attendance import store as attendance_store
from backend.server.db.discord_links import store as links_store

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot

_log = logging.getLogger(__name__)

POLL_INTERVAL_S = 120


class VoiceAttendanceCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot
        # Warn once per Discord guild when its channel is unusable (deleted,
        # bot kicked, wrong type); reset when it works again.
        self._channel_warned: set[str] = set()

    async def cog_load(self) -> None:
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    @tasks.loop(seconds=POLL_INTERVAL_S)
    async def poll(self) -> None:
        # An exception escaping a tasks.loop body kills the loop for good —
        # nothing in a tick is worth that.
        try:
            await self._tick()
        except Exception:
            _log.exception("[voice-attendance] tick failed")

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _tick(self) -> None:
        links = await links_store.list_voice_links()
        now = int(time.time())
        for link in links:
            try:
                await self._poll_link(link, now)
            except Exception:
                _log.exception("[voice-attendance] link %s failed", link["discord_guild_id"])

    async def _poll_link(self, link: dict, now: int) -> None:
        session = await attendance_store.find_live_session(link["world"], link["guild_name"], now)
        if session is None:
            return  # idle path ends here

        guild = self.bot.get_guild(int(link["discord_guild_id"]))
        channel = guild.get_channel(int(link["voice_channel_id"])) if guild else None
        if not isinstance(channel, discord.VoiceChannel):
            if link["discord_guild_id"] not in self._channel_warned:
                self._channel_warned.add(link["discord_guild_id"])
                _log.warning(
                    "[voice-attendance] configured channel %s in discord guild %s is missing or not a "
                    "voice channel — skipping until it resolves",
                    link["voice_channel_id"],
                    link["discord_guild_id"],
                )
            return
        self._channel_warned.discard(link["discord_guild_id"])

        ids = [str(m.id) for m in channel.members if not m.bot]
        if ids:
            await attendance_store.record_voice(session["id"], ids, now)
            _log.debug("[voice-attendance] session %s: %d in voice", session["id"], len(ids))
