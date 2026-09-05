"""Raid-attendance tests — store merge semantics, category derivation, routes.

Store tests run against a temp users.db (stores re-pointed by the autouse
fixture, same pattern as test_raid_planning.py). The ingest route reuses the
HMAC signing helpers from tests/server/_parses_ingest_fixtures.py so the
signature contract stays pinned by one source of truth; guild resolution and
the schedule probe are patched in the attendance module's namespace (the
route imports them by name).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.attendance import derive_categories, session_counts
from backend.server.db import init_db
from backend.server.db.attendance import (
    MAX_SESSION_SPAN_S,
    MERGE_GAP_S,
    session_day_for,
)
from backend.server.db.attendance import store as attendance_db
from tests.fixtures.users_db import point_users_db_at
from tests.server._parses_ingest_fixtures import _fake_require_user, _signed_post_kwargs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)


@pytest.fixture(autouse=True)
def _grant_subscriber(users_db: Path):
    """Attendance is in limited preview behind the 'subscriber' role — grant
    it to the identities the route tests act as (the HMAC fixture's token
    user + the guild-view session user). The gate's own tests use other
    ids."""
    import sqlite3

    with sqlite3.connect(users_db) as conn:
        for did in ("discord-123", "member-1"):
            conn.execute(
                "INSERT OR IGNORE INTO user_roles (discord_id, role, granted_by) VALUES (?, 'subscriber', 'test')",
                (did,),
            )


_WORLD = "Varsoon"
_GUILD = "Exordium"

# A fixed raid evening: 2026-07-25 19:00 local ≈ unix anchor. Absolute value
# only matters for session_day_for assertions.
T0 = 1_784_500_000


def _member(name: str, first: int, last: int) -> dict:
    return {"name": name, "first_seen": first, "last_seen": last}


async def _snapshot(uploader: str, raid: list[dict], online: list[dict] | None = None, **kw) -> dict:
    defaults = dict(
        world=_WORLD,
        guild_name=_GUILD,
        discord_id=uploader,
        sent_at=T0,
        raid_members=raid,
        online_guildies=online or [],
        zones=kw.pop("zones", []),
        scheduled=kw.pop("scheduled", False),
        team_index=kw.pop("team_index", None),
    )
    defaults.update(kw)
    return await attendance_db.apply_snapshot(**defaults)


# ---------------------------------------------------------------------------
# session_day_for — evening rollover
# ---------------------------------------------------------------------------


def test_session_day_rollover():
    # 01:00 UTC still belongs to the previous evening's raid night.
    import datetime as dt

    one_am = int(dt.datetime(2026, 9, 2, 1, 0, tzinfo=dt.UTC).timestamp())
    eight_am = int(dt.datetime(2026, 9, 2, 8, 0, tzinfo=dt.UTC).timestamp())
    assert session_day_for(one_am) == "2026-09-01"
    assert session_day_for(eight_am) == "2026-09-02"


# ---------------------------------------------------------------------------
# Store — apply_snapshot merge semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_then_read_back():
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)], [_member("Benchy", T0, T0 + 300)], zones=["VP"])
    assert res["merged"] is False
    obs = await attendance_db.observations_for_session(res["session_id"])
    assert {(o["character_name"], o["kind"]) for o in obs} == {("Tanky", "raid"), ("Benchy", "online")}
    session = await attendance_db.get_session(res["session_id"])
    assert session is not None
    assert json.loads(session["zones"]) == ["VP"]
    assert session["started_at"] == T0 and session["ended_at"] == T0 + 600


@pytest.mark.asyncio
async def test_two_uploaders_merge_commutatively():
    """Overlapping snapshots fold into ONE session with min/max windows —
    in either arrival order."""
    snap_a = dict(raid=[_member("Tanky", T0, T0 + 3600)], online=[], zones=["VP"])
    snap_b = dict(raid=[_member("Tanky", T0 + 1800, T0 + 7200)], online=[], zones=["Chardok"])

    results = {}
    for order in ("ab", "ba"):
        # Fresh DB per order: re-point the store at a throwaway file.
        for first, second in ((snap_a, snap_b),) if order == "ab" else ((snap_b, snap_a),):
            r1 = await _snapshot("u1", first["raid"], zones=first["zones"])
            r2 = await _snapshot("u2", second["raid"], zones=second["zones"])
            assert r2["session_id"] == r1["session_id"]
            assert r2["merged"] is True
            session = await attendance_db.get_session(r1["session_id"])
            obs = await attendance_db.observations_for_session(r1["session_id"])
            results[order] = (
                session["started_at"],
                session["ended_at"],
                json.loads(session["zones"]),
                sorted(json.loads(session["uploaders"])),
                [(o["character_name"], o["first_seen"], o["last_seen"]) for o in obs],
            )
            await attendance_db.delete_session(r1["session_id"])

    assert results["ab"] == results["ba"]
    started, ended, zones, uploaders, obs = results["ab"]
    assert (started, ended) == (T0, T0 + 7200)
    assert zones == ["Chardok", "VP"]
    assert uploaders == ["u1", "u2"]
    assert obs == [("Tanky", T0, T0 + 7200)]


@pytest.mark.asyncio
async def test_double_header_gets_new_session_and_seq():
    """A snapshot starting more than MERGE_GAP after the first session ended
    is a second session on the same day with seq=1."""
    r1 = await _snapshot("u1", [_member("Tanky", T0, T0 + 3600)])
    later = T0 + 3600 + MERGE_GAP_S + 60
    r2 = await _snapshot("u1", [_member("Tanky", later, later + 3600)])
    assert r2["session_id"] != r1["session_id"]
    assert r2["merged"] is False
    s1 = await attendance_db.get_session(r1["session_id"])
    s2 = await attendance_db.get_session(r2["session_id"])
    if s1["session_day"] == s2["session_day"]:
        assert (s1["seq"], s2["seq"]) == (0, 1)


@pytest.mark.asyncio
async def test_max_span_guard_forces_new_session():
    """A snapshot that would stretch the merged session past MAX span starts
    a new session even though it overlaps within the gap."""
    r1 = await _snapshot("u1", [_member("Tanky", T0, T0 + 15 * 3600)])
    near_end = T0 + 15 * 3600 + 600  # inside the merge gap...
    r2 = await _snapshot("u2", [_member("Tanky", near_end, T0 + MAX_SESSION_SPAN_S + 3600)])  # ...but too long
    assert r2["session_id"] != r1["session_id"]


@pytest.mark.asyncio
async def test_scheduled_flag_sticks_once_set():
    """scheduled merges via MAX — a later unscheduled snapshot never clears
    it; team_index keeps the first non-null value."""
    r1 = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)], scheduled=True, team_index=1)
    await _snapshot("u2", [_member("Healy", T0 + 300, T0 + 900)], scheduled=False, team_index=None)
    session = await attendance_db.get_session(r1["session_id"])
    assert session["scheduled"] == 1
    assert session["team_index"] == 1


@pytest.mark.asyncio
async def test_delete_session_removes_observations():
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    assert await attendance_db.delete_session(res["session_id"]) is True
    assert await attendance_db.get_session(res["session_id"]) is None
    assert await attendance_db.observations_for_session(res["session_id"]) == []
    assert await attendance_db.delete_session(res["session_id"]) is False


@pytest.mark.asyncio
async def test_list_sessions_newest_first_with_keyset():
    ids = []
    t = T0
    for _ in range(3):
        ids.append((await _snapshot("u1", [_member("Tanky", t, t + 600)]))["session_id"])
        t += MERGE_GAP_S + 3600
    rows = await attendance_db.list_sessions(_WORLD, _GUILD, limit=2)
    assert [r["id"] for r in rows] == [ids[2], ids[1]]
    rows = await attendance_db.list_sessions(_WORLD, _GUILD, limit=2, before_id=ids[1])
    assert [r["id"] for r in rows] == [ids[0]]


# ---------------------------------------------------------------------------
# Derivation — categories, precedence, rollup
# ---------------------------------------------------------------------------


def _obs(name: str, kind: str, first: int = T0, last: int = T0 + 600) -> dict:
    return {"session_id": 1, "character_name": name, "kind": kind, "first_seen": first, "last_seen": last}


def _cats(char_rows: list[dict]) -> dict[str, str]:
    return {r["name"]: r["category"] for r in char_rows}


def test_derivation_all_categories():
    obs = [
        _obs("Tanky", "raid"),  # raider in raid → present
        _obs("Pugsy", "raid"),  # unrostered pug → present, role None
        _obs("Benchy", "online"),  # raider online only → sat_out
        _obs("Randomer", "online"),  # unrostered online → absent (never sat_out)
    ]
    roles = {"tanky": "raider", "benchy": "raider", "afky": "raider", "ghosty": "raider", "alty": "raid_alt"}
    claims = {"afky": "u-afk"}
    afk_by_user = {"u-afk": "afk"}
    char_rows, _ = derive_categories(obs, roles, claims, afk_by_user, scheduled=True)
    cats = _cats(char_rows)
    assert cats["Tanky"] == "present"
    assert cats["Pugsy"] == "present"
    assert cats["Benchy"] == "sat_out"
    assert cats["Randomer"] == "absent"
    assert cats["Afky"] == "afk"  # rostered, declared afk, absent
    assert cats["Ghosty"] == "awol"  # raider, scheduled, absent, undeclared
    assert cats["Alty"] == "absent"  # raid alts are never AWOL
    role_by_name = {r["name"]: r["role"] for r in char_rows}
    assert role_by_name["Pugsy"] is None


def test_derivation_unscheduled_never_awol():
    char_rows, _ = derive_categories([], {"ghosty": "raider"}, {}, {}, scheduled=False)
    assert _cats(char_rows)["Ghosty"] == "absent"


def test_derivation_observed_beats_declared():
    """Declared AFK but showed up = present; declared AFK but online = sat_out."""
    obs = [_obs("Showedup", "raid"), _obs("Lurky", "online")]
    roles = {"showedup": "raider", "lurky": "raider"}
    claims = {"showedup": "u1", "lurky": "u2"}
    afk = {"u1": "afk", "u2": "afk"}
    cats = _cats(derive_categories(obs, roles, claims, afk, scheduled=True)[0])
    assert cats["Showedup"] == "present"
    assert cats["Lurky"] == "sat_out"


def test_derivation_alt_credits_owner():
    """A raid alt in the raid rolls the owner up to present even though the
    main was AWOL-shaped."""
    obs = [_obs("Alty", "raid")]
    roles = {"mainy": "raider", "alty": "raid_alt"}
    claims = {"mainy": "u1", "alty": "u1"}
    char_rows, user_rows = derive_categories(obs, roles, claims, {}, scheduled=True)
    cats = _cats(char_rows)
    assert cats["Mainy"] == "awol"  # the character row stays honest
    assert cats["Alty"] == "present"
    assert len(user_rows) == 1
    assert user_rows[0]["category"] == "present"  # the PLAYER attended
    assert sorted(user_rows[0]["characters"]) == ["Alty", "Mainy"]


def test_derivation_case_insensitive_join():
    """Roles/claims key lowercase; observations carry display casing."""
    obs = [_obs("TANKY", "raid")]
    cats = _cats(derive_categories(obs, {"tanky": "raider"}, {}, {}, scheduled=True)[0])
    assert cats == {"TANKY": "present"}


def test_session_counts_shape():
    obs = [_obs("A", "raid"), _obs("B", "online")]
    roles = {"b": "raider", "c": "raider"}
    char_rows, _ = derive_categories(obs, roles, {}, {}, scheduled=True)
    assert session_counts(char_rows) == {"present": 1, "sat_out": 1, "afk": 0, "awol": 1}


# ---------------------------------------------------------------------------
# Ingest route — HMAC + validation + merge
# ---------------------------------------------------------------------------


# Ingest timestamps must sit inside the route's clock-skew clamp
# [now-36h, now+1h], so the route tests anchor near real "now" (the store
# tests above keep the fixed T0 — the clamp lives in the route only).
_B = int(time.time()) - 1800


def _ingest_payload(**overrides) -> dict:
    payload = {
        "logger_name": "Menludiir",
        "logger_server": _WORLD,
        "sent_at": _B + 600,
        "raid_members": [
            {"name": "Tanky", "first_seen": _B, "last_seen": _B + 600},
            {"name": "Healy", "first_seen": _B, "last_seen": _B + 600},
        ],
        "online_guildies": [{"name": "Benchy", "first_seen": _B, "last_seen": _B + 600}],
        "zones": ["Veeshan's Peak"],
    }
    payload.update(overrides)
    return payload


def _ingest_patches(guild: str | object = _GUILD):
    return (
        patch("backend.server.api.attendance.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.attendance._resolve_uploader_guild_async", new=AsyncMock(return_value=guild)),
        patch("backend.server.api.attendance.schedule_db.get_schedule", new=AsyncMock(return_value=[])),
    )


async def _post_ingest(app, payload: dict, token: str = "eq2c_test_token"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post("/api/attendance/ingest", **_signed_post_kwargs(payload, token))


@pytest.mark.asyncio
async def test_ingest_happy_path_creates_session(app):
    p1, p2, p3 = _ingest_patches()
    with p1, p2, p3:
        res = await _post_ingest(app, _ingest_payload())
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "created"
    assert body["raid_members"] == 2 and body["online_guildies"] == 1
    obs = await attendance_db.observations_for_session(body["session_id"])
    assert {(o["character_name"], o["kind"]) for o in obs} == {
        ("Tanky", "raid"),
        ("Healy", "raid"),
        ("Benchy", "online"),
    }


@pytest.mark.asyncio
async def test_ingest_second_uploader_merges(app):
    p1, p2, p3 = _ingest_patches()
    with p1, p2, p3:
        r1 = await _post_ingest(app, _ingest_payload())
        overlapping = _ingest_payload(
            logger_name="Otherofficer",
            raid_members=[{"name": "Tanky", "first_seen": _B + 300, "last_seen": _B + 900}],
            online_guildies=[],
        )
        r2 = await _post_ingest(app, overlapping)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r2.json()["status"] == "merged"
    assert r2.json()["session_id"] == r1.json()["session_id"]
    obs = await attendance_db.observations_for_session(r1.json()["session_id"])
    tanky = next(o for o in obs if o["character_name"] == "Tanky" and o["kind"] == "raid")
    assert (tanky["first_seen"], tanky["last_seen"]) == (_B, _B + 900)


@pytest.mark.asyncio
async def test_ingest_bad_hmac_rejected(app):
    p1, p2, p3 = _ingest_patches()
    with p1, p2, p3:
        kwargs = _signed_post_kwargs(_ingest_payload())
        kwargs["headers"]["X-Lexicon-Signature"] = "0" * 64
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post("/api/attendance/ingest", **kwargs)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_ingest_unknown_world_403(app):
    p1, p2, p3 = _ingest_patches()
    with p1, p2, p3:
        res = await _post_ingest(app, _ingest_payload(logger_server="Antonia Bayle"))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_ingest_census_down_503(app):
    from backend.server.api.attendance import CENSUS_UNAVAILABLE

    p1, p2, p3 = _ingest_patches(guild=CENSUS_UNAVAILABLE)
    with p1, p2, p3:
        res = await _post_ingest(app, _ingest_payload())
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_ingest_unguilded_403(app):
    p1, p2, p3 = _ingest_patches(guild=None)
    with p1, p2, p3:
        res = await _post_ingest(app, _ingest_payload())
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_ingest_garbage_members_dropped_not_400(app):
    payload = _ingest_payload(
        raid_members=[
            {"name": "Tanky", "first_seen": _B, "last_seen": _B + 600},
            {"name": "x' OR 1=1 --", "first_seen": _B, "last_seen": _B + 600},
            {"name": "Backwards", "first_seen": _B + 600, "last_seen": _B},  # inverted window
        ],
        online_guildies=[],
    )
    p1, p2, p3 = _ingest_patches()
    with p1, p2, p3:
        res = await _post_ingest(app, payload)
    assert res.status_code == 201
    assert res.json()["raid_members"] == 1


@pytest.mark.asyncio
async def test_ingest_all_garbage_400(app):
    payload = _ingest_payload(raid_members=[{"name": "!!", "first_seen": _B, "last_seen": _B}], online_guildies=[])
    p1, p2, p3 = _ingest_patches()
    with p1, p2, p3:
        res = await _post_ingest(app, payload)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_ingest_schedule_probe_freezes_flag(app):
    team = {"name": "Team 1", "primary_tz": "UTC", "raids": []}
    p1, p2, _ = _ingest_patches()
    with (
        p1,
        p2,
        patch("backend.server.api.attendance.schedule_db.get_schedule", new=AsyncMock(return_value=[team])),
        patch("backend.server.api.attendance.raid_live.team_scheduled_at", return_value=True),
    ):
        res = await _post_ingest(app, _ingest_payload())
    assert res.status_code == 201
    assert res.json()["scheduled"] is True
    session = await attendance_db.get_session(res.json()["session_id"])
    assert session["scheduled"] == 1 and session["team_index"] == 0


# ---------------------------------------------------------------------------
# Guild view routes
# ---------------------------------------------------------------------------

_VIEWER = {"id": "member-1", "username": "member"}


def _member_gate_patches(*, officer: bool = False):
    return (
        patch(
            "backend.server.api.attendance._require_member",
            new=AsyncMock(return_value=(_VIEWER, officer)),
        ),
        patch("backend.server.api.attendance._require_officer", new=AsyncMock(return_value=_VIEWER)),
    )


@pytest.mark.asyncio
async def test_list_route_returns_counts(app):
    await _snapshot("u1", [_member("Tanky", T0, T0 + 600)], [_member("Benchy", T0, T0 + 300)])
    p_member, _ = _member_gate_patches()
    with p_member:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.get(f"/api/guild/{_GUILD}/attendance")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_officer"] is False
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["counts"]["present"] == 1


@pytest.mark.asyncio
async def test_detail_route_rolls_up_users(app):
    from backend.server.db.raid_planning import store as planning_db

    await planning_db.set_role(_WORLD, _GUILD, "Tanky", "raider", updated_by="u1")
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    p_member, _ = _member_gate_patches()
    with p_member:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/guild/{_GUILD}/attendance/{res['session_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session"]["id"] == res["session_id"]
    assert _cats(body["characters"])["Tanky"] == "present"


@pytest.mark.asyncio
async def test_detail_404_for_wrong_guild(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    p_member, _ = _member_gate_patches()
    with p_member:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/guild/Othersguild/attendance/{res['session_id']}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_route_officer_only(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    _, p_officer = _member_gate_patches(officer=True)
    with p_officer:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/api/guild/{_GUILD}/attendance/{res['session_id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert await attendance_db.get_session(res["session_id"]) is None


# ---------------------------------------------------------------------------
# End-to-end: two uploaders, real roles/claims/availability, one merged
# session with correct categories (the plan's Phase-2 verification scenario)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_two_uploaders_full_derivation(app):
    from backend.server.db.availability import store as availability_store
    from backend.server.db.claims import store as claims_store
    from backend.server.db.raid_planning import store as planning_store

    # Roster: two raiders (one shows, one is AWOL), a raid alt that covers
    # for its owner's main, a bench-sitter, and a declared-AFK raider.
    for name, role in (
        ("Tanky", "raider"),
        ("Ghosty", "raider"),
        ("Mainy", "raider"),
        ("Alty", "raid_alt"),
        ("Benchy", "raider"),
        ("Afky", "raider"),
    ):
        await planning_store.set_role(_WORLD, _GUILD, name, role, updated_by="officer")
    for discord_id, char in (("u-main", "Mainy"), ("u-main", "Alty"), ("u-afk", "Afky")):
        claim = await claims_store.submit_claim(discord_id, char, world=_WORLD)
        await claims_store.review_claim(claim["id"], "approved", admin_id="admin")

    await availability_store.set_days("u-afk", {session_day_for(_B): "afk"})

    team = {"name": "Team 1", "primary_tz": "UTC", "raids": []}
    p1, p2, _ = _ingest_patches()
    with (
        p1,
        p2,
        patch("backend.server.api.attendance.schedule_db.get_schedule", new=AsyncMock(return_value=[team])),
        patch("backend.server.api.attendance.raid_live.team_scheduled_at", return_value=True),
    ):
        # Uploader A saw the early window; uploader B the late one.
        r1 = await _post_ingest(
            app,
            _ingest_payload(
                raid_members=[
                    {"name": "Tanky", "first_seen": _B, "last_seen": _B + 3600},
                    {"name": "Alty", "first_seen": _B, "last_seen": _B + 3600},
                ],
                online_guildies=[{"name": "Benchy", "first_seen": _B, "last_seen": _B + 3600}],
            ),
        )
        r2 = await _post_ingest(
            app,
            _ingest_payload(
                logger_name="Otherofficer",
                raid_members=[
                    {"name": "Tanky", "first_seen": _B + 1800, "last_seen": _B + 2700},
                    {"name": "Pugsy", "first_seen": _B + 1800, "last_seen": _B + 2700},
                ],
                online_guildies=[],
            ),
        )
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    assert r2.json()["status"] == "merged"
    sid = r1.json()["session_id"]
    assert r2.json()["session_id"] == sid

    p_member, _ = _member_gate_patches()
    with p_member:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            listing = (await c.get(f"/api/guild/{_GUILD}/attendance")).json()
            detail = (await c.get(f"/api/guild/{_GUILD}/attendance/{sid}")).json()

    assert len(listing["sessions"]) == 1  # ONE merged session
    assert listing["sessions"][0]["counts"] == {"present": 3, "sat_out": 1, "afk": 1, "awol": 2}

    cats = _cats(detail["characters"])
    assert cats == {
        "Tanky": "present",
        "Alty": "present",
        "Pugsy": "present",  # unrostered pug
        "Benchy": "sat_out",
        "Afky": "afk",
        "Ghosty": "awol",
        "Mainy": "awol",  # character row honest; the PLAYER rolls up below
    }
    users = {u["discord_id"]: u for u in detail["users"]}
    assert users["u-main"]["category"] == "present"  # alt credited the owner
    assert users["u-main"]["main"] == "Mainy"  # attribution names the raid main
    assert users["u-afk"]["category"] == "afk"
    assert users["u-afk"]["afk_declared"] is True

    # Merged window spans both uploads (uploader A saw Tanky longest).
    tanky = next(o for o in detail["characters"] if o["name"] == "Tanky")
    assert (tanky["first_seen"], tanky["last_seen"]) == (_B, _B + 3600)


# ---------------------------------------------------------------------------
# Raid-main resolution (best-effort DKP/attendance attribution to the main)
# ---------------------------------------------------------------------------


def _role_rows(**names_to_roles: str) -> list[dict]:
    return [{"character_name": n, "role": r} for n, r in names_to_roles.items()]


def test_resolve_mains_basics():
    from backend.server.attendance import resolve_mains

    rows = _role_rows(Mainy="raider", Alty="raid_alt", Tanky="raider", Strayalt="raid_alt")
    # "boxling" is claimed by u1 but NOT rostered (second-account dual-box) --
    # it must still collapse onto u1's main so DKP can't double-dip.
    claims = {"mainy": "u1", "alty": "u1", "boxling": "u1", "tanky": "u2"}
    user_mains, char_mains = resolve_mains(rows, claims, primaries=set())
    assert user_mains == {"u1": "Mainy", "u2": "Tanky"}
    assert char_mains == {
        "Mainy": "Mainy",  # raider → self
        "Tanky": "Tanky",
        "Alty": "Mainy",  # alt → owner's main
        "Strayalt": "Strayalt",  # unclaimed alt → self (best effort)
        "Boxling": "Mainy",  # claimed-but-unrostered → owner's main
    }


def test_resolve_mains_prefers_primary_claim():
    from backend.server.attendance import resolve_mains

    rows = _role_rows(Aaa="raider", Zzz="raider")
    claims = {"aaa": "u1", "zzz": "u1"}
    # Without a primary flag, alphabetical wins; with one, the primary wins.
    assert resolve_mains(rows, claims, set())[0] == {"u1": "Aaa"}
    assert resolve_mains(rows, claims, {"zzz"})[0] == {"u1": "Zzz"}


def test_resolve_mains_pure_alt_player_has_no_main():
    from backend.server.attendance import resolve_mains

    rows = _role_rows(Alty="raid_alt")
    user_mains, char_mains = resolve_mains(rows, {"alty": "u1"}, set())
    assert user_mains == {}
    assert char_mains == {"Alty": "Alty"}


@pytest.mark.asyncio
async def test_mains_endpoint_returns_substitution_table(app):
    from backend.server.db.claims import store as claims_store
    from backend.server.db.raid_planning import store as planning_store

    for name, role in (("Mainy", "raider"), ("Alty", "raid_alt")):
        await planning_store.set_role(_WORLD, _GUILD, name, role, updated_by="officer")
    for char in ("Mainy", "Alty"):
        claim = await claims_store.submit_claim("u-main", char, world=_WORLD)
        await claims_store.review_claim(claim["id"], "approved", admin_id="admin")

    p1, p2, _ = _ingest_patches()
    with p1, p2:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.get(
                "/api/attendance/mains",
                params={"character": "Menludiir", "server": _WORLD},
                headers={"Authorization": "Bearer eq2c_test_token"},
            )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["guild"] == _GUILD
    assert body["mains"] == {"Mainy": "Mainy", "Alty": "Mainy"}


@pytest.mark.asyncio
async def test_mains_endpoint_unguilded_403(app):
    p1, p2, _ = _ingest_patches(guild=None)
    with p1, p2:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.get(
                "/api/attendance/mains",
                params={"character": "Menludiir", "server": _WORLD},
                headers={"Authorization": "Bearer eq2c_test_token"},
            )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Subscriber preview gate
# ---------------------------------------------------------------------------


async def _fake_require_nonsub(request):
    return {"id": "nonsub-999", "username": "outsider", "auth_source": "token"}


@pytest.mark.asyncio
async def test_ingest_requires_subscriber_role(app):
    _, p2, p3 = _ingest_patches()
    with patch("backend.server.api.attendance.require_user_session_or_token", _fake_require_nonsub), p2, p3:
        res = await _post_ingest(app, _ingest_payload())
    assert res.status_code == 403
    assert "preview" in res.json()["detail"]


@pytest.mark.asyncio
async def test_mains_requires_subscriber_role(app):
    _, p2, p3 = _ingest_patches()
    with patch("backend.server.api.attendance.require_user_session_or_token", _fake_require_nonsub), p2, p3:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.get(
                "/api/attendance/mains",
                params={"character": "Menludiir", "server": _WORLD},
                headers={"Authorization": "Bearer eq2c_test_token"},
            )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_guild_views_require_subscriber_role(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    nonsub = {"id": "nonsub-999", "username": "outsider"}
    with patch(
        "backend.server.api.attendance._require_member",
        new=AsyncMock(return_value=(nonsub, False)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            listing = await c.get(f"/api/guild/{_GUILD}/attendance")
            detail = await c.get(f"/api/guild/{_GUILD}/attendance/{res['session_id']}")
    assert listing.status_code == 403
    assert detail.status_code == 403


# ---------------------------------------------------------------------------
# Phase 3 — voice cross-check: live-session probe, voice recording, in_voice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_live_session_window_edges():
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 3600)])
    sid = res["session_id"]
    # During the window, and within the merge gap after it → live.
    assert (await attendance_db.find_live_session(_WORLD, _GUILD, T0 + 1800))["id"] == sid
    assert (await attendance_db.find_live_session(_WORLD, _GUILD, T0 + 3600 + MERGE_GAP_S - 60))["id"] == sid
    # Beyond the gap → nothing live.
    assert await attendance_db.find_live_session(_WORLD, _GUILD, T0 + 3600 + MERGE_GAP_S + 60) is None
    # Other guild / world → nothing.
    assert await attendance_db.find_live_session(_WORLD, "Otherguild", T0 + 1800) is None
    assert (
        await attendance_db.find_live_session("Varsoon" if _WORLD != "Varsoon" else "Wuoshi", _GUILD, T0 + 1800) is None
    )
    # Case-insensitive keys (belt-and-braces).
    assert (await attendance_db.find_live_session(_WORLD.upper(), _GUILD.lower(), T0 + 1800))["id"] == sid


@pytest.mark.asyncio
async def test_record_voice_upserts_commutatively():
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 3600)])
    sid = res["session_id"]
    await attendance_db.record_voice(sid, ["111", "222"], T0 + 100)
    await attendance_db.record_voice(sid, ["111"], T0 + 700)  # later tick extends last_seen
    await attendance_db.record_voice(sid, [], T0 + 800)  # empty tick is a no-op
    obs = await attendance_db.observations_for_session(sid)
    voice = {o["character_name"]: o for o in obs if o["kind"] == "voice"}
    assert set(voice) == {"111", "222"}
    assert (voice["111"]["first_seen"], voice["111"]["last_seen"]) == (T0 + 100, T0 + 700)
    assert (voice["222"]["first_seen"], voice["222"]["last_seen"]) == (T0 + 100, T0 + 100)


def test_derivation_in_voice_flags_users_not_characters():
    obs = [
        _obs("Tanky", "raid"),
        _obs("Ghosty", "online"),
        # voice rows carry DISCORD IDS in character_name
        {"session_id": 1, "character_name": "u-present", "kind": "voice", "first_seen": T0, "last_seen": T0 + 600},
        {"session_id": 1, "character_name": "u-awol", "kind": "voice", "first_seen": T0, "last_seen": T0 + 600},
        {"session_id": 1, "character_name": "u-stranger", "kind": "voice", "first_seen": T0, "last_seen": T0},
    ]
    roles = {"tanky": "raider", "ghosty": "raider", "awoly": "raider"}
    claims = {"tanky": "u-present", "ghosty": "u-bench", "awoly": "u-awol"}
    char_rows, user_rows = derive_categories(obs, roles, claims, {}, scheduled=True)

    # Voice ids never appear as characters.
    assert not any(r["name"] in ("u-present", "u-awol", "u-stranger") for r in char_rows)

    users = {u["discord_id"]: u for u in user_rows}
    assert users["u-present"]["category"] == "present" and users["u-present"]["in_voice"] is True
    assert users["u-bench"]["in_voice"] is False
    # THE Phase-3 payoff: AWOL in game but sitting in voice.
    assert users["u-awol"]["category"] == "awol" and users["u-awol"]["in_voice"] is True
    # A voice id with no claimed characters produces no user row at all.
    assert "u-stranger" not in users


@pytest.mark.asyncio
async def test_detail_route_carries_in_voice(app):
    from backend.server.db.raid_planning import store as planning_store

    await planning_store.set_role(_WORLD, _GUILD, "Tanky", "raider", updated_by="u1")
    from backend.server.db.claims import store as claims_store

    claim = await claims_store.submit_claim("u-voice", "Tanky", world=_WORLD)
    await claims_store.review_claim(claim["id"], "approved", admin_id="admin")

    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    await attendance_db.record_voice(res["session_id"], ["u-voice"], T0 + 100)

    p_member, _ = _member_gate_patches()
    with p_member:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            detail = (await c.get(f"/api/guild/{_GUILD}/attendance/{res['session_id']}")).json()
    users = {u["discord_id"]: u for u in detail["users"]}
    assert users["u-voice"]["in_voice"] is True


@pytest.mark.asyncio
async def test_delete_session_removes_voice_rows_too():
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    await attendance_db.record_voice(res["session_id"], ["111"], T0 + 100)
    await attendance_db.delete_session(res["session_id"])
    assert await attendance_db.observations_for_session(res["session_id"]) == []


# ---------------------------------------------------------------------------
# Officer corrections — override layer + row removal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_store_roundtrip():
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    sid = res["session_id"]
    await attendance_db.set_override(sid, "Tanky", "afk", set_by="officer-1")
    await attendance_db.set_override(sid, "Tanky", "sat_out", set_by="officer-2")  # upsert replaces
    ovs = await attendance_db.overrides_for_session(sid)
    assert ovs["tanky"]["category"] == "sat_out"
    assert ovs["tanky"]["set_by"] == "officer-2"
    assert await attendance_db.clear_override(sid, "TANKY") is True  # case-insensitive
    assert await attendance_db.overrides_for_session(sid) == {}
    assert await attendance_db.clear_override(sid, "Tanky") is False


def test_derivation_override_beats_everything():
    obs = [_obs("Tanky", "raid")]
    roles = {"tanky": "raider", "ghosty": "raider"}
    overrides = {
        "tanky": {"character_name": "Tanky", "category": "absent"},  # officer says he wasn't there
        "ghosty": {"character_name": "Ghosty", "category": "present"},  # officer adds a missed raider
        "handadded": {"character_name": "Handadded", "category": "present"},  # never observed, unrostered
    }
    char_rows, _ = derive_categories(obs, roles, {}, {}, scheduled=True, overrides=overrides)
    cats = _cats(char_rows)
    assert cats["Tanky"] == "absent"
    assert cats["Ghosty"] == "present"  # would have derived awol
    assert cats["Handadded"] == "present"  # joined the universe via the override
    flags = {r["name"]: r["overridden"] for r in char_rows}
    assert flags == {"Tanky": True, "Ghosty": True, "Handadded": True}
    # Counts follow the corrected categories.
    assert session_counts(char_rows)["present"] == 2


def test_derivation_without_overrides_marks_rows_unoverridden():
    char_rows, _ = derive_categories([_obs("Tanky", "raid")], {}, {}, {}, scheduled=False)
    assert char_rows[0]["overridden"] is False


@pytest.mark.asyncio
async def test_override_route_set_and_clear(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    sid = res["session_id"]
    _, p_officer = _member_gate_patches(officer=True)
    with p_officer:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.put(
                f"/api/guild/{_GUILD}/attendance/{sid}/override",
                json={"character_name": "ghosty", "category": "present"},
            )
            r_bad = await c.put(
                f"/api/guild/{_GUILD}/attendance/{sid}/override",
                json={"character_name": "Ghosty", "category": "vanished"},
            )
            r2 = await c.put(
                f"/api/guild/{_GUILD}/attendance/{sid}/override",
                json={"character_name": "Ghosty", "category": None},
            )
    assert r1.status_code == 200 and r1.json() == {"ok": True, "cleared": False}
    assert r_bad.status_code == 400
    assert r2.status_code == 200 and r2.json()["cleared"] is True
    assert await attendance_db.overrides_for_session(sid) == {}


@pytest.mark.asyncio
async def test_override_appears_in_detail_with_corrector_name(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    sid = res["session_id"]
    p_member, p_officer = _member_gate_patches(officer=True)
    with p_member, p_officer:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(
                f"/api/guild/{_GUILD}/attendance/{sid}/override",
                json={"character_name": "Tanky", "category": "afk"},
            )
            detail = (await c.get(f"/api/guild/{_GUILD}/attendance/{sid}")).json()
    tanky = next(r for r in detail["characters"] if r["name"] == "Tanky")
    assert tanky["category"] == "afk" and tanky["overridden"] is True
    assert tanky["override_by"]  # corrector id (or display name) present
    # List counts reflect the correction too.
    p_member, _ = _member_gate_patches()
    with p_member:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            listing = (await c.get(f"/api/guild/{_GUILD}/attendance")).json()
    assert listing["sessions"][0]["counts"] == {"present": 0, "sat_out": 0, "afk": 1, "awol": 0}


@pytest.mark.asyncio
async def test_remove_character_route(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600), _member("Junkname", T0, T0 + 60)])
    sid = res["session_id"]
    await attendance_db.set_override(sid, "Junkname", "present", set_by="o1")
    _, p_officer = _member_gate_patches(officer=True)
    with p_officer:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete(f"/api/guild/{_GUILD}/attendance/{sid}/character/Junkname")
    assert r.status_code == 200 and r.json()["removed"] is True
    obs = await attendance_db.observations_for_session(sid)
    assert {o["character_name"] for o in obs} == {"Tanky"}
    assert await attendance_db.overrides_for_session(sid) == {}


@pytest.mark.asyncio
async def test_correction_routes_are_officer_only(app):
    res = await _snapshot("u1", [_member("Tanky", T0, T0 + 600)])
    sid = res["session_id"]

    async def _not_officer(request, guild_name):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Officer access required")

    with patch("backend.server.api.attendance._require_officer", _not_officer):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.put(
                f"/api/guild/{_GUILD}/attendance/{sid}/override",
                json={"character_name": "Tanky", "category": "afk"},
            )
            r2 = await c.delete(f"/api/guild/{_GUILD}/attendance/{sid}/character/Tanky")
    assert r1.status_code == 403 and r2.status_code == 403
