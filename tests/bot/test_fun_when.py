"""Pure-logic tests for /when's launch-target resolution (fun.py)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.bot.cogs import fun

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _row(**overrides) -> dict:
    row = {
        "world": "Wuoshi",
        "launch_dt": "2026-05-01T17:00:00+00:00",  # long since launched
        "next_xpac": None,
        "next_xpac_dt": None,
    }
    row.update(overrides)
    return row


def test_unlaunched_server_counts_down_to_the_server() -> None:
    target = fun.next_launch_target(_row(launch_dt="2026-10-01T17:00:00+00:00"), NOW)
    assert target == ("The server", datetime(2026, 10, 1, 17, 0, tzinfo=UTC))


def test_server_launch_wins_over_a_scheduled_xpac() -> None:
    # Both set (weird but possible) — the server itself opens first.
    target = fun.next_launch_target(
        _row(launch_dt="2026-10-01T17:00:00+00:00", next_xpac="RoK", next_xpac_dt="2026-12-01T17:00:00+00:00"),
        NOW,
    )
    assert target is not None
    assert target[0] == "The server"


def test_launched_server_counts_down_to_the_next_xpac() -> None:
    target = fun.next_launch_target(_row(next_xpac="RoK", next_xpac_dt="2026-11-14T17:00:00+00:00"), NOW)
    assert target == ("Rise of Kunark", datetime(2026, 11, 14, 17, 0, tzinfo=UTC))


def test_launched_server_with_nothing_scheduled_is_none() -> None:
    assert fun.next_launch_target(_row(), NOW) is None


def test_xpac_without_a_date_is_none() -> None:
    assert fun.next_launch_target(_row(next_xpac="RoK", next_xpac_dt="not a date"), NOW) is None
    assert fun.next_launch_target(_row(next_xpac="RoK"), NOW) is None


def test_missing_registry_row_falls_back_to_the_env_date(monkeypatch) -> None:
    when = datetime(2026, 10, 1, 17, 0, tzinfo=UTC)
    monkeypatch.setattr(fun, "LAUNCH_DT", when)
    assert fun.next_launch_target(None, NOW) == ("The server", when)
    monkeypatch.setattr(fun, "LAUNCH_DT", None)
    assert fun.next_launch_target(None, NOW) is None


def test_xpac_full_names_tolerate_case_and_unknown_codes() -> None:
    assert fun.xpac_full_name("RoK") == "Rise of Kunark"
    assert fun.xpac_full_name("rok") == "Rise of Kunark"
    assert fun.xpac_full_name("TSO") == "The Shadow Odyssey"
    assert fun.xpac_full_name("SomethingNew") == "SomethingNew"


def test_format_launch_dt_is_windows_safe_and_utc() -> None:
    # 18:00 CEST == 16:00 UTC; day renders without a leading zero.
    from datetime import timedelta, timezone

    dt = datetime(2026, 11, 4, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    assert fun.format_launch_dt(dt) == "4 November 2026, 16:00 UTC"
