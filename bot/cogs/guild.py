import io
from collections.abc import Callable

import discord
from discord import app_commands
from discord.ext import commands

from census.client import CensusClient
from census.config import SERVICE_ID, WORLD
from census.models import GuildData, GuildMember

_COL_SEP = "  "


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def _build_table(data: GuildData) -> str:
    members = sorted(
        data.members,
        key=lambda m: (m.rank_id if m.rank_id is not None else 9999, -(m.level or 0)),
    )

    # Column definitions: (header, callable, max_width)
    def _cls(m: GuildMember) -> str:
        if m.cls and m.level is not None:
            return f"{m.cls} ({m.level})"
        return m.cls or "—"

    def _ts(m: GuildMember) -> str:
        ts = m.ts_class.capitalize() if m.ts_class else None
        if ts and m.ts_level is not None:
            return f"{ts} ({m.ts_level})"
        return ts or "—"

    cols: list[tuple[str, Callable[[GuildMember], str], int]] = [
        ("Rank", lambda m: m.rank or "—", 16),
        ("Name", lambda m: m.name, 22),
        ("Class", _cls, 24),
        ("AA", lambda m: str(m.aa_level) if m.aa_level is not None else "—", 4),
        ("Tradeskill", _ts, 24),
        ("Deity", lambda m: m.deity or "—", 16),
    ]

    # Compute actual column widths (header vs data)
    widths: list[int] = []
    for header, fn, max_w in cols:
        data_w = max((len(fn(m)) for m in members), default=0)
        widths.append(min(max(len(header), data_w), max_w))

    def _row(values: list[str]) -> str:
        return _COL_SEP.join(v.ljust(widths[i]) for i, v in enumerate(values))

    header_row = _row([h for h, _, _ in cols])
    separator = _COL_SEP.join("─" * w for w in widths)

    lines = [
        f"{data.name}  —  {data.world}  ({len(members)} members with data)",
        "",
        header_row,
        separator,
    ]
    for m in members:
        row_vals = [_trunc(fn(m), widths[i]) for i, (_, fn, _) in enumerate(cols)]
        lines.append(_row(row_vals))

    return "\n".join(lines)


class GuildCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # CENSUS-CLIENT-LIFECYCLE: migrate to web.lib.census_lifecycle.shared_census_client (Phase 2c.2)
        self.census = CensusClient(service_id=SERVICE_ID)
        self.world = WORLD

    async def cog_unload(self) -> None:
        await self.census.close()

    @app_commands.command(name="guild", description="Show a member summary for an EverQuest 2 guild")
    @app_commands.describe(name="Guild name (e.g. Exordium)")
    async def guild(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)

        data = await self.census.get_guild(name, self.world)
        if data is None or not data.members:
            await interaction.followup.send(
                f"No guild found for **{name}** on **{self.world}**.",
                ephemeral=True,
            )
            return

        table = _build_table(data)
        wrapped = f"```\n{table}\n```"

        # Discord message limit is 2000 chars; send as file if too large
        if len(wrapped) <= 2000:
            await interaction.followup.send(wrapped)
        else:
            buf = io.BytesIO(table.encode("utf-8"))
            await interaction.followup.send(
                f"**{data.name}** — {data.world} ({len(data.members)} members)",
                file=discord.File(buf, filename=f"{data.name.replace(' ', '_')}_guild.txt"),
            )
