"""Per-server item-watch isolation at the DB-helper layer (explicit ``world`` args,
leased users schema). HTTP-layer tests live in test_item_watch_routes.py.
"""

from __future__ import annotations

import pytest

from backend.server.db import (
    add_item_watch,
    list_item_watches,
    remove_item_watch,
    upsert_user,
)
from tests.fixtures.pg import pg_conn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def users_db(users_schema: str) -> str:
    """Isolated leased schema per test (conftest ``users_schema``), aliased
    so tests can keep naming it ``users_db``."""
    return users_schema


async def _seed_user(discord_id: str) -> None:
    """Ensure a minimal user row exists so FK constraints pass."""
    await upsert_user(
        discord_id=discord_id,
        discord_name=discord_id,
        discord_username=discord_id,
        avatar=None,
    )


# ---------------------------------------------------------------------------
# A — world column exists on fresh / migrated DB
# ---------------------------------------------------------------------------


def test_world_column_exists_in_schema(users_db):
    """The migration schema must carry the world column on item_watch."""
    with pg_conn(users_db) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'item_watch'",
            (users_db,),
        ).fetchall()
    cols = {r["column_name"] for r in rows}
    assert "world" in cols, "world column not found in item_watch"


def test_unique_constraint_includes_world(users_db):
    """The UNIQUE constraint on item_watch must cover (world, guild_name, character_name, item_id).

    We verify this against pg_index (which covers both UNIQUE constraints and
    unique indexes) — it covers (guild_name,
    character_name, item_id) plus the world column.
    """
    with pg_conn(users_db) as conn:
        # search_path is the leased schema, so the regclass resolves there.
        rows = conn.execute(
            """
            SELECT i.indexrelid::regclass::text AS idx_name, array_agg(a.attname) AS cols
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
            WHERE i.indrelid = 'item_watch'::regclass AND i.indisunique
            GROUP BY i.indexrelid
            """
        ).fetchall()
    unique_col_sets = [frozenset(r["cols"]) for r in rows]

    target = frozenset({"world", "guild_name", "character_name", "item_id"})
    assert target in unique_col_sets, (
        f"No UNIQUE({', '.join(sorted(target))}) constraint found. Found: {unique_col_sets}"
    )


# ---------------------------------------------------------------------------
# B — cross-server isolation: same (guild, character, item_id) on two worlds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_watch_insertable_on_two_worlds():
    """
    The SAME (guild_name, character_name, item_id) must be watchable
    independently on Varsoon and Wuoshi — no UNIQUE violation.
    """
    uid = "watch-iso-user-1"
    await _seed_user(uid)

    row_v = await add_item_watch(
        guild_name="TestGuild",
        character_name="Sihtric",
        item_id=12345,
        item_name="Sword of Test",
        added_by=uid,
        added_by_name="Sihtric",
        world="Varsoon",
    )
    row_w = await add_item_watch(
        guild_name="TestGuild",
        character_name="Sihtric",
        item_id=12345,
        item_name="Sword of Test",
        added_by=uid,
        added_by_name="Sihtric",
        world="Wuoshi",
    )

    assert row_v["world"] == "Varsoon"
    assert row_w["world"] == "Wuoshi"
    assert row_v["id"] != row_w["id"], "Two separate rows must have been created"


@pytest.mark.asyncio
async def test_listing_watches_for_varsoon_excludes_wuoshi():
    """list_item_watches('TestGuild2', world='Varsoon') must NOT return Wuoshi watches."""
    uid = "watch-iso-user-2"
    await _seed_user(uid)

    await add_item_watch(
        guild_name="TestGuild2",
        character_name="Menludiir",
        item_id=99001,
        item_name="Shield of Nektulos",
        added_by=uid,
        added_by_name="Menludiir",
        world="Varsoon",
    )
    await add_item_watch(
        guild_name="TestGuild2",
        character_name="Menludiir",
        item_id=99001,
        item_name="Shield of Nektulos",
        added_by=uid,
        added_by_name="Menludiir",
        world="Wuoshi",
    )

    varsoon_watches = await list_item_watches("TestGuild2", world="Varsoon")
    wuoshi_watches = await list_item_watches("TestGuild2", world="Wuoshi")

    assert all(w["world"] == "Varsoon" for w in varsoon_watches), (
        "list_item_watches for Varsoon returned a non-Varsoon row"
    )
    assert all(w["world"] == "Wuoshi" for w in wuoshi_watches), "list_item_watches for Wuoshi returned a non-Wuoshi row"
    # Both worlds should have exactly one match for this character+item
    v_ids = [w["item_id"] for w in varsoon_watches if w["character_name"] == "Menludiir"]
    w_ids = [w["item_id"] for w in wuoshi_watches if w["character_name"] == "Menludiir"]
    assert 99001 in v_ids
    assert 99001 in w_ids


# ---------------------------------------------------------------------------
# C — duplicate guard is world-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_on_same_world_raises():
    """Inserting the same (world, guild, character, item_id) twice must raise ValueError."""
    uid = "watch-dup-user-1"
    await _seed_user(uid)

    await add_item_watch(
        guild_name="DupGuild",
        character_name="Dupchar",
        item_id=55555,
        item_name="Duplicate Item",
        added_by=uid,
        added_by_name="Dupchar",
        world="Varsoon",
    )
    with pytest.raises(ValueError, match="already being watched"):
        await add_item_watch(
            guild_name="DupGuild",
            character_name="Dupchar",
            item_id=55555,
            item_name="Duplicate Item",
            added_by=uid,
            added_by_name="Dupchar",
            world="Varsoon",
        )


@pytest.mark.asyncio
async def test_same_watch_on_different_world_does_not_raise():
    """Same (guild, character, item_id) but different world must NOT raise ValueError."""
    uid = "watch-dup-user-2"
    await _seed_user(uid)

    await add_item_watch(
        guild_name="DupGuild2",
        character_name="Dupchar2",
        item_id=66666,
        item_name="Cross-Server Item",
        added_by=uid,
        added_by_name="Dupchar2",
        world="Varsoon",
    )
    # Must not raise:
    row = await add_item_watch(
        guild_name="DupGuild2",
        character_name="Dupchar2",
        item_id=66666,
        item_name="Cross-Server Item",
        added_by=uid,
        added_by_name="Dupchar2",
        world="Wuoshi",
    )
    assert row["world"] == "Wuoshi"


# ---------------------------------------------------------------------------
# D — remove_item_watch is world-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_watch_scoped_to_world():
    """Deleting a Varsoon watch must not delete the Wuoshi watch with same guild/id."""
    uid = "watch-rm-user-1"
    await _seed_user(uid)

    row_v = await add_item_watch(
        guild_name="RmGuild",
        character_name="Rmchar",
        item_id=77777,
        item_name="Removable Item",
        added_by=uid,
        added_by_name="Rmchar",
        world="Varsoon",
    )
    row_w = await add_item_watch(
        guild_name="RmGuild",
        character_name="Rmchar",
        item_id=77777,
        item_name="Removable Item",
        added_by=uid,
        added_by_name="Rmchar",
        world="Wuoshi",
    )

    # Remove only the Varsoon watch
    removed = await remove_item_watch(row_v["id"], "RmGuild", world="Varsoon")
    assert removed

    # Wuoshi watch must still exist
    wuoshi_watches = await list_item_watches("RmGuild", world="Wuoshi")
    assert any(w["id"] == row_w["id"] for w in wuoshi_watches), (
        "Wuoshi watch was incorrectly deleted when removing the Varsoon watch"
    )

    # Varsoon watch must be gone
    varsoon_watches = await list_item_watches("RmGuild", world="Varsoon")
    assert not any(w["id"] == row_v["id"] for w in varsoon_watches), "Varsoon watch was not deleted"
