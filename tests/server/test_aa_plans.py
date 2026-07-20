"""AA planner saved builds — DB layer + API tests.

Same harness as test_favorites.py: temp users.db via init_db +
point_users_db_at, signed session cookies against the global app fixture.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.db import init_db
from backend.server.db.aa_plans import store as aa_plans
from tests.fixtures.users_db import point_users_db_at

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"

pytestmark = pytest.mark.asyncio


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)


_ALLOC = {"42": {"101": 10, "102": 3}, "29": {"7": 1}}


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------


async def test_create_get_roundtrip_and_slug(users_db):
    row = await aa_plans.create_plan("disc1", "Wuoshi", "Badbang", "Raid DPS", "EoF", json.dumps(_ALLOC))
    assert row["name"] == "Raid DPS"
    assert row["share_slug"]
    fetched = await aa_plans.get_plan(row["id"])
    assert fetched is not None and json.loads(fetched["allocations"]) == _ALLOC
    by_slug = await aa_plans.get_plan_by_slug(row["share_slug"])
    assert by_slug is not None and by_slug["id"] == row["id"]


async def test_list_scoped_to_owner_world_character(users_db):
    await aa_plans.create_plan("disc1", "Wuoshi", "Badbang", "Mine", None, "{}")
    await aa_plans.create_plan("disc2", "Wuoshi", "Badbang", "Not mine", None, "{}")
    await aa_plans.create_plan("disc1", "Varsoon", "Badbang", "Other world", None, "{}")
    await aa_plans.create_plan("disc1", "Wuoshi", "Otherchar", "Other char", None, "{}")
    rows = await aa_plans.list_plans("disc1", "Wuoshi", "Badbang")
    assert [r["name"] for r in rows] == ["Mine"]
    assert await aa_plans.count_plans("disc1", "Wuoshi", "Badbang") == 1


async def test_update_and_delete_are_owner_scoped(users_db):
    row = await aa_plans.create_plan("disc1", "Wuoshi", "Badbang", "Plan", None, "{}")
    assert await aa_plans.update_plan(row["id"], "disc2", name="Stolen", xpac=None, allocations_json="{}") is False
    assert await aa_plans.update_plan(row["id"], "disc1", name="Renamed", xpac="RoK", allocations_json='{"1":{"2":3}}')
    updated = await aa_plans.get_plan(row["id"])
    assert updated is not None and updated["name"] == "Renamed" and updated["xpac"] == "RoK"
    assert await aa_plans.delete_plan(row["id"], "disc2") is False
    assert await aa_plans.delete_plan(row["id"], "disc1") is True
    assert await aa_plans.get_plan(row["id"]) is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _cookies(user_id: str) -> dict:
    payload = base64.b64encode(json.dumps({"user": {"id": user_id, "username": "tester"}}).encode()).decode()
    return {"session": itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()}


def _client(app, user_id: str | None = "disc-1") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies=_cookies(user_id) if user_id else None,
    )


_BODY = {"character_name": "Badbang", "name": "Raid DPS", "xpac": "EoF", "allocations": _ALLOC}


async def test_requires_session(app):
    async with _client(app, user_id=None) as client:
        assert (await client.get("/api/aa/plans?character=Badbang")).status_code == 401
        assert (await client.post("/api/aa/plans", json=_BODY)).status_code == 401


async def test_create_list_get_flow(app):
    async with _client(app) as client:
        created = await client.post("/api/aa/plans", json=_BODY)
        assert created.status_code == 200
        plan = created.json()
        assert plan["allocations"] == _ALLOC
        assert plan["is_mine"] is True
        assert plan["share_slug"]

        listed = await client.get("/api/aa/plans?character=badbang")  # lowercase URL is normalised
        assert [p["name"] for p in listed.json()] == ["Raid DPS"]

        detail = await client.get(f"/api/aa/plans/{plan['id']}")
        assert detail.status_code == 200


async def test_other_users_cannot_see_or_edit_my_plan(app):
    async with _client(app) as client:
        plan = (await client.post("/api/aa/plans", json=_BODY)).json()
    async with _client(app, user_id="disc-2") as other:
        assert (await other.get(f"/api/aa/plans/{plan['id']}")).status_code == 404
        assert (await other.put(f"/api/aa/plans/{plan['id']}", json=_BODY)).status_code == 404
        assert (await other.delete(f"/api/aa/plans/{plan['id']}")).json() == {"deleted": False}
        # …but the share slug is readable, flagged not-mine.
        shared = await other.get(f"/api/aa/plan/{plan['share_slug']}")
        assert shared.status_code == 200
        assert shared.json()["is_mine"] is False


async def test_update_and_delete_flow(app):
    async with _client(app) as client:
        plan = (await client.post("/api/aa/plans", json=_BODY)).json()
        updated = await client.put(
            f"/api/aa/plans/{plan['id']}",
            json={**_BODY, "name": "Tank build", "allocations": {"42": {"101": 5}}},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Tank build"
        assert updated.json()["allocations"] == {"42": {"101": 5}}
        assert (await client.delete(f"/api/aa/plans/{plan['id']}")).json() == {"deleted": True}
        assert (await client.get(f"/api/aa/plans/{plan['id']}")).status_code == 404


async def test_plan_cap_409(app, monkeypatch):
    monkeypatch.setattr("backend.server.api.aa_plans.MAX_PLANS_PER_CHARACTER", 2)
    async with _client(app) as client:
        for i in range(2):
            assert (await client.post("/api/aa/plans", json={**_BODY, "name": f"Plan {i}"})).status_code == 200
        blocked = await client.post("/api/aa/plans", json={**_BODY, "name": "One too many"})
        assert blocked.status_code == 409


async def test_validation_rejects_bad_payloads(app):
    async with _client(app) as client:
        bad_char = await client.post("/api/aa/plans", json={**_BODY, "character_name": "not a name!!"})
        assert bad_char.status_code == 400
        empty_name = await client.post("/api/aa/plans", json={**_BODY, "name": "   "})
        assert empty_name.status_code == 400
        bad_rank = await client.post("/api/aa/plans", json={**_BODY, "allocations": {"42": {"101": 0}}})
        assert bad_rank.status_code == 422
        bad_key = await client.post("/api/aa/plans", json={**_BODY, "allocations": {"nope": {"101": 1}}})
        assert bad_key.status_code == 422


async def test_unknown_slug_404(app):
    async with _client(app) as client:
        assert (await client.get("/api/aa/plan/doesnotexist")).status_code == 404
