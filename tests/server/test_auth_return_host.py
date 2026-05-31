"""Tests for the post-callback subdomain redirect.

Discord OAuth requires a single pre-registered ``redirect_uri``, so the
callback always lands on the parent domain (``eq2lexicon.com``). We stash
the originating subdomain in the session at ``/auth/login`` and use it to
build an absolute redirect back to where the user started."""

from __future__ import annotations

from backend.server.api import auth as auth_module

# ---------------------------------------------------------------------------
# _is_allowed_return_host — pure unit tests
# ---------------------------------------------------------------------------


def test_allowed_when_subdomain_under_parent(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    assert auth_module._is_allowed_return_host("wuoshi.eq2lexicon.com") is True


def test_allowed_when_exact_parent(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    assert auth_module._is_allowed_return_host("eq2lexicon.com") is True


def test_allowed_strips_port(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    assert auth_module._is_allowed_return_host("wuoshi.eq2lexicon.com:8000") is True


def test_rejects_unrelated_host(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    assert auth_module._is_allowed_return_host("evil.com") is False
    # Suffix-trick: must not allow "evil-eq2lexicon.com" to pass as a subdomain.
    assert auth_module._is_allowed_return_host("evileq2lexicon.com") is False
    assert auth_module._is_allowed_return_host("eq2lexicon.com.evil.com") is False


def test_rejects_empty_or_none(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    assert auth_module._is_allowed_return_host(None) is False
    assert auth_module._is_allowed_return_host("") is False


def test_dev_localhost_when_no_parent(monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_DOMAIN", raising=False)
    assert auth_module._is_allowed_return_host("localhost") is True
    assert auth_module._is_allowed_return_host("127.0.0.1") is True
    assert auth_module._is_allowed_return_host("eq2lexicon.com") is False


def test_handles_dotless_parent_format(monkeypatch):
    """SESSION_COOKIE_DOMAIN may or may not start with a dot — both forms
    must yield the same allow-list."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "eq2lexicon.com")  # no leading dot
    assert auth_module._is_allowed_return_host("wuoshi.eq2lexicon.com") is True
    assert auth_module._is_allowed_return_host("eq2lexicon.com") is True


# ---------------------------------------------------------------------------
# _post_login_redirect — direct unit tests of the redirect builder
# ---------------------------------------------------------------------------


def test_post_login_redirect_uses_absolute_url_for_trusted_host(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    r = auth_module._post_login_redirect("wuoshi.eq2lexicon.com", "/?access=approved")
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "https://wuoshi.eq2lexicon.com/?access=approved"


def test_post_login_redirect_falls_back_when_host_untrusted(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    r = auth_module._post_login_redirect("evil.com", "/")
    assert r.headers["location"] == "/"


def test_post_login_redirect_falls_back_when_host_none(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    r = auth_module._post_login_redirect(None, "/?access=pending")
    assert r.headers["location"] == "/?access=pending"
