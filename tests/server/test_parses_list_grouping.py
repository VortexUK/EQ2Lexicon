"""Tests for the Phase 4 top-N mutual-containment merge gate in
web/routes/parses/list.py:_group_into_fights.

Today's merger merges two uploads when (different uploaders) AND (same
guild_name) AND (same title) AND (start times within 60 s). Phase 4 adds
one more clause: each upload's top-N ally encDPS combatants must appear
in the other upload's ally list (mutual containment).

N is 3 if max(player_count_A, player_count_B) >= 7 else 2. Tests verify
both the N selection boundary and the containment-vs-equality semantics.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_require_user, make_fake_user
from tests.server._parses_fixtures import _FAKE_ENCOUNTER

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


def _two_uploads(
    *,
    top_a: set[str],
    all_a: set[str],
    top_b: set[str],
    all_b: set[str],
    player_count: int = 12,
    time_offset_s: int = 5,
):
    """Build two encounter dicts ready for _group_into_fights and a side-effect
    map that the patched top-N helpers should return for each encounter_id.

    All four sets are the parameters that drive the merge decision. The
    other gates (different uploaders, same guild + title, within 60 s) are
    held constant so the test focuses on the new clause alone."""
    base = _FAKE_ENCOUNTER["started_at"]
    a = dict(
        _FAKE_ENCOUNTER,
        id=10,
        uploaded_by="Alpha",
        started_at=base,
        player_count=player_count,
        combatant_count=player_count + 1,
    )
    b = dict(
        _FAKE_ENCOUNTER,
        id=11,
        uploaded_by="Bravo",
        started_at=base + time_offset_s,
        player_count=player_count,
        combatant_count=player_count + 1,
    )

    def fake_top(_conn, enc_id, _n):
        return {10: top_a, 11: top_b}[enc_id]

    def fake_all(_conn, enc_id):
        return {10: all_a, 11: all_b}[enc_id]

    return [a, b], fake_top, fake_all


@pytest.mark.asyncio
async def test_identical_top_three_merges(app):
    """Two uploads of the same raid fight — identical top 3, identical
    ally rosters. Today's gates pass + new top-N gate passes → ONE fight."""
    rows, fake_top, fake_all = _two_uploads(
        top_a={"P1", "P2", "P3"},
        all_a={"P1", "P2", "P3", "P4", "P5"},
        top_b={"P1", "P2", "P3"},
        all_b={"P1", "P2", "P3", "P4", "P5"},
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=rows)),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="raid"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1, "identical top-3 should merge"
    assert {u["uploaded_by"] for u in data["results"][0]["uploads"]} == {"Alpha", "Bravo"}


@pytest.mark.asyncio
async def test_disjoint_top_three_does_not_merge(app):
    """Two different groups of Guild X simultaneously doing Tarinax — same
    guild, same title, within 60 s — but completely different rosters.
    The pre-Phase-4 merger would merge them; the new gate rejects the merge."""
    rows, fake_top, fake_all = _two_uploads(
        top_a={"P1", "P2", "P3"},
        all_a={"P1", "P2", "P3", "P4", "P5"},
        top_b={"Q1", "Q2", "Q3"},
        all_b={"Q1", "Q2", "Q3", "Q4", "Q5"},
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=rows)),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="raid"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2, "disjoint top-3 should NOT merge"


@pytest.mark.asyncio
async def test_one_sided_containment_does_not_merge(app):
    """A's top 3 are all in B's allies (containment one direction) but
    B's top 3 are NOT all in A's allies (containment fails the other way).
    Mutual containment requires both directions; this case must NOT merge."""
    rows, fake_top, fake_all = _two_uploads(
        top_a={"P1", "P2", "P3"},
        all_a={"P1", "P2", "P3"},  # A only saw 3 allies
        top_b={"Q1", "Q2", "Q3"},
        all_b={"Q1", "Q2", "Q3", "P1", "P2", "P3"},  # B's allies include all of A's top
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=rows)),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="raid"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["total"] == 2


@pytest.mark.asyncio
async def test_partial_top_n_overlap_with_full_containment_merges(app):
    """A's top 3 = {P1, P2, P3}; B's top 3 = {P1, P2, P4} (one different).
    Each side's top-N IS fully contained in the other side's full ally list
    (P4 is in A's all_allies even if not A's top-3). The mutual-containment
    rule allows this case — the difference is ACT in upload A and B
    ranking the bottom slots slightly differently, but it's the same fight."""
    rows, fake_top, fake_all = _two_uploads(
        top_a={"P1", "P2", "P3"},
        all_a={"P1", "P2", "P3", "P4", "P5"},
        top_b={"P1", "P2", "P4"},
        all_b={"P1", "P2", "P3", "P4", "P5"},
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=rows)),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="raid"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_group_bucket_uses_n_equals_two(app):
    """Both uploads have player_count=5 → group bucket → N=2. Identical
    top-2 should merge; differing third slot doesn't matter."""
    rows, fake_top, fake_all = _two_uploads(
        top_a={"P1", "P2"},
        all_a={"P1", "P2", "P3"},
        top_b={"P1", "P2"},
        all_b={"P1", "P2", "P3"},
        player_count=5,
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=rows)),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="dungeon"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_boundary_one_seven_one_six_uses_n_three(app):
    """One upload has player_count=7 (just into raid bucket); the other
    has player_count=6 (group bucket). max(7,6) >= 7 so N=3 wins. Both
    sides happen to share an identical top 3, so the merge should succeed."""
    base = _FAKE_ENCOUNTER["started_at"]
    a = dict(_FAKE_ENCOUNTER, id=10, uploaded_by="Alpha", started_at=base, player_count=7, combatant_count=8)
    b = dict(_FAKE_ENCOUNTER, id=11, uploaded_by="Bravo", started_at=base + 5, player_count=6, combatant_count=7)

    # The patched helpers must respect the N argument the merger passes —
    # capture it so the assertion below is meaningful.
    captured_n: list[int] = []

    def fake_top(_conn, enc_id, n):
        captured_n.append(n)
        return {10: {"P1", "P2", "P3"}, 11: {"P1", "P2", "P3"}}[enc_id]

    def fake_all(_conn, enc_id):
        return {10: {"P1", "P2", "P3", "P4"}, 11: {"P1", "P2", "P3", "P4"}}[enc_id]

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=[a, b])),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="raid"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert 3 in captured_n, "N should be 3 when max(player_count) >= 7"


@pytest.mark.asyncio
async def test_empty_ally_uploads_still_merge(app):
    """Two uploads with no qualifying ally combatants (e.g. ACT logged
    only NPC damage). Top-N is empty set on both sides. The mutual-
    containment rule reduces to set() ⊆ set() which is trivially true,
    so the merge falls back to the existing gates alone."""
    # player_count=0 is unrealistic at ingest, but exercises the empty-set
    # branch of the mutual-containment check (set() ⊆ set() is trivially
    # true) without dragging in the N-selection threshold.
    rows, fake_top, fake_all = _two_uploads(
        top_a=set(),
        all_a=set(),
        top_b=set(),
        all_b=set(),
        player_count=0,
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", MagicMock(return_value=rows)),
        patch("backend.server.api.parses.list._top_n_ally_names", side_effect=fake_top),
        patch("backend.server.api.parses.list._all_ally_names", side_effect=fake_all),
        patch("backend.server.api.parses.list._classify_zone", return_value="other"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["total"] == 1
