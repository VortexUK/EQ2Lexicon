"""/raidcomp — post the raid composition card.

The raid leader picks a starting zone (dropdown of the linked server's
current-expansion raid zones from zones.db, plus a Custom option that opens
a text modal) and, when the guild runs multiple teams, which team. The
composition itself comes from the raid planner's saved layout
(raid_placements) — the same groups officers drag around on the site —
with class colours from classes.db and classes resolved via the Census
roster (placeholder raiders keep their hand-entered class). The rendered
card (backend/image/raid_comp.py) is posted PUBLICLY in the channel as the
raid announcement; all the picking happens ephemerally first.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.bot.guild_context import GuildContext, resolve_guild_context
from backend.eq2db.classes import catalogue as classes_db
from backend.eq2db.zones import catalogue as zones_db
from backend.image.raid_comp import render_raid_comp
from backend.server.db.availability import store as availability_db
from backend.server.db.raid_planning import store as planning_db
from backend.server.db.raid_schedule import store as schedule_db
from backend.server.db.servers import store as servers_db

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot

_log = logging.getLogger(__name__)

_CUSTOM = "__custom__"

#: Zone type tokens that count as raid content (mirrors the raids scraper).
RAID_TYPE_TOKENS: frozenset[str] = frozenset({"raid_x4", "raid_x3", "raid_x2", "raid", "contested_raid"})


def raid_zone_names(current_xpac: str | None) -> list[str]:
    """Non-deprecated raid-type zones for the expansion, alphabetical.
    Empty on unknown xpac / missing zones.db — the dropdown then offers
    only the Custom option. Pure over the catalogue read."""
    if not current_xpac:
        return []
    try:
        zones = zones_db.list_by_expansion(current_xpac)
    except Exception:  # zones.db missing locally — degrade to custom-only
        return []
    names = {z["name"] for z in zones if not z["is_deprecated"] and any(t in RAID_TYPE_TOKENS for t in z["types"])}
    return sorted(names)


def build_groups(
    placements: list[dict],
    cls_by_char: dict[str, str],
) -> tuple[list[list[dict]], list[dict]]:
    """Planner placements -> renderer shape: 4 ordered group lists + the
    sitout strip, each member {name, cls, colour}. Pure."""
    groups: list[list[dict]] = [[] for _ in range(4)]
    sitout: list[dict] = []
    for p in placements:
        if p["sitout"]:
            sitout.append(make_member(p["character_name"], cls_by_char))
        elif p["group_num"] is not None:
            slot = p["slot"] if p["slot"] is not None else 0
            groups[p["group_num"] - 1].append((slot, make_member(p["character_name"], cls_by_char)))  # type: ignore[arg-type]
    ordered = [[m for _, m in sorted(g, key=lambda t: t[0])] for g in groups]  # type: ignore[misc]
    return ordered, sitout


def make_member(name: str, cls_by_char: dict[str, str]) -> dict:
    """Renderer member dict {name, cls, colour} with the classes.db colour."""
    cls = cls_by_char.get(name.lower())
    row = classes_db.find_by_name(cls) if cls else None
    return {"name": name, "cls": cls, "colour": row["colour"] if row else None}


def split_afk(
    placements: list[dict],
    role_display: dict[str, str],
    claims: dict[str, str],
    afk_user_ids: set[str],
) -> tuple[list[str], list[str]]:
    """(afk_but_placed, afk_unplaced) character names for the comp date.

    A rostered character is AFK when its claim owner declared the day afk
    (site calendar or the bot's /afk). Placed = in a group; the unplaced
    list feeds the card's dedicated AFK strip (they are NOT sitout — they
    said they wouldn't be there). Pure."""
    placed = {p["character_name"].lower() for p in placements if p.get("group_num") is not None}
    afk_lower = {lo for lo, uid in claims.items() if uid in afk_user_ids and lo in role_display}
    afk_placed = sorted(role_display[lo] for lo in afk_lower & placed)
    afk_unplaced = sorted(role_display[lo] for lo in afk_lower - placed)
    return afk_placed, afk_unplaced


class _CustomZoneModal(discord.ui.Modal, title="Custom starting zone"):
    zone = discord.ui.TextInput(label="Zone name", max_length=64)

    def __init__(self, view: _CompView) -> None:
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self._view.zone_name = str(self.zone.value).strip()
        await self._view.refresh(interaction)


class _CompView(discord.ui.View):
    def __init__(
        self,
        cog: RaidCompCog,
        ctx: GuildContext,
        zones: list[str],
        teams: list[dict],
        afk_warn_by_team: dict[int, list[str]],
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.zone_name: str | None = None
        self.team_index = 0
        self.teams = teams
        self.afk_warn_by_team = afk_warn_by_team

        zone_options = [discord.SelectOption(label=z, value=z) for z in zones[:24]]
        zone_options.append(discord.SelectOption(label="Custom zone…", value=_CUSTOM, emoji="✏️"))
        self.zone_select = discord.ui.Select(placeholder="Starting zone…", options=zone_options)
        self.zone_select.callback = self._on_zone  # type: ignore[method-assign]
        self.add_item(self.zone_select)

        if len(teams) > 1:
            team_options = [
                discord.SelectOption(label=t.get("name") or f"Team {i + 1}", value=str(i))
                for i, t in enumerate(teams[:25])
            ]
            self.team_select = discord.ui.Select(placeholder="Raid team…", options=team_options)
            self.team_select.callback = self._on_team  # type: ignore[method-assign]
            self.add_item(self.team_select)

        self.post_button = discord.ui.Button(label="Post composition", style=discord.ButtonStyle.success, disabled=True)
        self.post_button.callback = self._on_post  # type: ignore[method-assign]
        self.add_item(self.post_button)

    def _status(self) -> str:
        zone = f"**{self.zone_name}**" if self.zone_name else "*pick a zone*"
        team = ""
        if len(self.teams) > 1:
            team = f" · team: **{self.teams[self.team_index].get('name') or f'Team {self.team_index + 1}'}**"
        msg = f"Raid composition — starting zone: {zone}{team}"
        afk_placed = self.afk_warn_by_team.get(self.team_index) or []
        if afk_placed:
            msg += "\n⚠️ **Placed in groups but marked AFK today:** " + ", ".join(afk_placed)
        return msg

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.post_button.disabled = self.zone_name is None
        await interaction.response.edit_message(content=self._status(), view=self)

    async def _on_zone(self, interaction: discord.Interaction) -> None:
        value = self.zone_select.values[0]
        if value == _CUSTOM:
            await interaction.response.send_modal(_CustomZoneModal(self))
            return
        self.zone_name = value
        await self.refresh(interaction)

    async def _on_team(self, interaction: discord.Interaction) -> None:
        self.team_index = int(self.team_select.values[0])
        await self.refresh(interaction)

    async def _on_post(self, interaction: discord.Interaction) -> None:
        assert self.zone_name is not None
        await interaction.response.edit_message(content="Rendering…", view=None)
        try:
            file = await self.cog.render_card(self.ctx, self.zone_name, self.team_index, self.teams)
        except Exception:
            _log.exception("[raidcomp] render failed")
            await interaction.edit_original_response(content="Failed to build the composition card — logged.")
            return
        if file is None:
            await interaction.edit_original_response(
                content="No raid layout saved for this team — set one up in the site's Raid Planner first."
            )
            return
        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.edit_original_response(content="Can't post in this channel.")
            return
        try:
            await channel.send(file=file)
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="I don't have permission to post in this channel — give me Send Messages + Attach Files."
            )
            return
        await interaction.edit_original_response(content="Composition posted. ✅")
        self.stop()


class RaidCompCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot

    @app_commands.command(name="raidcomp", description="Post tonight's raid composition + starting zone")
    @app_commands.guild_only()
    async def raidcomp(self, interaction: discord.Interaction) -> None:
        ctx = await resolve_guild_context(interaction.guild_id)
        if not ctx.linked or ctx.guild_name is None:
            await interaction.response.send_message(
                "This server isn't linked to an EQ2 guild — run `/lexicon link` first.",
                ephemeral=True,
            )
            return

        server_row = await asyncio.to_thread(servers_db.get_server_by_world_sync, ctx.world)
        current_xpac = (server_row or {}).get("current_xpac")
        zones = await asyncio.to_thread(raid_zone_names, current_xpac)
        teams = await schedule_db.get_schedule(ctx.world, ctx.guild_name)

        # Per-team AFK warning data (who's placed in groups but declared AFK
        # today) — computed up front so the picker can warn before posting.
        role_rows = await planning_db.get_roles(ctx.world, ctx.guild_name)
        role_display = {r["character_name"].lower(): r["character_name"] for r in role_rows}
        claims = await planning_db.claims_map(ctx.world)
        statuses = await availability_db.statuses_for_day(dt.date.today().isoformat())
        afk_uids = {uid for uid, s in statuses.items() if s == "afk"}
        afk_warn_by_team: dict[int, list[str]] = {}
        for i in range(max(1, len(teams))):
            placements = await planning_db.get_placements(ctx.world, ctx.guild_name, i)
            afk_placed, _ = split_afk(placements, role_display, claims, afk_uids)
            afk_warn_by_team[i] = afk_placed

        view = _CompView(self, ctx, zones, teams, afk_warn_by_team)
        await interaction.response.send_message(view._status(), view=view, ephemeral=True)

    async def render_card(
        self, ctx: GuildContext, zone_name: str, team_index: int, teams: list[dict]
    ) -> discord.File | None:
        """Placements + classes -> rendered PNG. None when no layout exists."""
        assert ctx.guild_name is not None
        placements = await planning_db.get_placements(ctx.world, ctx.guild_name, team_index)
        if not placements:
            return None

        # Class lookup: census roster first, placeholder raiders' hand-set
        # class as fallback (they're census-hidden by definition).
        cls_by_char: dict[str, str] = {}
        role_rows = await planning_db.get_roles(ctx.world, ctx.guild_name)
        for r in role_rows:
            if r.get("cls"):
                cls_by_char[r["character_name"].lower()] = r["cls"]
        data = await self.bot.census.get_guild(ctx.guild_name, ctx.world)
        if data is not None:
            for m in data.members:
                if m.cls:
                    cls_by_char[m.name.lower()] = m.cls

        groups, sitout = build_groups(placements, cls_by_char)

        # AFK strip: rostered characters whose owner declared today AFK and
        # who aren't in a group. They're not "sitting out" — they said they
        # wouldn't be here — so they move to their own strip.
        role_display = {r["character_name"].lower(): r["character_name"] for r in role_rows}
        claims = await planning_db.claims_map(ctx.world)
        statuses = await availability_db.statuses_for_day(dt.date.today().isoformat())
        afk_uids = {uid for uid, s in statuses.items() if s == "afk"}
        _, afk_unplaced = split_afk(placements, role_display, claims, afk_uids)
        afk_lower = {n.lower() for n in afk_unplaced}
        sitout = [m for m in sitout if m["name"].lower() not in afk_lower]
        afk_members = [make_member(n, cls_by_char) for n in afk_unplaced]

        team_name = None
        if len(teams) > 1:
            team_name = teams[team_index].get("name") or f"Team {team_index + 1}"

        img = await asyncio.to_thread(
            render_raid_comp,
            ctx.guild_name,
            zone_name,
            groups,
            sitout,
            afk=afk_members,
            team_name=team_name,
            date_str=dt.date.today().strftime("%A %d %B"),
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return discord.File(buf, filename="raidcomp.png")
