"""Tests for the server-side upload-honesty layer.

Covers the pure plausibility gate (backend/server/parses/plausibility.py), the
hardened numeric coercers, the request size caps + body-size middleware, and
the ingest route's reject/quarantine wiring.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.parses import plausibility
from backend.server.parses.models import (
    Combatant,
    Encounter,
    _to_float,
    _to_int,
    _to_perc,
)
from backend.server.parses.plausibility import Verdict, evaluate
from tests.server._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _signed_post_kwargs,
)

_NOW = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())


def _enc(**over) -> Encounter:
    base = dict(
        encid="ABCD1234",
        title="Some Boss",
        zone="Some Zone",
        started_at=datetime(2026, 5, 24, 13, 51, 56, tzinfo=UTC),
        ended_at=datetime(2026, 5, 24, 13, 52, 42, tzinfo=UTC),
        duration_s=46,
        total_damage=500_000,
        encdps=10_869.5,
        kills=1,
        deaths=0,
        success_level=1,
    )
    base.update(over)
    return Encounter(**base)  # type: ignore[arg-type]


def _combatant(**over) -> Combatant:
    base = dict(
        encid="ABCD1234",
        name="Menludiir",
        ally=True,
        started_at=datetime(2026, 5, 24, 13, 51, 56, tzinfo=UTC),
        ended_at=datetime(2026, 5, 24, 13, 52, 42, tzinfo=UTC),
        duration_s=46,
        damage=250_000,
        damage_perc=50.0,
        kills=1,
        healed=0,
        healed_perc=0.0,
        crit_heals=0,
        heals=0,
        cure_dispels=0,
        power_drain=0,
        power_replenish=0,
        dps=5434.0,
        encdps=5434.0,
        enchps=0.0,
        hits=100,
        crit_hits=30,
        blocked=0,
        misses=5,
        swings=105,
        heals_taken=0,
        damage_taken=1000,
        deaths=0,
        to_hit=95.0,
        crit_dam_perc=30.0,
        crit_heal_perc=0.0,
        crit_types=None,
        threat_str=None,
        threat_delta=0,
    )
    base.update(over)
    return Combatant(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pure gate — evaluate()
# ---------------------------------------------------------------------------


def test_evaluate_accepts_a_normal_fight():
    assert evaluate(_enc(), [_combatant()], now=_NOW).verdict is Verdict.ACCEPT


@pytest.mark.parametrize(
    ("enc_over", "reason"),
    [
        ({"duration_s": -1}, "duration_negative"),
        ({"total_damage": -5}, "total_damage_negative"),
        ({"encdps": -1.0}, "encdps_negative"),
        # started > ended
        (
            {
                "started_at": datetime(2026, 5, 24, 14, 0, tzinfo=UTC),
                "ended_at": datetime(2026, 5, 24, 13, 0, tzinfo=UTC),
            },
            "time_out_of_order",
        ),
        # before the 2015 floor
        (
            {
                "started_at": datetime(2001, 1, 1, tzinfo=UTC),
                "ended_at": datetime(2001, 1, 1, 0, 1, tzinfo=UTC),
            },
            "timestamp_implausible",
        ),
        # far future
        (
            {
                "started_at": datetime(2031, 1, 1, tzinfo=UTC),
                "ended_at": datetime(2031, 1, 1, 0, 1, tzinfo=UTC),
            },
            "timestamp_implausible",
        ),
    ],
)
def test_evaluate_rejects_impossible_encounters(enc_over, reason):
    r = evaluate(_enc(**enc_over), [_combatant()], now=_NOW)
    assert r.verdict is Verdict.REJECT
    assert r.reason == reason


def test_evaluate_rejects_negative_combatant_damage():
    r = evaluate(_enc(), [_combatant(damage=-1)], now=_NOW)
    assert r.verdict is Verdict.REJECT
    assert r.reason == "combatant_damage_negative"


def test_evaluate_quarantines_long_idle_merge_without_erroring():
    # A > 2h "encounter" is ACT idle-merge, not a real fight — quarantined
    # (off-board), NOT rejected, so the upload still succeeds for the user.
    r = evaluate(_enc(duration_s=plausibility.MAX_FIGHT_S + 1), [_combatant()], now=_NOW)
    assert r.verdict is Verdict.QUARANTINE
    assert r.reason == "duration_too_long"


def test_evaluate_quarantines_implausible_encounter_rate():
    r = evaluate(_enc(encdps=plausibility.MAX_PLAUSIBLE_RATE * 2), [_combatant()], now=_NOW)
    assert r.verdict is Verdict.QUARANTINE
    assert r.reason == "implausible_encdps"


def test_evaluate_quarantines_implausible_combatant_rate():
    r = evaluate(_enc(), [_combatant(encdps=plausibility.MAX_PLAUSIBLE_RATE + 1)], now=_NOW)
    assert r.verdict is Verdict.QUARANTINE
    assert r.reason == "implausible_rate"


# ---------------------------------------------------------------------------
# Coercer hardening
# ---------------------------------------------------------------------------


def test_to_float_rejects_non_finite():
    assert _to_float("inf") == 0.0
    assert _to_float("-inf") == 0.0
    assert _to_float("nan") == 0.0
    assert _to_float(float("inf")) == 0.0
    assert _to_float("1234.5") == 1234.5


def test_to_perc_rejects_non_finite():
    assert _to_perc("inf%") == 0.0
    assert _to_perc("93%") == 93.0


def test_to_int_clamps_to_int64():
    assert _to_int(10**30) == 2**63 - 1
    assert _to_int(-(10**30)) == -(2**63)
    assert _to_int("nan") == 0
    assert _to_int(1234) == 1234


def test_to_int_of_huge_float_does_not_overflow():
    # A finite huge float must clamp, never raise at the SQLite boundary.
    assert _to_int(1e308) == 2**63 - 1
    assert math.isfinite(float(_to_int(1e308)))


# ---------------------------------------------------------------------------
# Request size caps + body-size middleware
# ---------------------------------------------------------------------------


def test_ingest_request_caps_combatant_list():
    from pydantic import ValidationError

    from backend.server.api.parses.models import IngestRequest

    payload = _minimal_payload()
    payload["combatants"] = [{"name": f"Char{i}"} for i in range(513)]
    with pytest.raises(ValidationError):
        IngestRequest(**payload)


@pytest.mark.asyncio
async def test_body_size_middleware_rejects_oversized_content_length():
    from backend.server.core.gzip_request import MAX_REQUEST_BODY_BYTES, BodySizeLimitMiddleware

    downstream = AsyncMock()
    mw = BodySizeLimitMiddleware(downstream)
    scope = {
        "type": "http",
        "headers": [(b"content-length", str(MAX_REQUEST_BODY_BYTES + 1).encode())],
    }
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    await mw(scope, AsyncMock(), send)
    downstream.assert_not_awaited()
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_size_middleware_passes_small_bodies_through():
    from backend.server.core.gzip_request import BodySizeLimitMiddleware

    downstream = AsyncMock()
    mw = BodySizeLimitMiddleware(downstream)
    scope = {"type": "http", "headers": [(b"content-length", b"128")]}
    await mw(scope, AsyncMock(), AsyncMock())
    downstream.assert_awaited_once()


# ---------------------------------------------------------------------------
# Route wiring — reject / quarantine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_rejects_impossible_payload_before_insert(app):
    payload = _minimal_payload()
    payload["encounter"]["duration"] = -5  # impossible → REJECT (400)

    insert = MagicMock(return_value=("inserted", 1, 1, 0, 0))
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=insert),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))

    assert r.status_code == 400
    assert "implausible" in r.json()["detail"].lower()
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_route_quarantines_implausible_rate_off_the_board(app):
    payload = _minimal_payload()
    payload["encounter"]["encdps"] = plausibility.MAX_PLAUSIBLE_RATE * 10

    insert = MagicMock(return_value=("inserted", 1, 1, 0, 0))
    quarantine = MagicMock(return_value=777)
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=insert),
        patch("backend.server.api.parses.ingest._quarantine_encounter_sync", new=quarantine),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))

    assert r.status_code == 201
    assert r.json()["status"] == "quarantined"
    assert r.json()["encounter_id"] is None
    quarantine.assert_called_once()  # routed to the audit table
    insert.assert_not_called()  # never reached the encounters table
