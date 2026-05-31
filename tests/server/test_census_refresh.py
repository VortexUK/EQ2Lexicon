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
