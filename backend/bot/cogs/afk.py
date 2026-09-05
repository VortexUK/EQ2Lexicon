"""/afk — mark upcoming days AFK from Discord.

Writes the SAME user_availability rows the site's raid planner reads
(backend/server/db/availability.py, keyed purely by discord id — no claim
needed), so a day marked here immediately shows on the planner overlay and
drives the attendance 'afk' category.

Discord can't render a real calendar; the UI is an ephemeral multi-select
over the next ``HORIZON_DAYS`` days with the currently-AFK days
preselected. Submitting reconciles ONLY afk<->available: days marked
'tentative' on the website are excluded from the select and never touched.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import is_guild_officer, resolve_guild_context
from backend.bot.messaging import send_plan
from backend.bot.render import build_afk_summary, plan_code_block
from backend.server import attendance as derive
from backend.server.auth_deps import ADMIN_IDS
from backend.server.db import get_display_names_for_discord_ids
from backend.server.db.availability import store as availability_db
from backend.server.db.raid_planning import store as planning_db
from backend.server.db.raid_schedule import store as schedule_db

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot

_log = logging.getLogger(__name__)

#: Discord selects cap at 25 options — 3 weeks fits with room to spare.
#: Used only when the server has no linked raid schedule to filter by.
HORIZON_DAYS = 21

#: Raid-day mode: how far ahead to look for scheduled raid dates. The
#: select still caps at 25 options.
RAID_HORIZON_DAYS = 56


def raid_weekdays(teams: list[dict]) -> set[int]:
    """ISO weekdays (1=Mon..7=Sun) any team raids on. Slot ``days`` arrive
    as a list from the store or a comma-string from older shapes — accept
    both. Pure."""
    out: set[int] = set()
    for team in teams:
        for r in team.get("raids", []):
            days = r.get("days", [])
            if isinstance(days, str):
                days = [d for d in days.split(",") if d]
            out.update(int(d) for d in days)
    return out


def upcoming_raid_dates(weekdays: set[int], today: dt.date, *, horizon_days: int = RAID_HORIZON_DAYS) -> list[str]:
    """The next raid dates (ISO) within the horizon, capped at Discord's
    25-option select limit. Pure."""
    dates = [
        (today + dt.timedelta(days=i)).isoformat()
        for i in range(horizon_days)
        if (today + dt.timedelta(days=i)).isoweekday() in weekdays
    ]
    return dates[:25]


def compute_afk_changes(current: dict[str, str], chosen: set[str], days: list[str]) -> dict[str, str]:
    """The write-set for set_days: chosen days become 'afk', previously-afk
    unchosen days become 'available' (row delete). 'tentative' days are
    site-owned — never in ``days``' select, never written here. Pure."""
    changes: dict[str, str] = {}
    for day in days:
        status = current.get(day)
        if day in chosen and status != "afk":
            changes[day] = "afk"
        elif day not in chosen and status == "afk":
            changes[day] = "available"
    return changes


def _day_label(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return d.strftime("%a %d %b")


class _AfkSelect(discord.ui.Select):
    def __init__(self, days: list[str], current: dict[str, str]) -> None:
        options = [
            discord.SelectOption(label=_day_label(day), value=day, default=current.get(day) == "afk") for day in days
        ]
        super().__init__(
            placeholder="Select every day you'll be AFK…",
            min_values=0,
            max_values=len(options),
            options=options,
        )
        self._days = days
        self._current = current

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = set(self.values)
        changes = compute_afk_changes(self._current, chosen, self._days)
        if changes:
            await availability_db.set_days(str(interaction.user.id), changes)
            # The select's state is the new truth for the next interaction.
            for day, status in changes.items():
                if status == "available":
                    self._current.pop(day, None)
                else:
                    self._current[day] = status
        afk_days = sorted(d for d in self._days if self._current.get(d) == "afk")
        summary = ", ".join(_day_label(d) for d in afk_days) if afk_days else "none"
        # Rebuild the view so the select's DEFAULTS match the new state:
        # Discord visually resets a select to its original defaults after
        # every submit, so without this a cleared day looked ticked again
        # and the next interaction re-submitted (re-added) it. Seen live.
        await interaction.response.edit_message(
            content=f"Saved. You're marked **AFK** on: {summary}\n"
            f"(The raid planner and attendance tracking update immediately. "
            f"Adjust again below if needed.)",
            view=_AfkView(self._days, dict(self._current)),
        )


class _AfkView(discord.ui.View):
    def __init__(self, days: list[str], current: dict[str, str]) -> None:
        super().__init__(timeout=300)
        self.add_item(_AfkSelect(days, current))


class AfkCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot

    @app_commands.command(name="afk", description="Mark raid days you'll miss — feeds the raid planner + attendance")
    async def afk(self, interaction: discord.Interaction) -> None:
        today = dt.date.today()

        # Only offer the guild's actual raid days — nobody cares about AFK
        # on an off night. Falls back to every day when the server has no
        # linked schedule to filter by.
        weekdays: set[int] = set()
        ctx = await resolve_guild_context(interaction.guild_id)
        if ctx.linked and ctx.guild_name is not None:
            teams = await schedule_db.get_schedule(ctx.world, ctx.guild_name)
            weekdays = raid_weekdays(teams)

        if weekdays:
            days = upcoming_raid_dates(weekdays, today)
            intro = f"Pick the **raid days** you'll be AFK (next {len(days)} raid nights"
        else:
            days = [(today + dt.timedelta(days=i)).isoformat() for i in range(HORIZON_DAYS)]
            intro = f"Pick the days you'll be **AFK** over the next {HORIZON_DAYS} days (no raid schedule found to filter by"

        current = await availability_db.get_range(str(interaction.user.id), days[0], days[-1])
        tentative = sorted(d for d, s in current.items() if s == "tentative" and d in days)
        selectable = [d for d in days if current.get(d) != "tentative"]
        note = (
            "\n-# Tentative days (set on the website) are left alone: " + ", ".join(_day_label(d) for d in tentative)
            if tentative
            else ""
        )
        await interaction.response.send_message(
            f"{intro}; currently-AFK days are preselected — unselect to clear):{note}",
            view=_AfkView(selectable, dict(current)),
            ephemeral=True,
        )

    @app_commands.command(name="afksummary", description="Officers: who has declared AFK on each upcoming raid day")
    @app_commands.guild_only()
    async def afksummary(self, interaction: discord.Interaction) -> None:
        ctx = await resolve_guild_context(interaction.guild_id)
        if not ctx.linked or ctx.guild_name is None:
            await interaction.response.send_message(
                "This server isn't linked to an EQ2 guild — run `/lexicon link` first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        uid = str(interaction.user.id)
        if uid not in ADMIN_IDS and not await is_guild_officer(uid, ctx):
            await interaction.followup.send(
                f"Only officers of **{ctx.guild_name}** can view the AFK summary.", ephemeral=True
            )
            return

        teams = await schedule_db.get_schedule(ctx.world, ctx.guild_name)
        weekdays = raid_weekdays(teams)
        today = dt.date.today()
        if weekdays:
            days = upcoming_raid_dates(weekdays, today, horizon_days=28)
        else:
            days = [(today + dt.timedelta(days=i)).isoformat() for i in range(14)]

        # Label AFK users by their raid main (recognisable in guild chat),
        # site display name as fallback. Only rostered players matter here.
        role_rows = await planning_db.get_roles(ctx.world, ctx.guild_name)
        claims = await planning_db.claims_map(ctx.world)
        primaries = await planning_db.primary_claims(ctx.world)
        user_mains, _ = derive.resolve_mains(role_rows, claims, primaries)
        roled_lower = {r["character_name"].lower() for r in role_rows}
        rostered_uids = {uid_ for lo, uid_ in claims.items() if lo in roled_lower}
        display = await get_display_names_for_discord_ids(sorted(rostered_uids))

        entries = []
        for day in days:
            statuses = await availability_db.statuses_for_day(day)
            names = [
                user_mains.get(u) or display.get(u) or f"User {u[-4:]}"
                for u, s in statuses.items()
                if s == "afk" and u in rostered_uids
            ]
            entries.append({"label": _day_label(day), "names": names})

        table = build_afk_summary(ctx.guild_name, entries)
        plan = plan_code_block(table, filename=f"{ctx.guild_name.replace(' ', '_')}_afk.txt")
        await send_plan(interaction.followup, plan)
