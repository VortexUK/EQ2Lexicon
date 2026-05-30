"""
GET    /api/auth/tokens         — list the current user's API tokens.
POST   /api/auth/tokens         — mint a new token. Body: {"name": "..."}.
                                  Returns the raw token ONCE — store it now.
DELETE /api/auth/tokens/{id}    — revoke a token (scoped to the caller).

All endpoints require an authenticated session (Discord OAuth). API tokens
are used by external integrations (ACT plugin, future CLI tools) to talk
to /api/parses/ingest without going through the OAuth dance every time.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from web import db as users_db
from web.auth_deps import (
    is_admin,
    require_user_session_or_token,
)
from web.auth_deps import (
    require_user_session as _require_user,
)
from web.config import ALLOWED_SERVERS
from web.lib.log_safety import scrub as _scrub
from web.limiter import limiter

_log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TokenRow(BaseModel):
    id: int
    name: str
    token_prefix: str  # eq2c_xxxxxxx
    created_at: int
    last_used_at: int | None = None
    revoked_at: int | None = None


class TokenListResponse(BaseModel):
    tokens: list[TokenRow]


class TokenMintRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TokenMintResponse(BaseModel):
    token: str  # the RAW token — shown ONCE
    row: TokenRow


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/auth/tokens", response_model=TokenListResponse)
@limiter.limit("60/minute")
async def list_tokens(request: Request) -> TokenListResponse:
    user = _require_user(request)
    rows = await users_db.list_api_tokens(user["id"])
    return TokenListResponse(tokens=[TokenRow(**r) for r in rows])


@router.post("/auth/tokens", response_model=TokenMintResponse, status_code=201)
@limiter.limit("10/minute")
async def mint_token(request: Request, body: TokenMintRequest) -> TokenMintResponse:
    user = _require_user(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Token name must not be empty.")
    raw, row = await users_db.mint_api_token(user["id"], name)
    _log.info(
        "[auth-tokens] Token minted: user_id=%s token_id=%s name=%s prefix=%s",
        user["id"],
        row["id"],
        _scrub(name),
        row["token_prefix"],
    )
    return TokenMintResponse(token=raw, row=TokenRow(**row))


@router.delete("/auth/tokens/{token_id}", status_code=204)
@limiter.limit("30/minute")
async def revoke_token(request: Request, token_id: int) -> None:
    user = _require_user(request)
    ok = await users_db.revoke_api_token(user["id"], token_id)
    if not ok:
        # Either the token doesn't exist, isn't ours, or was already revoked.
        raise HTTPException(status_code=404, detail="Token not found or already revoked.")
    _log.info(
        "[auth-tokens] Token revoked: user_id=%s token_id=%s",
        user["id"],
        token_id,
    )


# ---------------------------------------------------------------------------
# Token validation (for the ACT plugin's "Test connection" button)
# ---------------------------------------------------------------------------


class WhoAmIResponse(BaseModel):
    discord_id: str
    discord_name: str
    auth_source: str  # 'token' for plugin calls; 'session' for browser calls
    # Site-admin status — derived from the ADMIN_DISCORD_IDS env-var
    # allowlist (env-only by design; never persisted in the DB). The
    # ACT plugin (v0.1.14+) uses this to ungate the Server URL field
    # in its settings UI for admins, so non-admin users never have to
    # think about which endpoint they're pointing at.
    is_admin: bool = False
    # EQ2 servers this account is allowed to upload from. Mirrors the
    # ALLOWED_SERVERS env var. The ACT plugin shows this list in a
    # read-only card so users know up-front which characters' parses
    # will reach the site; /api/parses/ingest enforces the same list
    # server-side in strict mode.
    allowed_servers: list[str] = []


@router.get("/auth/whoami", response_model=WhoAmIResponse)
@limiter.limit("60/minute")
async def whoami(request: Request) -> WhoAmIResponse:
    """Returns the authenticated user. Accepts session cookie OR bearer
    token, so the ACT plugin can hit this with its token to verify the
    server is reachable and the token is valid."""
    user = await require_user_session_or_token(request)
    return WhoAmIResponse(
        discord_id=user["id"],
        discord_name=user.get("discord_name") or user.get("username") or user["id"],
        auth_source=user.get("auth_source", "unknown"),
        is_admin=is_admin(user),
        # Sorted for stable client-side display ordering — frozensets
        # don't guarantee iteration order, so sort once here rather
        # than asking every plugin install to sort it on receipt.
        allowed_servers=sorted(ALLOWED_SERVERS),
    )
