from __future__ import annotations

import pytest

from backend.server import census_health as ch


def test_initial_state_is_unknown_up():
    ch._reset_for_test()
    s = ch.get_state()
    assert s["status"] in ("up", "unknown")
    assert "checked_at" in s


@pytest.mark.asyncio
async def test_probe_marks_up_on_200(monkeypatch):
    ch._reset_for_test()

    async def fake_probe() -> bool:
        return True

    monkeypatch.setattr(ch, "_probe_census", fake_probe)
    await ch.refresh_health()
    assert ch.get_state()["status"] == "up"


@pytest.mark.asyncio
async def test_probe_marks_down_on_failure(monkeypatch):
    ch._reset_for_test()

    async def fake_probe() -> bool:
        return False

    monkeypatch.setattr(ch, "_probe_census", fake_probe)
    await ch.refresh_health()
    assert ch.get_state()["status"] == "down"
    assert ch.is_down() is True


# ---------------------------------------------------------------------------
# _body_looks_healthy — validates the JSON envelope rather than just status
# code. During Census outages we observed 200 OK with a body like
# {"errorCode":"SERVER_ERROR"}, which the old status-code-only check
# incorrectly reported as healthy.
# ---------------------------------------------------------------------------


def test_body_looks_healthy_accepts_normal_envelope():
    """A normal collection response has ``returned`` and no errorCode."""
    body = {"returned": 1, "world_list": [{"name": "Varsoon"}]}
    assert ch._body_looks_healthy(body) is True


def test_body_looks_healthy_accepts_zero_returned():
    """``returned: 0`` is still a healthy response — Census found no rows
    matching the query, not that Census is broken."""
    body = {"returned": 0}
    assert ch._body_looks_healthy(body) is True


def test_body_looks_healthy_rejects_error_code():
    """The exact false-positive that started this fix: 200 OK with an
    ``errorCode`` field is Census signalling an internal failure."""
    body = {"errorCode": "SERVER_ERROR"}
    assert ch._body_looks_healthy(body) is False


def test_body_looks_healthy_rejects_error_code_even_with_other_fields():
    """Future-proof: if Census ever ships errorCode alongside other fields,
    presence of errorCode still wins."""
    body = {"returned": 0, "errorCode": "SERVER_ERROR"}
    assert ch._body_looks_healthy(body) is False


def test_body_looks_healthy_rejects_no_returned():
    """A 200 with neither errorCode nor a ``returned`` envelope field is
    suspicious — probably an HTML error page or a different endpoint
    entirely. Treat it as unhealthy."""
    body = {"weird": "shape"}
    assert ch._body_looks_healthy(body) is False


def test_body_looks_healthy_rejects_non_dict():
    """Defensive: a body that isn't a JSON object is unhealthy."""
    assert ch._body_looks_healthy([]) is False  # type: ignore[arg-type]
    assert ch._body_looks_healthy("oops") is False  # type: ignore[arg-type]
    assert ch._body_looks_healthy(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# game_server_status parsing + endpoint (footer server-status indicator)
# ---------------------------------------------------------------------------

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import census_health


def test_parse_server_states_filters_to_eq2_and_normalises():
    body = {
        "game_server_status_list": [
            {"name": "Wuoshi", "game_code": "eq2", "last_reported_state": "HIGH", "last_reported_time": "1784023424"},
            {"name": "Antonia Bayle", "game_code": "eq2", "last_reported_state": "locked", "last_reported_time": "x"},
            {"name": "Povar", "game_code": "eq", "last_reported_state": "up", "last_reported_time": "1"},
            {"name": "", "game_code": "eq2", "last_reported_state": "up"},
            "garbage",
        ]
    }
    out = census_health._parse_server_states(body)
    assert out["wuoshi"] == {"name": "Wuoshi", "state": "high", "reported_at": 1784023424}
    assert out["antonia bayle"]["state"] == "locked"
    assert out["antonia bayle"]["reported_at"] == 0  # unparseable time -> 0
    assert "povar" not in out  # wrong game
    assert len(out) == 2


def test_get_server_state_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        census_health, "_server_states", {"wuoshi": {"name": "Wuoshi", "state": "high", "reported_at": 1}}
    )
    assert census_health.get_server_state("Wuoshi")["state"] == "high"
    assert census_health.get_server_state("nope") is None


@pytest.mark.asyncio
async def test_server_status_endpoint_unknown_before_first_fetch(app, monkeypatch):
    monkeypatch.setattr(census_health, "_server_states", {})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/census/server-status")
    assert r.status_code == 200
    assert r.json()["state"] == "unknown"


@pytest.mark.asyncio
async def test_server_status_endpoint_returns_current_world(app, monkeypatch):
    monkeypatch.setattr(
        census_health,
        "_server_states",
        {
            "wuoshi": {"name": "Wuoshi", "state": "locked", "reported_at": 42},
            "varsoon": {"name": "Varsoon", "state": "high", "reported_at": 43},
        },
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/census/server-status")
    body = r.json()
    assert body["state"] in ("locked", "high")  # whichever world the test app focuses
    assert body["reported_at"] in (42, 43)
    assert body["world"].lower() in ("wuoshi", "varsoon")
