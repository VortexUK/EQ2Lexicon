from __future__ import annotations

import io
from collections import Counter
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from backend.census.config import WORLD
from backend.census.constants import SPELL_TIER_ORDER as _TIER_ORDER

if TYPE_CHECKING:
    from backend.bot.bot import EQ2Bot
from backend.census.models import CharacterSpells, SpellEntry
from backend.eq2db.spells import catalogue as _spells

_COL_SEP = "  "


def _apply_blocklist(entries: list[SpellEntry]) -> list[SpellEntry]:
    blocklist = _spells.load_blocklist()
    if not blocklist:
        return entries
    return [e for e in entries if _spells.strip_roman(e.name).lower() not in blocklist]


def _build_details(data: CharacterSpells) -> str:
    entries = _spells.unique_highest_entries(_apply_blocklist(data.entries))
    tier_order = {t: i for i, t in enumerate(_TIER_ORDER)}
    entries.sort(key=lambda e: (tier_order.get(e.tier, 99), e.level, e.name))

    name_w = max(len("Spell"), max((len(e.name) for e in entries), default=0))
    name_w = min(name_w, 50)

    def _row(name, level, tier) -> str:
        return f"{name:<{name_w}}  {str(level):>3}  {tier}"

    sep = "─" * (name_w + 2 + 3 + 2 + max(len(t) for t in _TIER_ORDER))
    lines = [
        f"{data.character_name} — All Spells & Arts",
        "",
        _row("Spell", "Lvl", "Tier"),
        sep,
    ]
    current_tier = None
    for e in entries:
        if e.tier != current_tier:
            if current_tier is not None:
                lines.append("")
            current_tier = e.tier
        lines.append(_row(e.name[:name_w], e.level, e.tier))

    lines.append(f"\n{len(entries)} unique spells/arts")
    return "\n".join(lines)


def _build_table(data: CharacterSpells) -> str:
    entries = _spells.unique_highest_entries(_apply_blocklist(data.entries))

    count: Counter[str] = Counter(e.tier for e in entries)
    all_tiers = [t for t in _TIER_ORDER if count[t]]

    tier_w = max(len("Tier"), max((len(t) for t in all_tiers), default=0))
    count_w = max(len("Count"), max((len(str(count[t])) for t in all_tiers), default=0))

    def _row(tier, n) -> str:
        return tier.ljust(tier_w) + _COL_SEP + str(n).rjust(count_w)

    sep = "─" * (tier_w + count_w + len(_COL_SEP))
    lines = [
        f"{data.character_name} — Spell Summary",
        "",
        _row("Tier", "Count"),
        sep,
    ]
    for tier in all_tiers:
        lines.append(_row(tier, count[tier]))

    lines += [sep, _row("Total", sum(count.values()))]

    return "\n".join(lines)


class SpellcheckCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot
        self.world = WORLD

    @app_commands.command(name="spellcheck", description="Summarise a character's spell tiers")
    @app_commands.describe(
        name="Character first name (e.g. Sihtric)",
        details="Show every spell ordered by tier and level instead of just the summary",
    )
    async def spellcheck(
        self,
        interaction: discord.Interaction,
        name: str,
        details: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)

        data = await self.bot.census.get_character_spells(name, self.world)
        if data is None:
            await interaction.followup.send(
                f"No character found for **{name}** on **{self.world}**.",
                ephemeral=True,
            )
            return
        if not data.entries:
            await interaction.followup.send(
                f"**{data.character_name}** was found but has no spells or combat arts on record.",
                ephemeral=True,
            )
            return

        if details:
            text = _build_details(data)
            wrapped = f"```\n{text}\n```"
            if len(wrapped) <= 2000:
                await interaction.followup.send(wrapped)
            else:
                buf = io.BytesIO(text.encode("utf-8"))
                await interaction.followup.send(
                    file=discord.File(buf, filename=f"{data.character_name}_spells.txt"),
                )
        else:
            table = _build_table(data)
            await interaction.followup.send(f"```\n{table}\n```")
