"""
Shared auth dependencies for FastAPI routes.

`require_user_session` — session-cookie only (the existing pattern).
`require_user_session_or_token` — session cookie OR Authorization: Bearer.
                                  Used by endpoints meant for the ACT plugin
                                  (and other external integrations).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from web import db as users_db


def require_user_session(request: Request) -> dict:
    """Require a logged-in session. Returns the session user dict.

    Shape:  {"id": "<discord_id>", "username": "...", ...}
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_user_session_or_token(request: Request) -> dict:
    """Accept either a session cookie OR an `Authorization: Bearer <token>`
    header. Returns a normalised dict:

        {"id": "<discord_id>", "username": "...", "auth_source": "session"|"token"}

    For token auth we also bump last_used_at on the token row.
    """
    # Prefer session cookie if present — cheaper, no DB hit.
    user = request.session.get("user")
    if user:
        return {**user, "auth_source": "session"}

    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    raw_token = auth_header[len("Bearer ") :].strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    row = await users_db.lookup_api_token(raw_token)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    if row.get("access_status") not in ("approved", None):
        # Tokens minted before the user is approved still resolve, but we
        # gate on access_status here so a token from a denied/pending user
        # can't be used for writes.
        raise HTTPException(status_code=403, detail="Account not approved")

    return {
        "id": row["user_id"],
        "username": row.get("discord_username") or row.get("discord_name") or row["user_id"],
        "discord_name": row.get("discord_name"),
        "auth_source": "token",
        "token_id": row["token_id"],
        "token_name": row.get("token_name"),
    }
