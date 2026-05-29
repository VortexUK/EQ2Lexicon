"""BE-011: last_used_at write coalescing in lookup_api_token."""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_users_db_for_coalesce(tmp_path):
    """Minimal users.db with one approved user and one api token."""
    import sqlite3 as _sqlite3

    from web import db as users_db

    db_path = tmp_path / "users.db"
    users_db.init_db(db_path)
    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (discord_id, discord_name, access_status) VALUES (?, ?, ?)",
            ("user-coalesce", "Alice", "approved"),
        )
        conn.commit()
    return db_path


@pytest.mark.asyncio
async def test_lookup_api_token_coalesces_writes(tmp_users_db_for_coalesce) -> None:
    """BE-011: two lookups within 60 s should issue at most one UPDATE."""
    from web import db as users_db

    db_path = tmp_users_db_for_coalesce

    # Mint a token for the user.
    raw, _row = await users_db.mint_api_token("user-coalesce", "Test Token", path=db_path)

    # First lookup — should write last_used_at.
    row1 = await users_db.lookup_api_token(raw, path=db_path)
    assert row1 is not None
    last_after_first = row1["last_used_at"]
    assert last_after_first is not None

    # Second lookup immediately after — within 60 s, so should NOT write.
    row2 = await users_db.lookup_api_token(raw, path=db_path)
    assert row2 is not None
    # last_used_at returned by the second call is whatever's in the row;
    # since we didn't update, it equals what the first call wrote.
    assert row2["last_used_at"] == last_after_first
