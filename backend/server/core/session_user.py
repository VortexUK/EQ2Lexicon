"""Typed shape for the session-user dict returned by web/auth_deps.

The two auth-dep flavours produce slightly different dicts:

  * ``require_user_session`` — session cookie only. Shape: {id, username,
    discord_name, avatar, ...}. Anything else is what Discord OAuth stashed
    at login.
  * ``require_user_session_or_token`` — either session OR an
    Authorization: Bearer header. Adds ``auth_source`` ("session"|"token"),
    and on the token path also ``token_id`` + ``token_name``.

Routes that take a ``user: dict`` parameter (30+ of them in the audit)
should annotate with ``SessionUser`` so pyright catches a ``user["i"]``
typo at type-check time. Phase 2c.6 migrates the annotations.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class _SessionUserRequired(TypedDict):
    """Required fields that every valid session-user dict must have."""

    id: str


class SessionUser(_SessionUserRequired, total=False):
    """Session-derived user shape. ``id`` is the only required field; the
    others are populated from the Discord OAuth profile and may be missing
    on legacy sessions or session-replays from third-party admin tools.

    All fields strings except where noted. ``id`` is the Discord snowflake.
    """

    username: str
    discord_name: str
    discord_username: str | None
    avatar: str | None


class _TokenUserRequired(_SessionUserRequired):
    """Required fields that every token-auth user dict must have."""


class TokenUser(_TokenUserRequired, total=False):
    """Extended shape returned by ``require_user_session_or_token`` when the
    request authenticated via a bearer token rather than a session cookie.

    The ``auth_source`` literal lets handlers gate behaviour (e.g. forbid
    destructive operations on token auth, or attribute uploads via
    ``token_name``)."""

    username: str
    discord_name: str
    discord_username: str | None
    avatar: str | None
    auth_source: Literal["session", "token"]
    token_id: int
    token_name: str | None
