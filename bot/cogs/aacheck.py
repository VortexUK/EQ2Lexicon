from __future__ import annotations

import io
import json
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from census.client import CensusClient
from image.aa_tree import detect_tree_type, render_tree

_DATA_DIR  = Path(__file__).resolve().parent.parent.parent / "data" / "AAs"
_TREES_DIR = _DATA_DIR / "trees"

# Loaded once at import: tree_id → (name, detected_type)
_TREE_NAMES: dict[int, str] = {}
_TREE_TYPES: dict[int, str] = {}


def _load_tree_index() -> None:
    for path in _TREES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            aa_list = data.get("alternateadvancement_list") or []
            if aa_list:
                tid = int(path.stem)
                _TREE_NAMES[tid] = aa_list[0].get("name", path.stem)
                _TREE_TYPES[tid] = detect_tree_type(data)
        except Exception:
            pass


_load_tree_index()

# Static choice value → detect_tree_type key(s) that satisfy it
_CHOICE_TO_TYPES: dict[str, set[str]] = {
    "class":      {"class"},
    "subclass":   {"subclass"},
    "shadows":    {"shadows"},
    "heroic":     {"heroic"},
    "tradeskill": {"tradeskill", "tradeskill_general"},
}

_TREE_CHOICES = [
    app_commands.Choice(name="Class",    value="class"),
    app_commands.Choice(name="Subclass", value="subclass"),
    app_commands.Choice(name="Shadows",  value="shadows"),
    app_commands.Choice(name="Heroic",   value="heroic"),
    app_commands.Choice(name="Trade",    value="tradeskill"),
]


class AaCheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.census = CensusClient(service_id=os.getenv("CENSUS_SERVICE_ID", "example"))
        self.world = os.getenv("EQ2_WORLD", "Varsoon")

    async def cog_unload(self) -> None:
        await self.census.close()

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

        char_aas = await self.census.get_character_aas(character, self.world)
        if char_aas is None:
            await interaction.followup.send(
                f"No character found for **{character}** on **{self.world}**.",
                ephemeral=True,
            )
            return

        # Find the tree ID matching the chosen type
        wanted_types = _CHOICE_TO_TYPES.get(tree.value, {tree.value})
        tree_id = next(
            (tid for tid in char_aas.tree_ids if _TREE_TYPES.get(tid) in wanted_types),
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

        tree_name = _TREE_NAMES.get(tree_id, str(tree_id))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        total = sum(aa_data.values())
        await interaction.followup.send(
            content=f"**{char_aas.character_name}** — {tree_name} ({total} AAs)",
            file=discord.File(buf, filename="aacheck.png"),
        )
