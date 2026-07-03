"""Raid-schedule DB layer + API tests.

DB layer is exercised against a temp users.db (explicit ``path=``). The API is
tested with the ``app`` fixture: a signed session cookie for auth + mocked
``_officer_chars`` / db helpers (same pattern as test_item_watch_routes.py).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.db import init_db
from backend.server.db import raid_schedule as rs

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


# ---------------------------------------------------------------------------
# DB layer (temp users.db)
# ---------------------------------------------------------------------------


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)  # creates raid_teams / raid_slots (+ asserts schema)
    return db


async def test_replace_then_get_round_trips(users_db):
    teams = [
        {
            "name": "Team 1",
            "primary_tz": "America/New_York",
            "twitch_login": "foochan",
            "raids": [{"days": "2,4", "start_min": 1200, "end_min": 1380, "label": "Prog"}],
        }
    ]
    await rs.replace_schedule("Varsoon", "Exordium", teams, "disc1", path=users_db)
    got = await rs.get_schedule("Varsoon", "Exordium", path=users_db)
    assert len(got) == 1
    assert got[0]["name"] == "Team 1"
    assert got[0]["twitch_login"] == "foochan"
    assert got[0]["raids"][0]["days"] == "2,4"
    assert got[0]["raids"][0]["start_min"] == 1200


async def test_replace_is_a_full_replace_and_scoped(users_db):
    await rs.replace_schedule(
        "Varsoon",
        "Exordium",
        [{"name": "T", "primary_tz": "UTC", "twitch_login": None, "raids": []}],
        "disc1",
        path=users_db,
    )
    # A different guild is untouched by Exordium's replace.
    await rs.replace_schedule(
        "Varsoon",
        "Other",
        [{"name": "O", "primary_tz": "UTC", "twitch_login": "otherchan", "raids": []}],
        "disc1",
        path=users_db,
    )
    # Replacing Exordium with an empty schedule clears its rows only.
    await rs.replace_schedule("Varsoon", "Exordium", [], "disc1", path=users_db)
    assert await rs.get_schedule("Varsoon", "Exordium", path=users_db) == []
    assert len(await rs.get_schedule("Varsoon", "Other", path=users_db)) == 1
    # list_all_teams_with_twitch spans guilds/worlds, twitch-only.
    with_tw = await rs.list_all_teams_with_twitch(path=users_db)
    assert {t["twitch_login"] for t in with_tw} == {"otherchan"}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    payload = base64.b64encode(json.dumps({"user": user}).encode()).decode()
    signed = itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()
    return {"session": signed}


_OFFICER = {"id": "officer-1", "username": "officer"}


def _valid_body() -> dict:
    return {
        "teams": [
            {
                "name": "Main",
                "primary_tz": "America/New_York",
                "twitch_url": "https://twitch.tv/mychannel",
                "raids": [{"days": [2, 4], "start": "20:00", "end": "23:00", "label": "Prog"}],
            }
        ]
    }


async def _put(app, body, *, officer=True, cookies=True):
    officer_ret = {"sihtric"} if officer else set()
    with (
        patch("backend.server.api.raid_schedule._officer_chars", new=AsyncMock(return_value=officer_ret)),
        patch("backend.server.api.raid_schedule.replace_schedule", new=AsyncMock()) as rep,
        patch("backend.server.api.raid_schedule.get_schedule", new=AsyncMock(return_value=[])),
        patch("backend.server.api.raid_schedule.audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/guild/Exordium/raid-schedule",
                json=body,
                cookies=_cookies(_OFFICER) if cookies else {},
            )
    return r, rep, audit


async def test_get_is_public(app):
    with patch("backend.server.api.raid_schedule.get_schedule", new=AsyncMock(return_value=[])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium/raid-schedule")
    assert r.status_code == 200
    assert r.json() == {"teams": []}


async def test_put_requires_auth(app):
    r, _, _ = await _put(app, _valid_body(), cookies=False)
    assert r.status_code == 401


async def test_put_requires_officer(app):
    r, _, _ = await _put(app, _valid_body(), officer=False)
    assert r.status_code == 403


async def test_put_officer_saves_and_converts(app):
    r, rep, _ = await _put(app, _valid_body())
    assert r.status_code == 200
    rep.assert_awaited_once()
    saved_teams = rep.await_args.args[2]  # (world, guild, teams, ...)
    team = saved_teams[0]
    assert team["twitch_login"] == "mychannel"  # url → login
    assert team["raids"][0]["start_min"] == 1200  # "20:00" → minutes
    assert team["raids"][0]["days"] == "2,4"


async def test_put_rejects_too_many_teams(app):
    body = {"teams": [{"name": f"T{i}", "primary_tz": "UTC", "raids": []} for i in range(5)]}
    r, rep, _ = await _put(app, body)
    assert r.status_code == 400
    rep.assert_not_awaited()


async def test_put_rejects_too_many_raids(app):
    raids = [{"days": [1], "start": "20:00", "end": "21:00"} for _ in range(5)]
    body = {"teams": [{"name": "T", "primary_tz": "UTC", "raids": raids}]}
    r, _, _ = await _put(app, body)
    assert r.status_code == 400


async def test_put_rejects_over_5_hours(app):
    body = {"teams": [{"name": "T", "primary_tz": "UTC", "raids": [{"days": [1], "start": "18:00", "end": "23:30"}]}]}
    r, _, _ = await _put(app, body)
    assert r.status_code == 400


async def test_put_rejects_bad_timezone(app):
    body = {"teams": [{"name": "T", "primary_tz": "Mars/Olympus", "raids": []}]}
    r, _, _ = await _put(app, body)
    assert r.status_code == 400


async def test_put_rejects_non_twitch_url(app):
    body = _valid_body()
    body["teams"][0]["twitch_url"] = "https://youtube.com/x"
    r, rep, _ = await _put(app, body)
    assert r.status_code == 400
    rep.assert_not_awaited()


async def test_put_rejects_blocklisted_twitch_and_reports(app):
    body = _valid_body()
    body["teams"][0]["twitch_url"] = "https://twitch.tv/pornstreamer"
    r, rep, audit = await _put(app, body)
    assert r.status_code == 400
    rep.assert_not_awaited()
    audit.assert_called_once()
    assert audit.call_args.args[0] == "suspicious_twitch_url"


async def test_put_rejects_blocklisted_team_name_and_reports(app):
    body = _valid_body()
    body["teams"][0]["name"] = "P0rn Squad"  # leetspeak evasion
    r, rep, audit = await _put(app, body)
    assert r.status_code == 400
    rep.assert_not_awaited()
    audit.assert_called_once()
    assert audit.call_args.args[0] == "suspicious_raid_text"
    assert audit.call_args.kwargs["field"] == "team_name"


async def test_put_rejects_blocklisted_raid_label_and_reports(app):
    body = _valid_body()
    body["teams"][0]["raids"][0]["label"] = "f a g g o t"  # spacing evasion
    r, rep, audit = await _put(app, body)
    assert r.status_code == 400
    rep.assert_not_awaited()
    audit.assert_called_once()
    assert audit.call_args.args[0] == "suspicious_raid_text"
    assert audit.call_args.kwargs["field"] == "raid_label"
