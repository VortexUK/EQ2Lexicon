"""last_used_at write coalescing in lookup_api_token."""

from __future__ import annotations

import pytest

from backend.server.db.tokens import TokensStore
from tests.fixtures.pg import pg_conn


@pytest.fixture
def tmp_users_db_for_coalesce(users_schema: str) -> str:
    """Isolated leased users schema with one approved user."""
    with pg_conn(users_schema) as conn:
        conn.execute(
            "INSERT INTO users (discord_id, discord_name, access_status) VALUES (%s, %s, %s)",
            ("user-coalesce", "Alice", "approved"),
        )
    return users_schema


@pytest.mark.asyncio
async def test_lookup_api_token_coalesces_writes(tmp_users_db_for_coalesce) -> None:
    """Two lookups within 60 s should issue at most one UPDATE."""
    schema = tmp_users_db_for_coalesce

    # Mint a token for the user.
    raw, _row = await TokensStore(schema).mint_api_token("user-coalesce", "Test Token")

    # First lookup — should write last_used_at.
    row1 = await TokensStore(schema).lookup_api_token(raw)
    assert row1 is not None
    last_after_first = row1["last_used_at"]
    assert last_after_first is not None

    # Second lookup immediately after — within 60 s, so should NOT write.
    row2 = await TokensStore(schema).lookup_api_token(raw)
    assert row2 is not None
    # last_used_at returned by the second call is whatever's in the row;
    # since we didn't update, it equals what the first call wrote.
    assert row2["last_used_at"] == last_after_first
