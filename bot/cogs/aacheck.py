from __future__ import annotations

import io
import json
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from census.client import CensusClient
from image.aa_tree import render_tree

_DATA_DIR  = Path(__file__).resolve().parent.parent.parent / "data" / "AAs"
_TREES_DIR = _DATA_DIR / "trees"

# tree_id → tree name, loaded once at import time
_TREE_NAMES: dict[int, str] = {}


def _load_tree_names() -> None:
    for path in _TREES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            aa_list = data.get("alternateadvancement_list") or []
            if aa_list:
                name = aa_list[0].get("name", path.stem)
                _TREE_NAMES[int(path.stem)] = name
        except Exception:
            pass


_load_tree_names()


class AaCheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.census = CensusClient(service_id=os.getenv("CENSUS_SERVICE_ID", "example"))
        self.world = os.getenv("EQ2_WORLD", "Varsoon")

    async def cog_unload(self) -> None:
        await self.census.close()

    async def tree_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        character: str = interaction.namespace.character or ""
        if not character:
            return []

        char_aas = await self.census.get_character_aas(character, self.world)
        if char_aas is None:
            return []

        choices: list[app_commands.Choice[str]] = []
        current_lower = current.lower()
        for tree_id in sorted(char_aas.tree_ids):
            name = _TREE_NAMES.get(tree_id, str(tree_id))
            if current_lower and current_lower not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name, value=str(tree_id)))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="aacheck", description="Show a character's AA allocations for a tree")
    @app_commands.describe(
        character="Character name",
        tree="AA tree to display",
    )
    @app_commands.autocomplete(tree=tree_autocomplete)
    async def aacheck(
        self,
        interaction: discord.Interaction,
        character: str,
        tree: str,
    ) -> None:
        await interaction.response.defer(thinking=True)

        char_aas = await self.census.get_character_aas(character, self.world)
        if char_aas is None:
            await interaction.followup.send(
                f"No character found for **{character}** on **{self.world}**.",
                ephemeral=True,
            )
            return

        try:
            tree_id = int(tree)
        except ValueError:
            await interaction.followup.send("Invalid tree selection.", ephemeral=True)
            return

        if tree_id not in char_aas.tree_ids:
            tree_name = _TREE_NAMES.get(tree_id, str(tree_id))
            await interaction.followup.send(
                f"**{char_aas.character_name}** has no AAs in the **{tree_name}** tree.",
                ephemeral=True,
            )
            return

        aa_data = char_aas.for_tree(tree_id)

        try:
            img, _tree_type = render_tree(tree_id, aa_data)
        except Exception as exc:
            await interaction.followup.send(f"Failed to render tree: {exc}", ephemeral=True)
            raise

        tree_name = _TREE_NAMES.get(tree_id, str(tree_id))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        total = sum(aa_data.values())
        await interaction.followup.send(
            content=f"**{char_aas.character_name}** — {tree_name} ({total} AAs)",
            file=discord.File(buf, filename="aacheck.png"),
        )
