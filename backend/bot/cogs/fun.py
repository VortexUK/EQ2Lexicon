from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import resolve_guild_context
from backend.census.config import LAUNCH_DT_ISO
from backend.server.db.servers import store as servers_db
from backend.server.xpac_rollover import parse_dt

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------
_DATA = Path(__file__).resolve().parent.parent.parent.parent / "data"
_INSULTS_PATH = _DATA / "insult_creator.json"
_TIME_METRICS_PATH = _DATA / "time_metrics.json"

# ---------------------------------------------------------------------------
# Launch target — the servers registry is the source of truth (per-guild
# world via /lexicon link): the server's own launch while it hasn't opened
# yet, then whichever expansion the admins have scheduled (next_xpac_dt,
# the same instant the countdown banner and auto-rollover use). The env
# LAUNCH_DT survives only as a fallback for a missing registry row.
# ---------------------------------------------------------------------------
LAUNCH_DT: datetime | None
try:
    LAUNCH_DT = datetime.fromisoformat(LAUNCH_DT_ISO.replace("Z", "+00:00")) if LAUNCH_DT_ISO else None
except ValueError:
    LAUNCH_DT = None

#: Expansion short code (as stored in servers.next_xpac) → announcement name.
_XPAC_FULL_NAMES: dict[str, str] = {
    "DoF": "Desert of Flames",
    "KoS": "Kingdom of Sky",
    "EoF": "Echoes of Faydwer",
    "RoK": "Rise of Kunark",
    "TSO": "The Shadow Odyssey",
    "SF": "Sentinel's Fate",
    "DoV": "Destiny of Velious",
    "AoD": "Age of Discovery",
    "CoE": "Chains of Eternity",
    "ToV": "Tears of Veeshan",
    "AoM": "Altar of Malice",
    "ToT": "Terrors of Thalumbra",
    "KA": "Kunark Ascending",
    "PoP": "Planes of Prophecy",
}
_XPAC_BY_LOWER = {k.lower(): v for k, v in _XPAC_FULL_NAMES.items()}


def xpac_full_name(code: str) -> str:
    """Announcement name for a short code; unknown codes pass through."""
    return _XPAC_BY_LOWER.get(code.lower(), code)


def next_launch_target(row: dict | None, now: datetime) -> tuple[str, datetime] | None:
    """(label, instant) of the next launch on this server: the server itself
    while it hasn't opened yet, else the scheduled expansion. None = nothing
    on the calendar. A missing registry row falls back to the env date."""
    if row is None:
        return ("The server", LAUNCH_DT) if LAUNCH_DT else None
    launch = parse_dt(row.get("launch_dt"))
    if launch is not None and launch > now:
        return ("The server", launch)
    xpac_dt = parse_dt(row.get("next_xpac_dt"))
    if row.get("next_xpac") and xpac_dt is not None:
        return (xpac_full_name(row["next_xpac"]), xpac_dt)
    return None


def format_launch_dt(dt: datetime) -> str:
    """'14 September 2026, 17:00 UTC' — no %-d (it breaks on Windows)."""
    dt = dt.astimezone(UTC)
    return f"{dt.day} {dt:%B %Y}, {dt:%H:%M} UTC"


# ---------------------------------------------------------------------------
# Owner identities  (set OWNER_DISCORD_ID env var to your numeric Discord ID
# for the most reliable check; display-name fallbacks are also supported)
# ---------------------------------------------------------------------------
_OWNER_ID: int | None = int(v) if (v := os.getenv("OWNER_DISCORD_ID")) else None
_OWNER_NAMES = {"vortex", "menludiir", "tovortex"}


def _is_owner(user: discord.User | discord.Member) -> bool:
    if _OWNER_ID:
        # If an ID is configured, that is the only check that matters
        return user.id == _OWNER_ID
    # Fallback to name matching only when no ID is configured
    if user.name.lower() in _OWNER_NAMES:
        return True
    if isinstance(user, discord.Member) and user.display_name.lower() in _OWNER_NAMES:
        return True
    return False


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _format_count(value: float) -> str:
    """Format a float nicely: drop .0, otherwise show 1 decimal place."""
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _random_insult(data: dict) -> tuple[str, str]:
    """Returns (article, insult) e.g. ('an', 'ugly ass clown')."""
    w1 = random.choice(data["column1"])
    w2 = random.choice(data["column2"])
    w3 = random.choice(data["column3"])
    article = "an" if w1[0].lower() in "aeiou" else "a"
    return article, f"{w1} {w2} {w3}"


def _random_time_metric(minutes_remaining: float, metrics: list[dict]) -> str:
    metric = random.choice(metrics)
    count = minutes_remaining / metric["duration_minutes"]
    return metric["template"].format(count=_format_count(count))


def _normal_countdown(delta_seconds: float) -> str:
    total = int(delta_seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"**{days}** day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"**{hours}** hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"**{minutes}** minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts) or "**any moment now**"


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="when", description="How long until the next EQ2 launch (server or expansion)?")
    async def when(self, interaction: discord.Interaction) -> None:
        ctx = await resolve_guild_context(interaction.guild_id)
        row = await asyncio.to_thread(servers_db.get_server_by_world_sync, ctx.world)

        now = datetime.now(UTC)
        target = next_launch_target(row, now)
        if target is None:
            await interaction.response.send_message(
                "Nothing on the launch calendar right now — no expansion is scheduled yet.",
                ephemeral=True,
            )
            return

        label, when_dt = target
        delta = (when_dt - now).total_seconds()
        if delta <= 0:
            await interaction.response.send_message(f"🎉 **{label} is live!** Get in there!", ephemeral=False)
            return

        if _is_owner(interaction.user):
            countdown = _normal_countdown(delta)
            await interaction.response.send_message(
                f"⏳ {label} launches in: {countdown}\n*({format_launch_dt(when_dt)})*"
            )
            return

        # --- Everyone else gets the obtuse treatment ---
        insults = _load_json(_INSULTS_PATH)
        metrics = _load_json(_TIME_METRICS_PATH)["metrics"]
        minutes = delta / 60

        metric_str = _random_time_metric(minutes, metrics)
        insult = _random_insult(insults)

        username = interaction.user.display_name
        article, insult = insult
        await interaction.response.send_message(
            f"**{label}** launches in approximately **{metric_str}**.\n\nYou're {article} {insult}, {username}."
        )
