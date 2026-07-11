"""Per-server claim isolation tests (Task 6 — scope claims per (user, world)).

Tests use the DB helpers directly with explicit ``world`` args so they run
without a live HTTP layer and stay fast.  The conftest plants a temp DB and
calls init_db() for us.

Convention mirrors the rest of tests/web/: use the shared ``app`` fixture
from conftest when testing route-level behaviour (x-server header), and call
the DB helpers directly for unit-level isolation cases.
"""

from __future__ import annotations

import pytest

from backend.server import db
from backend.server.db import (
    get_active_claims,
    review_claim,
    set_primary,
    submit_claim,
    upsert_user,
    withdraw_claim,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATH = db.DB_PATH  # redirected to the pytest tmpdir by conftest.py


async def _seed_user(discord_id: str) -> None:
    """Ensure a minimal user row exists so FK constraints pass."""
    await upsert_user(
        discord_id=discord_id,
        discord_name=discord_id,
        discord_username=discord_id,
        avatar=None,
    )


# ---------------------------------------------------------------------------
# A1 — world column exists on fresh DB
# ---------------------------------------------------------------------------


def test_world_column_exists_in_schema():
    """init_db (run by conftest) must have added the world column."""
    import sqlite3

    with sqlite3.connect(_PATH) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
    assert "world" in cols, "world column not found in character_claims"


# ---------------------------------------------------------------------------
# B1 — claims are scoped to world: Varsoon claim not visible on Wuoshi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_claim_invisible_to_different_world():
    """An approved claim on Varsoon must NOT appear in get_active_claims for Wuoshi."""
    uid = "cross-world-user-1"
    await _seed_user(uid)

    # Submit + manually approve on Varsoon
    claim = await submit_claim(uid, "SomeVarsoonChar", world="Varsoon")
    await review_claim(claim["id"], "approved", "admin-1")

    varsoon_data = await get_active_claims(uid, world="Varsoon")
    wuoshi_data = await get_active_claims(uid, world="Wuoshi")

    assert len(varsoon_data["approved"]) == 1
    assert varsoon_data["approved"][0]["character_name"] == "SomeVarsoonChar"
    assert wuoshi_data["approved"] == []
    assert wuoshi_data["pending"] is None


@pytest.mark.asyncio
async def test_pending_claim_invisible_to_different_world():
    """A pending claim on Wuoshi must NOT appear when querying Varsoon."""
    uid = "cross-world-user-2"
    await _seed_user(uid)

    await submit_claim(uid, "WuoshiPendingChar", world="Wuoshi")

    varsoon_data = await get_active_claims(uid, world="Varsoon")
    wuoshi_data = await get_active_claims(uid, world="Wuoshi")

    assert varsoon_data["pending"] is None
    assert wuoshi_data["pending"] is not None
    assert wuoshi_data["pending"]["character_name"] == "WuoshiPendingChar"


# ---------------------------------------------------------------------------
# B2 — is_primary is independent per (discord_id, world)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_is_independent_per_world():
    """
    Set primary char A on Varsoon and char B on Wuoshi.
    Each world reports its own primary; setting Varsoon's primary does NOT
    clear Wuoshi's primary.
    """
    uid = "primary-test-user-1"
    await _seed_user(uid)

    # Approve two chars on different worlds
    claim_v = await submit_claim(uid, "VarsoonPrimary", world="Varsoon")
    claim_v_row = await review_claim(claim_v["id"], "approved", "admin-1")
    assert claim_v_row is not None

    claim_w = await submit_claim(uid, "WuoshiPrimary", world="Wuoshi")
    claim_w_row = await review_claim(claim_w["id"], "approved", "admin-1")
    assert claim_w_row is not None

    # auto-primary should already be set (first approval per world) — verify
    v_data = await get_active_claims(uid, world="Varsoon")
    w_data = await get_active_claims(uid, world="Wuoshi")
    assert v_data["approved"][0]["is_primary"] == 1
    assert w_data["approved"][0]["is_primary"] == 1

    # Now explicitly set the same claim again (idempotent) and verify cross-world isolation
    ok = await set_primary(uid, claim_v["id"], world="Varsoon")
    assert ok

    v_data2 = await get_active_claims(uid, world="Varsoon")
    w_data2 = await get_active_claims(uid, world="Wuoshi")

    assert v_data2["approved"][0]["is_primary"] == 1, "Varsoon primary should still be set"
    assert w_data2["approved"][0]["is_primary"] == 1, "Wuoshi primary must NOT have been cleared"


@pytest.mark.asyncio
async def test_setting_varsoon_primary_does_not_affect_wuoshi_primary():
    """
    User has two approved chars on Varsoon (A and B) and one on Wuoshi (C).
    Switching Varsoon primary from A→B must not touch Wuoshi's C.
    """
    uid = "primary-test-user-2"
    await _seed_user(uid)

    # Approve two Varsoon chars
    c_v1 = await submit_claim(uid, "VarsoonA", world="Varsoon")
    await review_claim(c_v1["id"], "approved", "admin-1")

    c_v2 = await submit_claim(uid, "VarsoonB", world="Varsoon")
    await review_claim(c_v2["id"], "approved", "admin-1")

    # Approve one Wuoshi char
    c_w = await submit_claim(uid, "WuoshiC", world="Wuoshi")
    await review_claim(c_w["id"], "approved", "admin-1")

    # Confirm Wuoshi primary is set
    w_before = await get_active_claims(uid, world="Wuoshi")
    assert w_before["approved"][0]["is_primary"] == 1

    # Switch Varsoon primary to VarsoonB
    ok = await set_primary(uid, c_v2["id"], world="Varsoon")
    assert ok

    # Wuoshi primary must be unchanged
    w_after = await get_active_claims(uid, world="Wuoshi")
    wuoshi_primaries = [c for c in w_after["approved"] if c["is_primary"] == 1]
    assert len(wuoshi_primaries) == 1
    assert wuoshi_primaries[0]["character_name"] == "WuoshiC"


# ---------------------------------------------------------------------------
# B3 — submit_claim records the world that was passed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_claim_records_world():
    """Submitting a claim with world='Wuoshi' stores world='Wuoshi' in the row."""
    uid = "world-record-user-1"
    await _seed_user(uid)

    claim = await submit_claim(uid, "RecordChar", world="Wuoshi")

    assert claim["world"] == "Wuoshi"


@pytest.mark.asyncio
async def test_submit_claim_default_world_is_varsoon():
    """submit_claim without explicit world stores 'Varsoon' (column default)."""
    # This tests the migration path: old call sites that don't pass world
    # get the Varsoon default via the column DEFAULT — but since we're now
    # making world a required param on submit_claim, this test verifies the
    # explicit 'Varsoon' path.
    uid = "world-record-user-2"
    await _seed_user(uid)

    claim = await submit_claim(uid, "VarsoonDefaultChar", world="Varsoon")

    assert claim["world"] == "Varsoon"


# ---------------------------------------------------------------------------
# B4 — withdraw_claim with was_primary promotes within same world only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_withdraw_primary_promotes_within_same_world():
    """
    When the Varsoon primary is withdrawn, the next-oldest Varsoon char gets
    promoted. The Wuoshi primary is not touched.
    """
    uid = "withdraw-primary-user-1"
    await _seed_user(uid)

    # Two Varsoon chars — first one auto-gets primary
    c_v1 = await submit_claim(uid, "VarsoonFirst", world="Varsoon")
    await review_claim(c_v1["id"], "approved", "admin-1")

    c_v2 = await submit_claim(uid, "VarsoonSecond", world="Varsoon")
    await review_claim(c_v2["id"], "approved", "admin-1")

    # One Wuoshi char
    c_w = await submit_claim(uid, "WuoshiFirst", world="Wuoshi")
    await review_claim(c_w["id"], "approved", "admin-1")

    # Confirm initial state
    v_before = await get_active_claims(uid, world="Varsoon")
    assert v_before["approved"][0]["is_primary"] == 1  # VarsoonFirst is primary

    # Withdraw the Varsoon primary
    changed = await withdraw_claim(c_v1["id"], uid, world="Varsoon")
    assert changed

    # VarsoonSecond should now be primary on Varsoon
    v_after = await get_active_claims(uid, world="Varsoon")
    remaining = [c for c in v_after["approved"] if c["status"] == "approved"]
    assert len(remaining) == 1
    assert remaining[0]["character_name"] == "VarsoonSecond"
    assert remaining[0]["is_primary"] == 1

    # Wuoshi primary must be unchanged
    w_after = await get_active_claims(uid, world="Wuoshi")
    assert w_after["approved"][0]["is_primary"] == 1
    assert w_after["approved"][0]["character_name"] == "WuoshiFirst"


# ---------------------------------------------------------------------------
# B5 — list_claims optional world filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_claims_with_world_filter():
    """list_claims(world='Varsoon') returns only Varsoon pending claims."""
    from backend.server.db import list_claims

    uid_v = "list-claims-v-user"
    uid_w = "list-claims-w-user"
    await _seed_user(uid_v)
    await _seed_user(uid_w)

    await submit_claim(uid_v, "ListVarsoonChar", world="Varsoon")
    await submit_claim(uid_w, "ListWuoshiChar", world="Wuoshi")

    varsoon_pending = await list_claims(status="pending", world="Varsoon")
    wuoshi_pending = await list_claims(status="pending", world="Wuoshi")
    all_pending = await list_claims(status="pending", world=None)

    varsoon_chars = {c["character_name"] for c in varsoon_pending}
    wuoshi_chars = {c["character_name"] for c in wuoshi_pending}
    all_chars = {c["character_name"] for c in all_pending}

    assert "ListVarsoonChar" in varsoon_chars
    assert "ListWuoshiChar" not in varsoon_chars

    assert "ListWuoshiChar" in wuoshi_chars
    assert "ListVarsoonChar" not in wuoshi_chars

    # None = all worlds
    assert "ListVarsoonChar" in all_chars
    assert "ListWuoshiChar" in all_chars


# ---------------------------------------------------------------------------
# B6 — same character name is claimable independently per server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_character_name_claimable_on_different_servers():
    """EQ2 names are unique only within a server: the same name on two worlds
    is a different character, so the duplicate-claim check must be world-scoped.
    """
    await _seed_user("u1")
    await _seed_user("u2")

    # u1 claims "Sihtric" on Varsoon
    await submit_claim("u1", "Sihtric", world="Varsoon")

    # u2 can claim "Sihtric" on Wuoshi (different character / server) — must NOT raise
    await submit_claim("u2", "Sihtric", world="Wuoshi")

    # but u2 claiming "Sihtric" on Varsoon (same server as u1's) must be rejected
    with pytest.raises(ValueError):
        await submit_claim("u2", "Sihtric", world="Varsoon")
