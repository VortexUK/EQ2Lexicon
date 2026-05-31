from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from backend.census.config import LAUNCH_DT_ISO

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------
_DATA = Path(__file__).resolve().parent.parent.parent.parent / "data"
_INSULTS_PATH = _DATA / "insult_creator.json"
_TIME_METRICS_PATH = _DATA / "time_metrics.json"

# ---------------------------------------------------------------------------
# Launch target — read from config so it can be updated via env var
# ---------------------------------------------------------------------------
LAUNCH_DT: datetime | None
try:
    LAUNCH_DT = datetime.fromisoformat(LAUNCH_DT_ISO.replace("Z", "+00:00")) if LAUNCH_DT_ISO else None
except ValueError:
    LAUNCH_DT = None

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

    @app_commands.command(name="when", description="How long until the EQ2 server launch?")
    async def when(self, interaction: discord.Interaction) -> None:
        if LAUNCH_DT is None:
            await interaction.response.send_message("No launch date configured.", ephemeral=True)
            return

        now = datetime.now(UTC)
        delta = (LAUNCH_DT - now).total_seconds()
        dt_str = LAUNCH_DT.strftime("%-d %B %Y, %H:%M UTC") if hasattr(LAUNCH_DT, "strftime") else LAUNCH_DT_ISO

        if delta <= 0:
            await interaction.response.send_message("🎉 **The server is live!** Get in there!", ephemeral=False)
            return

        if _is_owner(interaction.user):
            countdown = _normal_countdown(delta)
            await interaction.response.send_message(f"⏳ Server launches in: {countdown}\n*({dt_str})*")
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
            f"The server launches in approximately **{metric_str}**.\n\nYou're {article} {insult}, {username}."
        )
