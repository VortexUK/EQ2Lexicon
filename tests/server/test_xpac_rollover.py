"""Automatic expansion rollover + the rankings era lock."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.server.api.rankings import _apply_era_lock
from backend.server.db import init_db
from backend.server.db.servers import store as servers_db
from backend.server.xpac_rollover import (
    XPAC_MAX_LEVEL,
    check_rollovers,
    parse_dt,
    rollover_due,
)
from tests.fixtures.users_db import point_users_db_at


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)


_NOW = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)


def _seed(world: str = "Wuoshi", **over) -> None:
    servers_db.upsert_server_settings_sync(
        world,
        max_level=over.pop("max_level", 70),
        current_xpac=over.pop("current_xpac", "EoF"),
        launch_dt=None,
        next_xpac=over.pop("next_xpac", "RoK"),
        next_xpac_dt=over.pop("next_xpac_dt", "2026-09-09T19:00:00+00:00"),
    )


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def test_parse_dt_handles_naive_offset_and_garbage():
    assert parse_dt("2026-09-09T19:00:00+00:00") == datetime(2026, 9, 9, 19, 0, tzinfo=UTC)
    assert parse_dt("2026-09-09T19:00:00").tzinfo is UTC  # naive -> UTC
    assert parse_dt("not a date") is None
    assert parse_dt(None) is None


def test_rollover_due_cases():
    row = {"next_xpac": "RoK", "next_xpac_dt": "2026-09-09T19:00:00+00:00"}
    assert rollover_due(row, _NOW) is True
    assert rollover_due({**row, "next_xpac_dt": "2026-12-01T00:00:00+00:00"}, _NOW) is False
    assert rollover_due({**row, "next_xpac": None}, _NOW) is False
    assert rollover_due({**row, "next_xpac_dt": "garbage"}, _NOW) is False


def test_xpac_level_caps_are_sane():
    assert XPAC_MAX_LEVEL["RoK"] == 80
    assert XPAC_MAX_LEVEL["EoF"] == 70


# ---------------------------------------------------------------------------
# The flip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_rollovers_flips_due_server():
    _seed()
    flipped = await check_rollovers(_NOW)
    assert [f["world"] for f in flipped] == ["Wuoshi"]

    row = servers_db.get_server_by_world_sync("Wuoshi")
    assert row["current_xpac"] == "RoK"
    assert row["max_level"] == 80
    # Cutoff = the SCHEDULED instant, not "now" — a late restart must not
    # shift the era-lock boundary.
    assert row["current_xpac_started_dt"] == "2026-09-09T19:00:00+00:00"
    assert row["next_xpac"] is None and row["next_xpac_dt"] is None

    # Idempotent: nothing left to flip.
    assert await check_rollovers(_NOW) == []


@pytest.mark.asyncio
async def test_check_rollovers_ignores_future_and_unset():
    _seed(next_xpac_dt="2026-12-01T00:00:00+00:00")
    _seed(world="Varsoon", next_xpac=None, next_xpac_dt=None)
    assert await check_rollovers(_NOW) == []
    assert servers_db.get_server_by_world_sync("Wuoshi")["current_xpac"] == "EoF"


@pytest.mark.asyncio
async def test_check_rollovers_unknown_xpac_keeps_cap():
    _seed(next_xpac="Mystery", max_level=70)
    flipped = await check_rollovers(_NOW)
    assert flipped[0]["current_xpac"] == "Mystery"
    row = servers_db.get_server_by_world_sync("Wuoshi")
    assert row["max_level"] == 70  # unknown short code — cap untouched


# ---------------------------------------------------------------------------
# Era lock (pure)
# ---------------------------------------------------------------------------

_ZONE_XPAC = {"veeshan's peak": "RoK", "emerald halls": "EoF"}
_CUTOFF = 1_789_000_000


def _kill(zone: str, ingested_at: int | None) -> dict:
    return {"id": 1, "zone": zone, "ingested_at": ingested_at}


def test_era_lock_none_keeps_everything():
    kills = [_kill("Emerald Halls", _CUTOFF + 999)]
    assert _apply_era_lock(kills, None, _ZONE_XPAC) == kills


def test_era_lock_filters_only_out_of_era_post_cutoff():
    kills = [
        _kill("Veeshan's Peak", _CUTOFF + 999),  # in-era: always ranks
        _kill("Emerald Halls", _CUTOFF - 1),  # out-of-era, pre-cutoff: keeps its rank
        _kill("Emerald Halls", _CUTOFF + 1),  # out-of-era, post-cutoff: locked out
        _kill("Uncatalogued Zone", _CUTOFF + 999),  # unknown zone: never over-lock
        _kill("Emerald Halls", None),  # out-of-era, unknown ingest time: locked out
    ]
    kept = _apply_era_lock(kills, ("RoK", _CUTOFF), _ZONE_XPAC)
    assert [k["zone"] for k in kept] == ["Veeshan's Peak", "Emerald Halls", "Uncatalogued Zone"]
    assert kept[1]["ingested_at"] == _CUTOFF - 1
