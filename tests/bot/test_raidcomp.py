"""/raidcomp — pure assembly helpers + a renderer smoke test."""

from __future__ import annotations

from unittest.mock import patch

from backend.bot.cogs.raidcomp import build_groups, raid_zone_names
from backend.image.raid_comp import render_raid_comp


def test_build_groups_orders_by_slot_and_splits_sitout():
    placements = [
        {"character_name": "Second", "group_num": 1, "slot": 1, "sitout": False},
        {"character_name": "First", "group_num": 1, "slot": 0, "sitout": False},
        {"character_name": "Grouptwo", "group_num": 2, "slot": 0, "sitout": False},
        {"character_name": "Benchy", "group_num": None, "slot": None, "sitout": True},
    ]
    cls_by_char = {"first": "Templar", "benchy": "Guardian"}
    groups, sitout = build_groups(placements, cls_by_char)
    assert [m["name"] for m in groups[0]] == ["First", "Second"]
    assert groups[0][0]["cls"] == "Templar"
    assert groups[0][0]["colour"]  # classes.db colour resolved
    assert groups[0][1]["cls"] is None and groups[0][1]["colour"] is None
    assert [m["name"] for m in groups[1]] == ["Grouptwo"]
    assert groups[2] == [] and groups[3] == []
    assert [m["name"] for m in sitout] == ["Benchy"]


def test_raid_zone_names_filters_types_and_deprecated():
    zones = [
        {"name": "Veeshan's Peak", "types": ["raid_x4"], "is_deprecated": False},
        {"name": "Old Raid", "types": ["raid_x4"], "is_deprecated": True},
        {"name": "Group Zone", "types": ["group"], "is_deprecated": False},
        {"name": "Contested Dragon", "types": ["contested_raid"], "is_deprecated": False},
    ]
    with patch("backend.bot.cogs.raidcomp.zones_db") as mock_db:
        mock_db.list_by_expansion.return_value = zones
        assert raid_zone_names("RoK") == ["Contested Dragon", "Veeshan's Peak"]
    assert raid_zone_names(None) == []


def test_render_raid_comp_smoke():
    groups = [
        [{"name": "Tanky", "cls": "Guardian", "colour": "#60a5fa"}] * 6,
        [{"name": "Healy", "cls": "Templar", "colour": "#4ade80"}] * 3,
        [],
        [{"name": "Nocolour", "cls": None, "colour": None}],
    ]
    sitout = [{"name": "Benchy", "cls": "Wizard", "colour": "#f87171"}]
    img = render_raid_comp(
        "Paragon",
        "Veeshan's Peak",
        groups,
        sitout,
        team_name="Team 1",
        date_str="Friday 05 September",
    )
    assert img.width > 500 and img.height > 400
    # Dark card, not a blank white canvas.
    assert img.getpixel((5, 5)) == (18, 15, 11)


def test_render_raid_comp_without_sitout_is_shorter():
    tall = render_raid_comp("G", "Z", [[], [], [], []], [{"name": "X", "cls": None, "colour": None}])
    short = render_raid_comp("G", "Z", [[], [], [], []], [])
    assert short.height < tall.height
