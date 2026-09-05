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


def test_raid_weekdays_handles_list_and_string_shapes():
    from backend.bot.cogs.afk import raid_weekdays

    teams = [
        {"raids": [{"days": [2, 4]}, {"days": "6,7"}]},
        {"raids": [{"days": []}]},
    ]
    assert raid_weekdays(teams) == {2, 4, 6, 7}
    assert raid_weekdays([]) == set()


def test_upcoming_raid_dates_filters_and_caps():
    import datetime as dt

    from backend.bot.cogs.afk import upcoming_raid_dates

    monday = dt.date(2026, 9, 7)  # a Monday
    dates = upcoming_raid_dates({2, 4}, monday, horizon_days=14)  # Tue + Thu
    assert dates == ["2026-09-08", "2026-09-10", "2026-09-15", "2026-09-17"]
    # Every day over a long horizon still caps at Discord's 25-option limit.
    assert len(upcoming_raid_dates({1, 2, 3, 4, 5, 6, 7}, monday, horizon_days=60)) == 25
