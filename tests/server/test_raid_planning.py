"""Raid-planning DB layer + API tests.

DB layer runs against a temp users.db (stores re-pointed by the autouse
fixture). The API is tested with the ``app`` fixture, a signed session
cookie for auth, and mocked ``_officer_chars`` / ``_roster_rank_map`` /
roster helpers (same pattern as test_raid_schedule.py).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.db import init_db
from tests.fixtures.users_db import point_users_db_at

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


from backend.server.db.availability import store as availability_db  # noqa: E402
from backend.server.db.raid_planning import store as planning_db  # noqa: E402

_WORLD = "Varsoon"
_GUILD = "Exordium"


# ---------------------------------------------------------------------------
# DB layer — roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_roles_roundtrip():
    await planning_db.set_role(_WORLD, _GUILD, "Tanky", "raider", updated_by="u1")
    await planning_db.set_role(_WORLD, _GUILD, "Altchar", "raid_alt", updated_by="u1")
    roles = await planning_db.get_roles(_WORLD, _GUILD)
    assert {(r["character_name"], r["role"]) for r in roles} == {("Tanky", "raider"), ("Altchar", "raid_alt")}


@pytest.mark.asyncio
async def test_role_upsert_replaces():
    await planning_db.set_role(_WORLD, _GUILD, "Tanky", "raider", updated_by="u1")
    await planning_db.set_role(_WORLD, _GUILD, "Tanky", "raid_alt", updated_by="u2")
    roles = await planning_db.get_roles(_WORLD, _GUILD)
    assert roles[0]["role"] == "raid_alt"


@pytest.mark.asyncio
async def test_clearing_role_removes_placements_everywhere():
    await planning_db.set_role(_WORLD, _GUILD, "Tanky", "raider", updated_by="u1")
    await planning_db.replace_placements(
        _WORLD, _GUILD, 0, [{"character_name": "Tanky", "group_num": 1, "slot": 0, "sitout": False}], updated_by="u1"
    )
    await planning_db.replace_placements(
        _WORLD, _GUILD, 1, [{"character_name": "Tanky", "group_num": 2, "slot": 3, "sitout": False}], updated_by="u1"
    )
    await planning_db.set_role(_WORLD, _GUILD, "Tanky", None, updated_by="u1")
    assert await planning_db.get_roles(_WORLD, _GUILD) == []
    assert await planning_db.get_placements(_WORLD, _GUILD, 0) == []
    assert await planning_db.get_placements(_WORLD, _GUILD, 1) == []


@pytest.mark.asyncio
async def test_invalid_role_raises():
    with pytest.raises(ValueError):
        await planning_db.set_role(_WORLD, _GUILD, "Tanky", "bench_warmer", updated_by="u1")


# ---------------------------------------------------------------------------
# DB layer — placements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_placements_is_full_replace_and_team_scoped():
    p1 = [{"character_name": "A", "group_num": 1, "slot": 0, "sitout": False}]
    p2 = [{"character_name": "B", "group_num": None, "slot": None, "sitout": True}]
    await planning_db.replace_placements(_WORLD, _GUILD, 0, p1, updated_by="u1")
    await planning_db.replace_placements(_WORLD, _GUILD, 1, p2, updated_by="u1")
    # Replacing team 0 doesn't touch team 1.
    await planning_db.replace_placements(_WORLD, _GUILD, 0, [], updated_by="u1")
    assert await planning_db.get_placements(_WORLD, _GUILD, 0) == []
    got = await planning_db.get_placements(_WORLD, _GUILD, 1)
    assert got[0]["character_name"] == "B" and got[0]["sitout"] == 1


@pytest.mark.asyncio
async def test_prune_placements_beyond_drops_removed_teams():
    for ti in (0, 1, 2):
        await planning_db.replace_placements(
            _WORLD, _GUILD, ti, [{"character_name": f"C{ti}", "group_num": 1, "slot": 0, "sitout": False}], "u1"
        )
    await planning_db.prune_placements_beyond(_WORLD, _GUILD, 1)  # only team 0 remains
    assert await planning_db.get_placements(_WORLD, _GUILD, 0) != []
    assert await planning_db.get_placements(_WORLD, _GUILD, 1) == []
    assert await planning_db.get_placements(_WORLD, _GUILD, 2) == []


# ---------------------------------------------------------------------------
# DB layer — availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_availability_roundtrip_and_default_available():
    await availability_db.set_days("u1", {"2026-08-01": "afk", "2026-08-02": "tentative"})
    days = await availability_db.get_range("u1", "2026-08-01", "2026-08-31")
    assert days == {"2026-08-01": "afk", "2026-08-02": "tentative"}
    # Setting back to available renders as the default again (the stored
    # newest-wins clear row is filtered out of the calendar).
    await availability_db.set_days("u1", {"2026-08-01": "available"})
    days = await availability_db.get_range("u1", "2026-08-01", "2026-08-31")
    assert days == {"2026-08-02": "tentative"}


@pytest.mark.asyncio
async def test_statuses_for_day_across_users():
    await availability_db.set_days("u1", {"2026-08-01": "afk"})
    await availability_db.set_days("u2", {"2026-08-01": "tentative"})
    got = await availability_db.statuses_for_day("2026-08-01")
    assert got == {"u1": "afk", "u2": "tentative"}


# ---------------------------------------------------------------------------
# API — auth/session plumbing (mirrors test_raid_schedule.py)
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    import os

    from itsdangerous import TimestampSigner

    secret = os.environ.get("SESSION_SECRET", "test-secret")
    import base64
    import json as _json

    payload = base64.b64encode(_json.dumps({"user": user}).encode())
    signed = TimestampSigner(secret).sign(payload)
    return {"session": signed.decode()}


_USER = {"id": "member-1", "username": "member"}


def _mock_roster():
    mk = lambda n, c, lv: SimpleNamespace(name=n, cls=c, level=lv, rank_id=5)  # noqa: E731
    return [
        mk("Tanky", "Guardian", 70),
        mk("Healy", "Templar", 70),
        mk("Bardy", "Troubador", 70),
        mk("Chanty", "Illusionist", 70),
        mk("Alty", "Wizard", 65),
    ]


def _planner_patches(*, officer: bool, member: bool = True):
    roster = _mock_roster()
    rank_map = {m.name.lower(): 1 for m in roster}
    member_claims = {"approved": [{"character_name": "Tanky"}] if member else [], "pending": None}
    return (
        patch("backend.server.api.raid_planning._roster_rank_map", new=AsyncMock(return_value=rank_map)),
        patch("backend.server.api.raid_planning.get_active_claims", new=AsyncMock(return_value=member_claims)),
        patch(
            "backend.server.api.raid_planning._officer_chars",
            new=AsyncMock(return_value={"tanky"} if officer else set()),
        ),
        patch("backend.server.api.raid_planning._guild_roster", new=AsyncMock(return_value=roster)),
        patch(
            "backend.server.api.raid_planning.raid_schedule_db.get_schedule",
            new=AsyncMock(return_value=[{"id": 1, "name": "Team 1", "raids": []}]),
        ),
    )


async def _get(app, path, user=_USER):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path, cookies=_cookies(user))


async def _put(app, path, body, user=_USER):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.put(path, json=body, cookies=_cookies(user))


# ---------------------------------------------------------------------------
# API — visibility gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_requires_session(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/guild/{_GUILD}/raid-planning/0")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_planner_403_for_non_member(app):
    p = _planner_patches(officer=False, member=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_planner_member_gets_payload_not_officer(app):
    p = _planner_patches(officer=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0")
    assert r.status_code == 200
    body = r.json()
    assert body["is_officer"] is False
    assert {e["name"] for e in body["roster"]} == {"Tanky", "Healy", "Bardy", "Chanty", "Alty"}


@pytest.mark.asyncio
async def test_role_put_requires_officer(app):
    p = _planner_patches(officer=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(app, f"/api/guild/{_GUILD}/raid-planning/roles", {"character_name": "Tanky", "role": "raider"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_role_put_canonicalises_and_persists(app):
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(app, f"/api/guild/{_GUILD}/raid-planning/roles", {"character_name": "tAnKy", "role": "raider"})
    assert r.status_code == 200
    assert r.json()["character_name"] == "Tanky"  # canonical casing from roster
    roles = await planning_db.get_roles(_WORLD, _GUILD)
    assert roles[0]["character_name"] == "Tanky"


@pytest.mark.asyncio
async def test_role_put_rejects_non_member_character(app):
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(app, f"/api/guild/{_GUILD}/raid-planning/roles", {"character_name": "Nobody", "role": "raider"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# API — placement validation
# ---------------------------------------------------------------------------


async def _seed_raider(name="Tanky"):
    await planning_db.set_role(_WORLD, _GUILD, name, "raider", updated_by="u1")


@pytest.mark.asyncio
async def test_placements_rejects_unrostered_character(app):
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {"placements": [{"character_name": "Tanky", "group_num": 1, "slot": 0, "sitout": False}]},
        )
    assert r.status_code == 400
    assert "not on the raid roster" in r.json()["detail"]


@pytest.mark.asyncio
async def test_placements_rejects_double_booked_slot(app):
    await _seed_raider("Tanky")
    await _seed_raider("Healy")
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {
                "placements": [
                    {"character_name": "Tanky", "group_num": 1, "slot": 0, "sitout": False},
                    {"character_name": "Healy", "group_num": 1, "slot": 0, "sitout": False},
                ]
            },
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_placements_rejects_group_and_sitout(app):
    await _seed_raider("Tanky")
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {"placements": [{"character_name": "Tanky", "group_num": 1, "slot": 0, "sitout": True}]},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_placements_out_of_range_rejected(app):
    await _seed_raider("Tanky")
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {"placements": [{"character_name": "Tanky", "group_num": 5, "slot": 0, "sitout": False}]},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_placements_happy_path_roundtrip(app):
    await _seed_raider("Tanky")
    await _seed_raider("Healy")
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {
                "placements": [
                    {"character_name": "Tanky", "group_num": 1, "slot": 0, "sitout": False},
                    {"character_name": "Healy", "group_num": None, "slot": None, "sitout": True},
                ]
            },
        )
        assert r.status_code == 200
        g = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0")
    got = {p2["character_name"]: p2 for p2 in g.json()["placements"]}
    assert got["Tanky"]["group_num"] == 1
    assert got["Healy"]["sitout"] is True


# ---------------------------------------------------------------------------
# API — availability overlay in planner payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_overlays_availability_for_claimed_raider(app):
    from backend.server.db.claims import store as claims_db

    await _seed_raider("Tanky")
    # Approve a claim so Tanky maps to player member-1.
    from backend.server.db import upsert_user

    await upsert_user(discord_id="member-1", discord_name="Member One", discord_username="m1", avatar=None)
    claim = await claims_db.submit_claim("member-1", "Tanky", world=_WORLD)
    await claims_db.review_claim(claim["id"], "approved", "admin")
    today = dt.date.today().isoformat()
    await availability_db.set_days("member-1", {today: "afk"})

    p = _planner_patches(officer=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0?date={today}")
    body = r.json()
    assert body["availability"].get("tanky") == "afk"
    assert body["players"].get("tanky") == "Member One"


# ---------------------------------------------------------------------------
# API — /me/availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_my_availability_requires_session(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/me/availability")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_my_availability_is_raider_flag(app):
    from backend.server.db import upsert_user
    from backend.server.db.claims import store as claims_db

    await upsert_user(discord_id="member-1", discord_name="Member One", discord_username="m1", avatar=None)
    claim = await claims_db.submit_claim("member-1", "Tanky", world=_WORLD)
    await claims_db.review_claim(claim["id"], "approved", "admin")

    r = await _get(app, "/api/me/availability")
    assert r.status_code == 200
    assert r.json()["is_raider"] is False

    await _seed_raider("Tanky")
    r = await _get(app, "/api/me/availability")
    assert r.json()["is_raider"] is True


@pytest.mark.asyncio
async def test_put_availability_validates_window_and_status(app):
    today = dt.date.today()
    ok_day = (today + dt.timedelta(days=5)).isoformat()
    too_far = (today + dt.timedelta(days=200)).isoformat()

    r = await _put(app, "/api/me/availability", {"days": {ok_day: "afk"}})
    assert r.status_code == 200
    r = await _put(app, "/api/me/availability", {"days": {too_far: "afk"}})
    assert r.status_code == 400
    r = await _put(app, "/api/me/availability", {"days": {ok_day: "asleep"}})
    assert r.status_code == 400
    r = await _put(app, "/api/me/availability", {"days": {"not-a-date": "afk"}})
    assert r.status_code == 400

    # round-trip through GET
    r = await _get(app, "/api/me/availability")
    assert r.json()["days"] == {ok_day: "afk"}

    # back to available removes it
    r = await _put(app, "/api/me/availability", {"days": {ok_day: "available"}})
    r = await _get(app, "/api/me/availability")
    assert r.json()["days"] == {}


# ---------------------------------------------------------------------------
# API — admin-as-officer editing (admin must ALSO be a guild member)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_member_can_edit_like_officer(app, monkeypatch):
    """A site admin with a claimed character in the guild edits placements."""
    from backend.server import auth_deps

    monkeypatch.setattr(auth_deps, "ADMIN_IDS", frozenset({"member-1"}))
    await _seed_raider("Tanky")
    p = _planner_patches(officer=False)  # not an officer — admin path only
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {"placements": [{"character_name": "Tanky", "group_num": 1, "slot": 0, "sitout": False}]},
        )
        assert r.status_code == 200
        g = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0")
    assert g.json()["is_officer"] is True  # UI enables editing for the admin


@pytest.mark.asyncio
async def test_admin_who_is_not_member_still_403(app, monkeypatch):
    """Admin alone is not enough — the planner belongs to the guild."""
    from backend.server import auth_deps

    monkeypatch.setattr(auth_deps, "ADMIN_IDS", frozenset({"member-1"}))
    await _seed_raider("Tanky")
    p = _planner_patches(officer=False, member=False)  # no claim in this guild
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/0/placements",
            {"placements": []},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_planner_roster_includes_rank_fields(app):
    p = _planner_patches(officer=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0")
    entry = r.json()["roster"][0]
    assert "rank" in entry and "rank_id" in entry


# ---------------------------------------------------------------------------
# Placeholder raiders (census-hidden characters)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_placeholder_roundtrip_and_supersede():
    await planning_db.set_role(_WORLD, _GUILD, "Ghosty", "raider", updated_by="u1", placeholder=True, cls="Templar")
    row = (await planning_db.get_roles(_WORLD, _GUILD))[0]
    assert (row["placeholder"], row["cls"]) == (1, "Templar")
    # A later normal write supersedes the stand-in wholesale.
    await planning_db.set_role(_WORLD, _GUILD, "Ghosty", "raider", updated_by="u2")
    row = (await planning_db.get_roles(_WORLD, _GUILD))[0]
    assert (row["placeholder"], row["cls"]) == (0, None)


@pytest.mark.asyncio
async def test_role_put_placeholder_bypasses_roster_check(app):
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/roles",
            {"character_name": "ghosty", "role": "raider", "placeholder": True, "cls": "Templar"},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "character_name": "Ghosty", "role": "raider", "placeholder": True}
    row = (await planning_db.get_roles(_WORLD, _GUILD))[0]
    assert (row["character_name"], row["placeholder"], row["cls"]) == ("Ghosty", 1, "Templar")


@pytest.mark.asyncio
async def test_role_put_placeholder_requires_valid_class(app):
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/roles",
            {"character_name": "Ghosty", "role": "raider", "placeholder": True, "cls": "Flumph"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_role_put_placeholder_ignored_for_real_member(app):
    """If the name IS on the census roster the flag is dropped — the real
    record wins."""
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/roles",
            {"character_name": "tanky", "role": "raider", "placeholder": True, "cls": "Templar"},
        )
    assert r.status_code == 200
    assert r.json()["placeholder"] is False
    row = (await planning_db.get_roles(_WORLD, _GUILD))[0]
    assert (row["character_name"], row["placeholder"]) == ("Tanky", 0)


@pytest.mark.asyncio
async def test_role_put_clears_placeholder_row(app):
    await planning_db.set_role(_WORLD, _GUILD, "Ghosty", "raider", updated_by="u1", placeholder=True, cls="Templar")
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(app, f"/api/guild/{_GUILD}/raid-planning/roles", {"character_name": "Ghosty", "role": None})
    assert r.status_code == 200
    assert await planning_db.get_roles(_WORLD, _GUILD) == []


@pytest.mark.asyncio
async def test_planner_roster_appends_placeholder_until_unhidden(app):
    await planning_db.set_role(_WORLD, _GUILD, "Ghosty", "raider", updated_by="u1", placeholder=True, cls="Templar")
    await planning_db.set_role(_WORLD, _GUILD, "Tanky", "raider", updated_by="u1", placeholder=True, cls="Guardian")
    p = _planner_patches(officer=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0")
    assert r.status_code == 200
    roster = r.json()["roster"]
    ghosty = [e for e in roster if e["name"] == "Ghosty"]
    assert len(ghosty) == 1 and ghosty[0]["placeholder"] is True and ghosty[0]["cls"] == "Templar"
    # Tanky IS on the mock census roster — the real entry supersedes; no
    # synthetic duplicate appears.
    tanky = [e for e in roster if e["name"].lower() == "tanky"]
    assert len(tanky) == 1 and tanky[0]["placeholder"] is False


# ---------------------------------------------------------------------------
# Officer-set per-character availability (raiders without site accounts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_char_availability_store_roundtrip_and_world_scope():
    await availability_db.set_character_days(_WORLD, "Tanky", {"2026-08-01": "afk"}, set_by="officer-1")
    assert await availability_db.char_statuses_for_day(_WORLD, "2026-08-01") == {"tanky": "afk"}
    # World-scoped: another server's day is untouched.
    assert await availability_db.char_statuses_for_day("Wuoshi", "2026-08-01") == {}
    # "available" is STORED (an explicit newest-wins clear, so it can beat
    # an older player AFK in the planner merge) — case-insensitive upsert.
    await availability_db.set_character_days(_WORLD, "TANKY", {"2026-08-01": "available"}, set_by="officer-1")
    assert await availability_db.char_statuses_for_day(_WORLD, "2026-08-01") == {"tanky": "available"}
    timed = await availability_db.char_statuses_for_day_with_times(_WORLD, "2026-08-01")
    status, stamp = timed["tanky"]
    assert status == "available" and stamp > 0


@pytest.mark.asyncio
async def test_char_availability_put_requires_officer(app):
    import datetime as dt

    today = dt.date.today().isoformat()
    p = _planner_patches(officer=False)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/availability",
            {"character_name": "Tanky", "days": {today: "afk"}},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_char_availability_put_rejects_unrostered(app):
    import datetime as dt

    today = dt.date.today().isoformat()
    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/availability",
            {"character_name": "Nobody", "days": {today: "afk"}},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_char_availability_set_shows_in_planner_and_self_declaration_wins(app):
    """Officer marks a rostered raider AFK for today → the planner overlay
    carries it. If the player then declares their own status, the player's
    calendar wins over the officer entry."""
    import datetime as dt

    today = dt.date.today().isoformat()
    await _seed_raider("Tanky")

    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _put(
            app,
            f"/api/guild/{_GUILD}/raid-planning/availability",
            {"character_name": "Tanky", "days": {today: "afk"}},
        )
        assert r.status_code == 200, r.text
        planner = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0?date={today}")
    assert planner.json()["availability"] == {"tanky": "afk"}

    # The player (claimed owner of Tanky) declares tentative — wins.
    from backend.server.db.claims import store as claims_db

    claim = await claims_db.submit_claim(_USER["id"], "Tanky", world=_WORLD)
    await claims_db.review_claim(claim["id"], "approved", "admin")
    await availability_db.set_days(_USER["id"], {today: "tentative"})
    with p[0], p[1], p[2], p[3], p[4]:
        planner = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0?date={today}")
    assert planner.json()["availability"] == {"tanky": "tentative"}


def test_merge_availability_newest_wins():
    from backend.server.db.availability import merge_availability

    char_times = {"tanky": ("tentative", 200), "ghosty": ("afk", 100)}
    user_times = {"u1": ("afk", 100), "u2": ("tentative", 300)}
    char_to_user = {"tanky": "u1", "ghosty": "u2", "solo": "u1"}
    merged = merge_availability(char_times, user_times, char_to_user)
    assert merged["tanky"] == "tentative"  # officer edit is newer than u1's afk
    assert merged["ghosty"] == "tentative"  # u2's later declaration beats the officer
    assert merged["solo"] == "afk"  # no char entry — owner's calendar applies
    # Ties go to the player.
    merged = merge_availability({"tanky": ("tentative", 100)}, {"u1": ("afk", 100)}, {"tanky": "u1"})
    assert merged["tanky"] == "afk"


@pytest.mark.asyncio
async def test_planner_officer_edit_overrides_stale_self_declaration(app):
    """The live complaint: a player self-declared AFK, the officer changes
    the character to Tentative on the planner — the newer officer edit must
    actually show (the old always-player-wins merge silently masked it),
    and an officer 'Available' must clear the badge, until the player
    re-declares (newest edit wins again)."""
    import sqlite3 as _sq

    from backend.server.db import upsert_user
    from backend.server.db.claims import store as claims_db

    await _seed_raider("Tanky")
    await upsert_user(discord_id="member-1", discord_name="Member One", discord_username="m1", avatar=None)
    claim = await claims_db.submit_claim("member-1", "Tanky", world=_WORLD)
    await claims_db.review_claim(claim["id"], "approved", "admin")
    today = dt.date.today().isoformat()

    # The player declared AFK a while ago (age the stamp).
    await availability_db.set_days("member-1", {today: "afk"})
    with _sq.connect(availability_db.path) as conn:
        conn.execute("UPDATE user_availability SET updated_at = 1000 WHERE discord_id = 'member-1'")
        conn.commit()

    # Officer corrects the character to tentative (stamped now → newer).
    await availability_db.set_character_days(_WORLD, "Tanky", {today: "tentative"}, set_by="officer-1")

    p = _planner_patches(officer=True)
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0?date={today}")
    assert r.json()["availability"].get("tanky") == "tentative"

    # Officer clears to Available — the stale player AFK must NOT resurface.
    await availability_db.set_character_days(_WORLD, "Tanky", {today: "available"}, set_by="officer-1")
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0?date={today}")
    assert "tanky" not in r.json()["availability"]

    # The player re-declares afterwards — their word wins again.
    await availability_db.set_days("member-1", {today: "afk"})
    with p[0], p[1], p[2], p[3], p[4]:
        r = await _get(app, f"/api/guild/{_GUILD}/raid-planning/0?date={today}")
    assert r.json()["availability"].get("tanky") == "afk"


@pytest.mark.asyncio
async def test_user_available_is_stored_but_hidden_from_calendar():
    """A player's explicit 'available' keeps a stamped row (so it can beat
    an older officer AFK in the merge) but the calendar still renders it
    as the default (absent from get_range)."""
    await availability_db.set_days("u1", {"2026-08-01": "afk", "2026-08-02": "afk"})
    await availability_db.set_days("u1", {"2026-08-01": "available"})
    days = await availability_db.get_range("u1", "2026-08-01", "2026-08-31")
    assert days == {"2026-08-02": "afk"}
    timed = await availability_db.statuses_for_day_with_times("2026-08-01")
    status, stamp = timed["u1"]
    assert status == "available" and stamp > 0
