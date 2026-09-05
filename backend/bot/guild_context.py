"""Per-Discord-guild context resolution for bot commands.

The discord_guild_links registry (users.db, configured in Discord via the
/lexicon command group) maps a Discord server to an EQ2 (world, guild_name)
pair. Every world-aware command resolves its context here instead of the
old env-WORLD pin; unlinked servers (and DMs) fall back to
``FALLBACK_WORLD`` with no default guild.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from backend.server.db.discord_links import store as links_store

_log = logging.getLogger(__name__)

#: Where unlinked Discord servers point. Hardcoded by design (2026-09-04):
#: Wuoshi is the only server the userbase cares about right now — revisit
#: when that changes rather than growing another env var.
FALLBACK_WORLD = "Wuoshi"


@dataclass(frozen=True)
class GuildContext:
    world: str
    guild_name: str | None
    voice_channel_id: str | None
    linked: bool


_FALLBACK = GuildContext(world=FALLBACK_WORLD, guild_name=None, voice_channel_id=None, linked=False)


async def resolve_guild_context(discord_guild_id: int | None) -> GuildContext:
    """The invoking Discord guild's EQ2 context. ``None`` (DMs) and unknown
    guilds resolve to the fallback. A missing table (bot racing the web
    lifespan's init_db on a brand-new deploy) degrades to the fallback too —
    a slash command must never crash on registry trouble."""
    if discord_guild_id is None:
        return _FALLBACK
    try:
        link = await links_store.get_link(str(discord_guild_id))
    except sqlite3.OperationalError as exc:
        _log.warning("[bot] guild-link lookup failed (%s) — using fallback world", exc)
        return _FALLBACK
    if link is None:
        return _FALLBACK
    return GuildContext(
        world=link["world"],
        guild_name=link["guild_name"],
        voice_channel_id=link["voice_channel_id"],
        linked=True,
    )
