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


async def is_guild_officer(discord_id: str, ctx: GuildContext) -> bool:
    """Site-officer check for bot commands: does this Discord user hold an
    approved claim on an officer-ranked character in the linked guild?

    Reuses the web layer's ``_officer_chars`` — which reads the request
    contextvar ``current_world()`` — by pinning the active server to the
    link's world for the duration of the call (the bot has no request).
    Census/roster trouble degrades to False (deny) rather than raising into
    the command."""
    if not ctx.linked or ctx.guild_name is None:
        return False
    from backend.server import server_context
    from backend.server.api.guild import _officer_chars

    srv = server_context.server_for_world(ctx.world)
    token = server_context.set_active_server(srv) if srv is not None else None
    try:
        return bool(await _officer_chars(discord_id, ctx.guild_name))
    except Exception:
        _log.warning("[bot] officer check failed for %s in %s", discord_id, ctx.guild_name, exc_info=True)
        return False
    finally:
        if token is not None:
            server_context.reset_active_server(token)


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
