from __future__ import annotations

from backend.server import census_refresh as cr


def test_should_refresh_respects_throttle(monkeypatch):
    cr._reset_for_test()
    monkeypatch.setattr(cr.census_health, "is_down", lambda: False)
    key = "menludiir:varsoon"
    assert cr._should_refresh(key) is True
    cr._mark_attempt(key)
    assert cr._should_refresh(key) is False  # within 15 min


def test_should_refresh_skips_when_down(monkeypatch):
    cr._reset_for_test()
    monkeypatch.setattr(cr.census_health, "is_down", lambda: True)
    assert cr._should_refresh("anykey:varsoon") is False


def test_merge_roster_keeps_best_known():
    # member resolved this time -> use fresh; member didn't -> fall back to stored
    fresh = {"Menludiir": {"name": "Menludiir", "level": 92, "cls": "Templar"}}
    roster = [{"name": "Menludiir", "rank": "Leader"}, {"name": "Alt", "rank": "Member"}]
    stored = {"alt": {"name": "Alt", "level": 80, "cls": "Fury"}}
    out = cr._merge_roster(roster, fresh, stored)
    by = {m["name"]: m for m in out}
    assert by["Menludiir"]["level"] == 92  # fresh
    assert by["Alt"]["level"] == 80  # best-known from stored
    assert by["Menludiir"]["rank"] == "Leader"  # rank from roster


def test_merge_roster_skips_unknown_members():
    # A member with NEITHER fresh NOR stored data is omitted — no blank rows.
    fresh = {"Menludiir": {"name": "Menludiir", "level": 92, "cls": "Templar"}}
    roster = [
        {"name": "Menludiir", "rank": "Leader"},
        {"name": "Ghost", "rank": "Member"},  # never resolved, not in store
    ]
    stored: dict[str, dict] = {}
    out = cr._merge_roster(roster, fresh, stored)
    names = {m["name"] for m in out}
    assert names == {"Menludiir"}
    assert "Ghost" not in names


# ---------------------------------------------------------------------------
# Honest "refreshing": the requester reports whether a refresh started, and a
# refresh that finds nothing newer still tells the page it is over.
# ---------------------------------------------------------------------------


def test_request_character_refresh_reports_whether_it_started(monkeypatch):
    import asyncio

    cr._reset_for_test()
    monkeypatch.setattr(cr.census_health, "is_down", lambda: False)
    monkeypatch.setattr(cr, "current_world", lambda: "Varsoon")
    created: list = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro: (created.append(coro), coro.close()))

    assert cr.request_character_refresh("Menludiir") is True  # started
    assert cr.request_character_refresh("Menludiir") is False  # throttled / in flight
    monkeypatch.setattr(cr.census_health, "is_down", lambda: True)
    assert cr.request_guild_refresh("Exordium") is False  # health-gated
    assert len(created) == 1


async def test_character_refresh_with_no_census_record_publishes_nochange(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch

    cr._reset_for_test()
    client = MagicMock()
    client.get_character = AsyncMock(return_value=None)
    client.close = AsyncMock()
    published: list[dict] = []
    with (
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=client),
        patch.object(cr.census_events, "publish", side_effect=published.append),
    ):
        key = "menludiir:varsoon"
        cr._in_flight.add(key)
        await cr._run_character_refresh("Menludiir", key, "Varsoon")

    assert published and published[0]["type"] == "character"
    assert published[0]["key"] == key and published[0]["nochange"] is True
    assert key not in cr._in_flight


def test_try_begin_applies_throttle_dedupe_and_health_gate(monkeypatch):
    cr._reset_for_test()
    monkeypatch.setattr(cr.census_health, "is_down", lambda: False)
    key = "aas:menludiir:varsoon"
    assert cr.try_begin(key) is True
    assert cr.try_begin(key) is False  # in flight
    cr.end(key)
    assert cr.try_begin(key) is False  # throttled (attempt marked)
    monkeypatch.setattr(cr.census_health, "is_down", lambda: True)
    assert cr.try_begin("gear:other:varsoon") is False  # health-gated
