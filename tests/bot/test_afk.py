"""/afk day reconciliation — pure logic (compute_afk_changes)."""

from __future__ import annotations

from backend.bot.cogs.afk import HORIZON_DAYS, compute_afk_changes

DAYS = [f"2026-09-{d:02d}" for d in range(5, 12)]


def test_new_afk_days_are_written():
    changes = compute_afk_changes({}, {DAYS[0], DAYS[2]}, DAYS)
    assert changes == {DAYS[0]: "afk", DAYS[2]: "afk"}


def test_unchosen_previously_afk_days_are_cleared():
    current = {DAYS[0]: "afk", DAYS[1]: "afk"}
    changes = compute_afk_changes(current, {DAYS[1]}, DAYS)
    assert changes == {DAYS[0]: "available"}  # DAYS[1] unchanged -> no write


def test_unchanged_days_produce_no_writes():
    current = {DAYS[0]: "afk"}
    assert compute_afk_changes(current, {DAYS[0]}, DAYS) == {}


def test_tentative_days_outside_the_selectable_list_are_untouched():
    # The cog excludes tentative days from `days`; even if the status map
    # carries them, no write is ever produced for a day not in the list.
    current = {DAYS[3]: "tentative"}
    changes = compute_afk_changes(current, set(), [d for d in DAYS if d != DAYS[3]])
    assert DAYS[3] not in changes


def test_horizon_fits_discords_select_cap():
    assert HORIZON_DAYS <= 25
