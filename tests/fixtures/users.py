"""Shared test factories for SessionUser-shaped fakes.

Resolves TEST-001 (5+ files redeclaring `_fake_admin`) and TEST-031
(test fakes returning untyped dicts instead of SessionUser).

Usage in tests:

    from tests.fixtures.users import make_fake_admin, make_fake_user

    fake_admin = make_fake_admin()
    fake_user = make_fake_user(id="123456789")

    with patch("backend.server.api.admin._require_admin", lambda request=None: fake_admin):
        ...

Or, for routes that use the `_require_user(request)` signature:

    from tests.fixtures.users import make_fake_require_user, make_fake_user

    _fake_require_user = make_fake_require_user(make_fake_user())
"""

from __future__ import annotations

from collections.abc import Callable

from backend.server.core.session_user import SessionUser


def make_fake_admin(id: str = "admin-1", username: str = "boss") -> SessionUser:
    """Return a SessionUser-shaped dict for an admin fixture user.

    Defaults match the shape the original `_fake_admin` definitions used
    across tests/web/test_admin_*.py and tests/web/test_role_requests.py.
    """
    return SessionUser(id=id, username=username)


def make_fake_user(id: str = "user-1", username: str = "testuser") -> SessionUser:
    """Return a SessionUser-shaped dict for a non-admin fixture user."""
    return SessionUser(id=id, username=username)


def make_fake_require_user(user: SessionUser) -> Callable[..., SessionUser]:
    """Build a `_require_user(request=None)`-shaped callable that returns `user`.

    Matches the signature of `web.auth_deps._require_user` (and the
    `_require_user` re-exports in route modules). Use with `patch(...)`:

        fake_require_user = make_fake_require_user(make_fake_user())
        with patch("backend.server.api.parses.list._require_user", fake_require_user):
            ...
    """

    def _fake_require_user(request: object = None) -> SessionUser:
        return user

    return _fake_require_user
