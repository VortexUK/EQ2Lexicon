"""/attendance — per-player summary of the linked guild's recent raid nights.

Reads the same stores the site's Attendance tab uses (sessions merged from
parser uploads, officer overrides applied, categories derived at read time)
and aggregates the per-player rollup across the last ``SESSION_WINDOW``
sessions. Subscriber-preview gated like the rest of the attendance feature
set; requires the invoking Discord server to be /lexicon-linked.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import resolve_guild_context
from backend.bot.messaging import send_plan
from backend.bot.render import build_attendance_summary, plan_code_block
from backend.server import attendance as derive
from backend.server.auth_deps import ADMIN_IDS
from backend.server.db import get_display_names_for_discord_ids, has_role
from backend.server.db.attendance import store as attendance_db
from backend.server.db.availability import store as availability_db
from backend.server.db.raid_planning import store as planning_db

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot

_log = logging.getLogger(__name__)

SESSION_WINDOW = 10


class AttendanceSummaryCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot

    @app_commands.command(name="attendance", description="Summarise the guild's attendance over recent raid nights")
    @app_commands.guild_only()
    async def attendance(self, interaction: discord.Interaction) -> None:
        ctx = await resolve_guild_context(interaction.guild_id)
        if not ctx.linked or ctx.guild_name is None:
            await interaction.response.send_message(
                "This server isn't linked to an EQ2 guild — run `/lexicon link` first.",
                ephemeral=True,
            )
            return
        # Same limited-preview gate as the site's attendance routes.
        uid = str(interaction.user.id)
        if uid not in ADMIN_IDS and not await has_role(uid, "subscriber"):
            await interaction.response.send_message("Attendance tracking is in limited preview.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)

        sessions = await attendance_db.list_sessions(ctx.world, ctx.guild_name, limit=SESSION_WINDOW)
        if not sessions:
            await interaction.followup.send(
                f"No attendance recorded yet for **{ctx.guild_name}** — sessions appear when "
                f"someone runs the EQ2Parser Raid tab on a raid night.",
            )
            return

        session_ids = [s["id"] for s in sessions]
        obs_by_session = await attendance_db.observations_for_sessions(session_ids)
        overrides_by_session = await attendance_db.overrides_for_sessions(session_ids)
        role_rows = await planning_db.get_roles(ctx.world, ctx.guild_name)
        roles = {r["character_name"].lower(): r["role"] for r in role_rows}
        claims = await planning_db.claims_map(ctx.world)
        primaries = await planning_db.primary_claims(ctx.world)
        user_mains, _ = derive.resolve_mains(role_rows, claims, primaries)

        totals: dict[str, dict[str, int]] = {}
        for s in sessions:
            afk_by_user = await availability_db.statuses_for_day(s["session_day"])
            _, user_rows = derive.derive_categories(
                obs_by_session.get(s["id"], []),
                roles,
                claims,
                afk_by_user,
                bool(s["scheduled"]),
                user_mains,
                overrides=overrides_by_session.get(s["id"], {}),
            )
            for u in user_rows:
                agg = totals.setdefault(u["discord_id"], {"present": 0, "sat_out": 0, "afk": 0, "awol": 0})
                if u["category"] in agg:
                    agg[u["category"]] += 1

        display = await get_display_names_for_discord_ids(sorted(totals))
        entries = []
        for uid_, counts in totals.items():
            # Main character name first (most recognisable in a guild
            # Discord), site display name as fallback.
            name = user_mains.get(uid_) or display.get(uid_) or f"User {uid_[-4:]}"
            entries.append({"name": name, **counts})

        table = build_attendance_summary(ctx.guild_name, entries, len(sessions))
        plan = plan_code_block(table, filename=f"{ctx.guild_name.replace(' ', '_')}_attendance.txt")
        await send_plan(interaction.followup, plan)
