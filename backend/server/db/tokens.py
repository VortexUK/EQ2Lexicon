"""users.db api_tokens table helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the API token domain. ``path: Path = DB_PATH`` parameter on every public
function so tests can inject a temp DB.

Raw tokens are 'eq2c_' + 32 url-safe base64 chars (≈192 bits entropy).
Only the SHA-256 hash is stored; the raw token is shown to the user once
at mint time and never recoverable.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

import aiosqlite

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

TOKEN_PREFIX = "eq2c_"


class TokensStore(AsyncStoreBase):
    """users.db `tokens` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db); methods open per-call
    connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    @staticmethod
    def generate_token() -> tuple[str, str, str]:
        """Mint a new bearer token.

        Returns (raw_token, sha256_hex, prefix_for_display).
        The raw_token is what the user pastes into the plugin — show it ONCE.
        """
        body = secrets.token_urlsafe(24)  # ~32 char url-safe base64
        raw = f"{TOKEN_PREFIX}{body}"
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        prefix = raw[:12]  # eq2c_ + 7 chars — enough to disambiguate in UI
        return raw, h, prefix

    @staticmethod
    def hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def mint_api_token(
        self,
        user_id: str,
        name: str,
    ) -> tuple[str, dict]:
        """Create a new token row. Returns (raw_token, row_dict).

        The raw_token must be returned to the caller and shown to the user
        immediately — it cannot be recovered later.
        """
        raw, h, prefix = TokensStore.generate_token()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                _SQL["mint_token"],
                (user_id, name, h, prefix),
            )
            new_id = cur.lastrowid
            await db.commit()
            async with db.execute(_SQL["find_by_id"], (new_id,)) as cur2:
                row = await cur2.fetchone()
        assert row is not None
        return raw, dict(row)

    async def list_api_tokens(self, user_id: str) -> list[dict]:
        """All tokens for a user, newest first. Hash is omitted — UI doesn't need it."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                _SQL["list_for_user"],
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def revoke_api_token(
        self,
        user_id: str,
        token_id: int,
    ) -> bool:
        """Mark a token revoked. Scoped to user_id so one user can't revoke another's.
        Returns True if a row was updated."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["revoke_token"],
                (token_id, user_id),
            )
            await db.commit()
        return cur.rowcount > 0

    async def lookup_api_token(self, raw_token: str) -> dict | None:
        """Look up a token by its raw value (we hash internally). Returns the
        row plus the joined user info, or None if not found / revoked / expired.
        Side effect: bumps last_used_at on success."""
        if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
            return None
        h = TokensStore.hash_token(raw_token)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                _SQL["lookup_by_hash"],
                (h,),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row["revoked_at"] is not None:
                return None
            # Coalesce last_used_at writes to 60s buckets.
            #
            # Plugin uploads fire multiple times per second during a raid; committing
            # an UPDATE on every upload was a real write-storm risk (WAL mitigates
            # locking but the disk write itself is the cost). Sub-minute precision
            # on this column isn't useful — the UI shows "last used 5 min ago",
            # not "last used 0.6 seconds ago". The existing SELECT already pulled
            # the current value as part of the row fetch in lookup callers; check
            # against it here.
            now = int(time.time())
            last_used = row["last_used_at"]
            did_write = last_used is None or (now - int(last_used)) >= 60
            if did_write:
                await db.execute(
                    _SQL["update_last_used_at"],
                    (now, row["token_id"]),
                )
                await db.commit()
        result = dict(row)
        if did_write:
            result["last_used_at"] = now
        return result


# The shared default instance — every runtime consumer goes through this.
store = TokensStore()
