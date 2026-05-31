"""Tests for the Phase-1 dungeon-speed-per-character variant in
web/routes/rankings.py:_build_speed_board_character.

For dungeons (scope=group + curated dungeon zone), Speed ranks per-player
instead of per-guild. Each player gets one row showing the fastest clear
they were on. If 6 friends speedrun a dungeon together, all 6 appear
tied at the same duration.
"""

from __future__ import annotations

import pytest

from backend.server.api.rankings import _build_speed_board, _build_speed_board_character


def _combatant(name: str, *, cls: str | None = "Wizard", is_player: bool = True) -> dict:
    return {
        "name": name,
        "ally": 1,
        "is_player": is_player,
        "cls": cls,
        "encdps": 100.0,
        "enchps": 0.0,
        "ilvl": 200.0,
        "level": 100,
    }


def _kill(
    *,
    id: int,
    zone: str,
    title: str,
    duration_s: int,
    combatants: list[dict],
    guild_name: str | None = "Exordium",
    scope: str = "group",
    player_count: int | None = None,
    started_at: int = 1000,
) -> dict:
    if player_count is None:
        player_count = sum(1 for c in combatants if c.get("is_player"))
    return {
        "id": id,
        "zone": zone,
        "title": title,
        "duration_s": duration_s,
        "guild_name": guild_name,
        "scope": scope,
        "player_count": player_count,
        "started_at": started_at,
        "combatants": combatants,
    }


def test_character_speed_one_row_per_player():
    kills = [
        _kill(
            id=1,
            zone="Halls of Fate",
            title="Doomguard",
            duration_s=83,
            started_at=1000,
            combatants=[_combatant("Alpha"), _combatant("Bravo"), _combatant("Charlie")],
        ),
    ]
    rows = _build_speed_board_character(kills, zone="Halls of Fate", boss="Doomguard")
    names = sorted(r["name"] for r in rows)
    assert names == ["Alpha", "Bravo", "Charlie"]
    assert all(r["duration_s"] == 83 for r in rows), "all 3 players tied on the same clear"
    assert all(r["kind"] == "character" for r in rows)


def test_character_speed_keeps_fastest_per_player():
    # Alpha was on two clears (90s and 60s). They should get one row at 60s.
    kills = [
        _kill(
            id=1,
            zone="Halls of Fate",
            title="Doomguard",
            duration_s=90,
            combatants=[_combatant("Alpha"), _combatant("Bravo")],
        ),
        _kill(
            id=2,
            zone="Halls of Fate",
            title="Doomguard",
            duration_s=60,
            combatants=[_combatant("Alpha"), _combatant("Charlie")],
        ),
    ]
    rows = _build_speed_board_character(kills, zone="Halls of Fate", boss="Doomguard")
    by_name = {r["name"]: r for r in rows}
    assert by_name["Alpha"]["duration_s"] == 60
    assert by_name["Bravo"]["duration_s"] == 90
    assert by_name["Charlie"]["duration_s"] == 60


def test_character_speed_excludes_non_player_combatants():
    # Multi-word pet (is_player=False) should not be ranked.
    kills = [
        _kill(
            id=1,
            zone="Halls of Fate",
            title="Doomguard",
            duration_s=83,
            combatants=[
                _combatant("Alpha"),
                _combatant("a krait warrior", cls=None, is_player=False),
            ],
        ),
    ]
    rows = _build_speed_board_character(kills, zone="Halls of Fate", boss="Doomguard")
    names = {r["name"] for r in rows}
    assert names == {"Alpha"}


def test_character_speed_filters_by_zone_and_boss():
    kills = [
        _kill(id=1, zone="Halls of Fate", title="Doomguard", duration_s=60, combatants=[_combatant("Alpha")]),
        _kill(id=2, zone="Halls of Fate", title="Other Boss", duration_s=30, combatants=[_combatant("Alpha")]),
        _kill(id=3, zone="Different Zone", title="Doomguard", duration_s=10, combatants=[_combatant("Alpha")]),
    ]
    rows = _build_speed_board_character(kills, zone="Halls of Fate", boss="Doomguard")
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha"
    assert rows[0]["duration_s"] == 60


def test_character_speed_includes_class_and_ilvl():
    kills = [
        _kill(
            id=1,
            zone="Halls of Fate",
            title="Doomguard",
            duration_s=83,
            combatants=[_combatant("Alpha", cls="Berserker")],
        ),
    ]
    rows = _build_speed_board_character(kills, zone="Halls of Fate", boss="Doomguard")
    assert rows[0]["cls"] == "Berserker"
    assert rows[0]["ilvl"] == 200.0  # avg of one combatant


def test_character_speed_returns_sorted_ascending_duration():
    kills = [
        _kill(id=1, zone="Halls of Fate", title="Doomguard", duration_s=120, combatants=[_combatant("Alpha")]),
        _kill(id=2, zone="Halls of Fate", title="Doomguard", duration_s=60, combatants=[_combatant("Bravo")]),
        _kill(id=3, zone="Halls of Fate", title="Doomguard", duration_s=90, combatants=[_combatant("Charlie")]),
    ]
    rows = _build_speed_board_character(kills, zone="Halls of Fate", boss="Doomguard")
    durations = [r["duration_s"] for r in rows]
    assert durations == sorted(durations), f"expected ascending, got {durations}"


def test_guild_speed_still_works_unchanged():
    # Sanity: the existing per-guild speed board still produces guild rows
    # (raids haven't changed).
    kills = [
        _kill(
            id=1,
            zone="Castle Mistmoore",
            title="Tarinax",
            duration_s=300,
            guild_name="Exordium",
            scope="raid",
            player_count=24,
            combatants=[_combatant("Alpha")],
        ),
    ]
    rows = _build_speed_board(kills, size="raid", zone="Castle Mistmoore", boss="Tarinax")
    assert len(rows) == 1
    assert rows[0]["kind"] == "guild"
    assert rows[0]["guild_name"] == "Exordium"
