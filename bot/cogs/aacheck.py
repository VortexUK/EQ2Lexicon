from __future__ import annotations

import io
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from census.config import WORLD
from image.aa_tree import load_tree_index, render_tree

if TYPE_CHECKING:
    from bot.bot import EQ2Bot

# Static choice value → detect_tree_type key(s) that satisfy it
_CHOICE_TO_TYPES: dict[str, set[str]] = {
    "class": {"class"},
    "subclass": {"subclass"},
    "shadows": {"shadows"},
    "heroic": {"heroic"},
    "tradeskill": {"tradeskill", "tradeskill_general"},
}

_TREE_CHOICES = [
    app_commands.Choice(name="Class", value="class"),
    app_commands.Choice(name="Subclass", value="subclass"),
    app_commands.Choice(name="Shadows", value="shadows"),
    app_commands.Choice(name="Heroic", value="heroic"),
    app_commands.Choice(name="Trade", value="tradeskill"),
]


class AaCheckCog(commands.Cog):
    def __init__(self, bot: EQ2Bot) -> None:
        self.bot = bot
        self.world = WORLD

    @app_commands.command(name="aacheck", description="Show a character's AA allocations for a tree")
    @app_commands.describe(
        character="Character name",
        tree="Which AA tree to display",
    )
    @app_commands.choices(tree=_TREE_CHOICES)
    async def aacheck(
        self,
        interaction: discord.Interaction,
        character: str,
        tree: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(thinking=True)

        char_aas = await self.bot.census.get_character_aas(character, self.world)
        if char_aas is None:
            await interaction.followup.send(
                f"No character found for **{character}** on **{self.world}**.",
                ephemeral=True,
            )
            return

        # Find the tree ID matching the chosen type
        tree_index = load_tree_index()
        wanted_types = _CHOICE_TO_TYPES.get(tree.value, {tree.value})
        tree_id = next(
            (tid for tid in char_aas.tree_ids if tree_index.get(tid, {}).get("type") in wanted_types),
            None,
        )
        if tree_id is None:
            await interaction.followup.send(
                f"**{char_aas.character_name}** has no AAs in a **{tree.name}** tree.",
                ephemeral=True,
            )
            return

        aa_data = char_aas.for_tree(tree_id)

        try:
            img, _tree_type = render_tree(tree_id, aa_data)
        except Exception as exc:
            await interaction.followup.send(f"Failed to render tree: {exc}", ephemeral=True)
            raise

        tree_name = tree_index.get(tree_id, {}).get("name", str(tree_id))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        total = sum(aa_data.values())
        await interaction.followup.send(
            content=f"**{char_aas.character_name}** — {tree_name} ({total} AAs)",
            file=discord.File(buf, filename="aacheck.png"),
        )
