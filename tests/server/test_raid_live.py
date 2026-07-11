"""Tests for backend/server/raid_live.py — schedule-window matching + the
Twitch-verified live cache. HTTP + DB are mocked; the window math is pure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.server import raid_live as rl


@pytest.fixture(autouse=True)
def _reset():
    rl._reset_for_test()
    yield
    rl._reset_for_test()


def test_slot_active_window_with_grace():
    def active(wd, mn):  # Tue+Thu 20:00-23:00
        return rl._slot_active_now([2, 4], 1200, 1380, wd, mn)

    assert active(2, 1260) is True  # Tue 21:00
    assert active(4, 1380) is True  # Thu 23:00 (end, inside grace)
    assert active(2, 1140) is False  # Tue 19:00 (before grace)
    assert active(2, 1190) is True  # Tue 19:50 (within 15m grace)
    assert active(3, 1260) is False  # Wed — not a raid day


def test_slot_active_handles_midnight_crossing():
    def mc(wd, mn):  # Tue 22:00-01:00
        return rl._slot_active_now([2], 1320, 60, wd, mn)

    assert mc(2, 1380) is True  # Tue 23:00
    assert mc(3, 30) is True  # Wed 00:30 — spillover past midnight
    assert mc(3, 120) is False  # Wed 02:00 — after end+grace


def test_team_scheduled_now_uses_team_timezone():
    team = {"primary_tz": "America/New_York", "raids": [{"days": "2,4", "start_min": 1200, "end_min": 1380}]}
    # Wed 02:00 UTC (winter) == Tue 21:00 EST → inside the Tue window.
    assert rl.team_scheduled_now(team, now=datetime(2026, 1, 14, 2, 0, tzinfo=UTC)) is True
    # Wed 18:00 UTC == Wed 13:00 EST → not a raid day/time.
    assert rl.team_scheduled_now(team, now=datetime(2026, 1, 14, 18, 0, tzinfo=UTC)) is False


def test_build_live_map_includes_only_live_candidates():
    cands = [
        {"world": "Varsoon", "guild_name": "Exordium", "name": "Main", "twitch_login": "foo"},
        {"world": "Varsoon", "guild_name": "G2", "name": "T", "twitch_login": "offline"},
        {"world": "Kaladim", "guild_name": "K", "name": "K1", "twitch_login": "bar"},
    ]
    live = {"foo": {"viewer_count": 42, "title": "raid", "started_at": "x"}, "bar": {"viewer_count": 3}}
    out = rl.build_live_map(cands, live)
    assert set(out) == {"Varsoon", "Kaladim"}
    assert out["Varsoon"][0]["guild_name"] == "Exordium"
    assert out["Varsoon"][0]["twitch_url"] == "https://twitch.tv/foo"
    assert out["Varsoon"][0]["viewer_count"] == 42


def test_is_configured(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    assert rl.is_configured() is False
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    assert rl.is_configured() is True


async def test_refresh_no_ops_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    with patch("backend.server.db.raid_schedule.store.list_all_teams_with_twitch", new=AsyncMock()) as db:
        await rl.refresh()
    db.assert_not_awaited()
    assert rl.get_live("Varsoon") == []


async def test_refresh_populates_cache_for_scheduled_live_teams(monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    now = datetime(2026, 1, 14, 2, 0, tzinfo=UTC)  # Tue 21:00 EST
    teams = [
        {
            "world": "Varsoon",
            "guild_name": "Exordium",
            "name": "Main",
            "primary_tz": "America/New_York",
            "twitch_login": "foo",
            "raids": [{"days": "2,4", "start_min": 1200, "end_min": 1380}],
        },
        # Not scheduled now (Mondays only) → excluded even if it were live.
        {
            "world": "Varsoon",
            "guild_name": "G2",
            "name": "T",
            "primary_tz": "America/New_York",
            "twitch_login": "bar",
            "raids": [{"days": "1", "start_min": 1200, "end_min": 1380}],
        },
    ]
    with (
        patch("backend.server.raid_live.datetime") as dt,
        patch("backend.server.db.raid_schedule.store.list_all_teams_with_twitch", new=AsyncMock(return_value=teams)),
        patch("backend.server.raid_live._get_token", new=AsyncMock(return_value="tok")),
        patch("backend.server.raid_live._fetch_live", new=AsyncMock(return_value={"foo": {"viewer_count": 10}})) as fl,
    ):
        dt.now.return_value = now
        await rl.refresh()
    # only the scheduled 'foo' was polled + is live
    assert fl.await_args.args[0] == ["foo"]
    live = rl.get_live("Varsoon")
    assert len(live) == 1 and live[0]["twitch_login"] == "foo"
