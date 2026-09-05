"""backend/bot/render.py — the pure text half of every bot response.

These are the first bot tests in the repo. The builders were extracted
verbatim from the cogs (2026-09 formalisation); assertions pin the exact
output shape so the extraction provably changed nothing.
"""

from __future__ import annotations

from unittest.mock import patch

from backend.bot.render import (
    MESSAGE_LIMIT,
    SendPlan,
    build_guild_table,
    build_spell_details,
    build_spell_summary,
    fit_width,
    format_row,
    plan_code_block,
    rule,
    truncate,
)
from backend.census.models import CharacterSpells, GuildData, GuildMember, SpellEntry

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def test_truncate_elides_with_ellipsis():
    assert truncate("Shortname", 22) == "Shortname"
    assert truncate("Averyveryverylongguildname", 10) == "Averyvery…"
    assert len(truncate("Averyveryverylongguildname", 10)) == 10


def test_fit_width_header_vs_data_vs_cap():
    assert fit_width("AA", ["123", "45"], 4) == 3  # data wins over header
    assert fit_width("Tradeskill", ["Sage"], 24) == 10  # header wins over data
    assert fit_width("Name", ["Averyverylongname"], 8) == 8  # cap wins


def test_format_row_and_rule():
    widths = [4, 3]
    assert format_row(["ab", "c"], widths) == "ab    c  "
    assert rule(widths) == "────  ───"


# ---------------------------------------------------------------------------
# plan_code_block — the message-vs-file decision
# ---------------------------------------------------------------------------


def test_plan_short_text_is_fenced_content():
    plan = plan_code_block("hello", filename="x.txt", file_header="**Header**")
    assert plan == SendPlan(content="```\nhello\n```", file_text=None, filename="x.txt")


def test_plan_long_text_becomes_file():
    text = "x" * 3000
    plan = plan_code_block(text, filename="big.txt", file_header="**Big**")
    assert plan.content == "**Big**"
    assert plan.file_text == text
    assert plan.filename == "big.txt"


def test_plan_exact_limit_boundary():
    # The fence adds 8 chars: "```\n" + "\n```".
    fits = "x" * (MESSAGE_LIMIT - 8)
    assert plan_code_block(fits, filename="f.txt").file_text is None
    assert plan_code_block(fits + "x", filename="f.txt").file_text is not None


def test_plan_without_header_has_bare_file():
    plan = plan_code_block("y" * 3000, filename="f.txt")
    assert plan.content is None and plan.file_text is not None


# ---------------------------------------------------------------------------
# /guild table
# ---------------------------------------------------------------------------


def _member(name: str, rank_id: int | None, level: int | None = 70, **kw) -> GuildMember:
    defaults = dict(
        cls="Templar",
        ts_class="sage",
        ts_level=50,
        aa_level=100,
        deity="Rodcet Nife",
        rank="Raider",
    )
    defaults.update(kw)
    return GuildMember(name=name, level=level, rank_id=rank_id, **defaults)


def test_guild_table_sorts_by_rank_then_level_desc():
    data = GuildData(
        name="Paragon",
        world="Wuoshi",
        members=[
            _member("Lowbie", rank_id=1, level=50),
            _member("Boss", rank_id=0),
            _member("Toplevel", rank_id=1, level=70),
            _member("Unranked", rank_id=None),
        ],
    )
    table = build_guild_table(data)
    lines = table.splitlines()
    assert lines[0] == "Paragon  —  Wuoshi  (4 members with data)"
    names_in_order = [ln.split()[1] for ln in lines[4:]]  # col 0 is Rank
    assert names_in_order == ["Boss", "Toplevel", "Lowbie", "Unranked"]


def test_guild_table_placeholders_and_columns():
    data = GuildData(
        name="G",
        world="Wuoshi",
        members=[
            _member(
                "Bare",
                rank_id=None,
                level=None,
                cls=None,
                ts_class=None,
                ts_level=None,
                aa_level=None,
                deity=None,
                rank=None,
            )
        ],
    )
    table = build_guild_table(data)
    header = table.splitlines()[2]
    assert header.split() == ["Rank", "Name", "Class", "AA", "Tradeskill", "Deity"]
    row = table.splitlines()[4]
    assert row.split() == ["—", "Bare", "—", "—", "—", "—"]


def test_guild_table_capitalises_tradeskill_and_pairs_levels():
    data = GuildData(name="G", world="Wuoshi", members=[_member("Crafty", rank_id=0)])
    row = build_guild_table(data).splitlines()[4]
    assert "Templar (70)" in row
    assert "Sage (50)" in row


# ---------------------------------------------------------------------------
# /spellcheck builders
# ---------------------------------------------------------------------------


def _spells_data() -> CharacterSpells:
    return CharacterSpells(
        character_name="Sihtric",
        entries=[
            SpellEntry(name="Smite III", tier="Adept I", spell_type="spells", level=20),
            SpellEntry(name="Smite II", tier="Apprentice IV", spell_type="spells", level=12),
            SpellEntry(name="Heal I", tier="Master I", spell_type="spells", level=5),
        ],
    )


def test_spell_summary_orders_tiers_and_totals():
    with patch("backend.bot.render._spells.load_blocklist", return_value=set()):
        table = build_spell_summary(_spells_data())
    lines = table.splitlines()
    assert lines[0] == "Sihtric — Spell Summary"
    # unique_highest_entries keeps the highest Smite; tier rows follow
    # SPELL_TIER_ORDER; the total row closes the table.
    assert lines[-1].startswith("Total")
    assert lines[-1].rstrip().endswith("2")  # Smite III + Heal I


def test_spell_details_groups_by_tier_with_blank_lines():
    with patch("backend.bot.render._spells.load_blocklist", return_value=set()):
        text = build_spell_details(_spells_data())
    assert text.splitlines()[0] == "Sihtric — All Spells & Arts"
    assert "2 unique spells/arts" in text
    body = text.split("─" * 10)[-1]
    assert "" in body.splitlines()  # blank line between tier groups


def test_blocklist_filters_by_base_name():
    with patch("backend.bot.render._spells.load_blocklist", return_value={"smite"}):
        table = build_spell_summary(_spells_data())
    assert "Total" in table
    assert table.splitlines()[-1].rstrip().endswith("1")  # only Heal I survives


# ---------------------------------------------------------------------------
# /attendance summary
# ---------------------------------------------------------------------------


def test_attendance_summary_sorts_and_computes_percentages():
    from backend.bot.render import build_attendance_summary

    entries = [
        {"name": "Slacker", "present": 2, "sat_out": 1, "afk": 3, "awol": 4},
        {"name": "Regular", "present": 9, "sat_out": 1, "afk": 0, "awol": 0},
        {"name": "Also", "present": 9, "sat_out": 0, "afk": 1, "awol": 0},
    ]
    table = build_attendance_summary("Paragon", entries, 10)
    lines = table.splitlines()
    assert lines[0] == "Paragon — attendance over the last 10 raid night(s)"
    names = [ln.split()[0] for ln in lines[4:]]
    assert names == ["Also", "Regular", "Slacker"]  # present desc, then name
    assert lines[4].rstrip().endswith("90%")
    assert lines[6].rstrip().endswith("20%")


def test_attendance_summary_zero_sessions_shows_dash():
    from backend.bot.render import build_attendance_summary

    table = build_attendance_summary("G", [{"name": "X", "present": 0, "sat_out": 0, "afk": 0, "awol": 0}], 0)
    assert "—" in table.splitlines()[-1]


def test_afk_summary_lists_days_with_placeholders():
    from backend.bot.render import build_afk_summary

    entries = [
        {"label": "Fri 05 Sep", "names": ["Slacker", "ghosty"]},
        {"label": "Tue 09 Sep", "names": []},
    ]
    table = build_afk_summary("Paragon", entries)
    lines = table.splitlines()
    assert lines[0] == "Paragon — declared AFK, next 2 raid night(s)"
    assert lines[2].startswith("Fri 05 Sep") and lines[2].endswith("ghosty, Slacker")  # case-insensitive sort
    assert lines[3].endswith("·")  # nobody declared
