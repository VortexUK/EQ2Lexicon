"""Parse poster — new raid boss fights land in a configured Discord channel.

Every ~minute, for each Discord guild with a parses channel set
(``/lexicon parses``), pick up boss fights uploaded for the linked EQ2
guild since the per-link watermark, mirror-group them (five uploaders
still mean ONE post) and send a clean embed: outcome + duration, raid-wide
DPS/HPS, top-5 DPS and HPS side by side, linked to the parse page.

Dedup model: a fight is posted only once its uploads have had ``SETTLE_S``
to arrive, and only when its EARLIEST upload sits past the watermark — a
late mirror attaching to an already-posted fight regroups with uploads from
before the watermark (the ``LOOKBACK_S`` requery window exists for exactly
that) and is skipped. The watermark then advances to now-SETTLE_S, so a
restart never re-posts either.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from backend.bot.render import ParsePost, build_parse_post
from backend.server.db.discord_links import store as links_store
from backend.server.parses.boss import is_boss
from backend.server.parses.db import store as parses_db

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot

_log = logging.getLogger(__name__)

POLL_INTERVAL_S = 60
#: Grace for mirrors + the combatant/census backfill before a fight posts.
SETTLE_S = 180
#: Requery window behind the watermark so a straggler upload regroups with
#: its already-posted fight instead of standing alone as a "new" one.
LOOKBACK_S = 1800
#: Raid bucket floor (matches the mirror-grouping's raid threshold) — group
#: content stays out of the channel.
MIN_PLAYERS = 7
#: Flood guard per link per tick (a normal raid night never gets near it).
MAX_POSTS_PER_TICK = 10
#: Never post fights older than this, even after downtime.
MAX_AGE_S = 24 * 3600


def site_url_for(world: str, encounter_id: int) -> str | None:
    """Parse-page link from the deployment's parent domain (unset in dev →
    embeds simply carry no link)."""
    domain = os.getenv("SESSION_COOKIE_DOMAIN", "").lstrip(".")
    if not domain:
        return None
    return f"https://{world.lower()}.{domain}/parse/{encounter_id}"


def collect_new_fights(
    world: str, guild_name: str, posted_until: int, until: int, *, min_players: int = MIN_PLAYERS
) -> list[tuple[dict, list[dict]]]:
    """SYNC (runs in a thread): the boss fights to post, oldest first, as
    ``(fight, ally_players)`` pairs. A fight qualifies when its earliest
    upload ingested in ``(posted_until, until]`` and it is raid-sized."""
    if not parses_db.path.exists():
        return []
    # API-layer grouping helpers, imported locally like cleanup.py does —
    # a module-level import would invert the api→db layering.
    from backend.server.api.parses.list import (  # noqa: PLC0415
        _PLAYER_COUNT_SQL,
        _ensure_classified,
        _group_into_fights,
    )

    sql = (
        f"SELECT e.*, ({_PLAYER_COUNT_SQL}) AS player_count FROM encounters e "
        "WHERE e.world = ? AND e.guild_name = ? COLLATE NOCASE AND e.hidden_at IS NULL "
        "AND e.ingested_at > ? AND e.ingested_at <= ?"
    )
    conn = parses_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in conn.execute(sql, (world, guild_name, posted_until - LOOKBACK_S, until)).fetchall()
            if is_boss(r["title"])
        ]
        for r in rows:
            if _ensure_classified(conn, r["id"], r.get("zone")):
                refreshed = conn.execute(
                    "SELECT COUNT(*) FROM combatants WHERE encounter_id = ? AND is_player = 1", (r["id"],)
                ).fetchone()
                r["player_count"] = int(refreshed[0])

        out: list[tuple[dict, list[dict]]] = []
        for g in _group_into_fights(rows, conn):
            first_upload = min(u["ingested_at"] for u in g["uploads"])
            if first_upload <= posted_until or (g.get("player_count") or 0) < min_players:
                continue
            combatants = parses_db.get_combatants_for_encounter(conn, g["id"])
            players = [c for c in combatants if c.get("ally") and c.get("is_player")]
            out.append((g, players))
        out.sort(key=lambda pair: pair[0]["started_at"])
        return out
    finally:
        conn.close()


def _to_embed(post: ParsePost, started_at: int) -> discord.Embed:
    embed = discord.Embed(title=post.title, description=post.description, color=post.color, url=post.url)
    for name, value, inline in post.fields:
        embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text=post.footer)
    embed.timestamp = datetime.fromtimestamp(started_at, tz=UTC)
    return embed


class ParsePostsCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot
        # Warn once per Discord guild when its channel is unusable; reset
        # when it works again (mirrors the voice poller).
        self._channel_warned: set[str] = set()

    async def cog_load(self) -> None:
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    @tasks.loop(seconds=POLL_INTERVAL_S)
    async def poll(self) -> None:
        # An exception escaping a tasks.loop body kills the loop for good.
        try:
            await self._tick()
        except Exception:
            _log.exception("[parse-posts] tick failed")

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _tick(self) -> None:
        links = await links_store.list_parse_links()
        if not links:
            return
        until = int(time.time()) - SETTLE_S
        for link in links:
            try:
                await self._poll_link(link, until)
            except Exception:
                _log.exception("[parse-posts] link %s failed", link["discord_guild_id"])

    async def _poll_link(self, link: dict, until: int) -> None:
        posted_until = max(int(link.get("parses_posted_at") or 0), until - MAX_AGE_S)
        if until <= posted_until:
            return

        guild = self.bot.get_guild(int(link["discord_guild_id"]))
        channel = guild.get_channel(int(link["parses_channel_id"])) if guild else None
        if not isinstance(channel, discord.TextChannel):
            if link["discord_guild_id"] not in self._channel_warned:
                self._channel_warned.add(link["discord_guild_id"])
                _log.warning(
                    "[parse-posts] configured channel %s in discord guild %s is missing or not a "
                    "text channel — skipping until it resolves",
                    link["parses_channel_id"],
                    link["discord_guild_id"],
                )
            return  # watermark holds — fights post once the channel is back
        self._channel_warned.discard(link["discord_guild_id"])

        fights = await asyncio.to_thread(collect_new_fights, link["world"], link["guild_name"], posted_until, until)
        if len(fights) > MAX_POSTS_PER_TICK:
            _log.warning(
                "[parse-posts] %s: %d fights pending, posting newest %d (flood guard)",
                link["discord_guild_id"],
                len(fights),
                MAX_POSTS_PER_TICK,
            )
            fights = fights[-MAX_POSTS_PER_TICK:]
        for fight, players in fights:
            post = build_parse_post(fight, players, site_url_for(link["world"], fight["id"]))
            await channel.send(embed=_to_embed(post, fight["started_at"]))
        if fights:
            _log.info("[parse-posts] posted %d fight(s) to discord guild %s", len(fights), link["discord_guild_id"])
        await links_store.set_parses_posted_at(link["discord_guild_id"], until)
