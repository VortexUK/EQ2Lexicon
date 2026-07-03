"""Unit tests for backend/server/core/twitch.py."""

from __future__ import annotations

from backend.server.core.twitch import is_blocked, parse_twitch_login


def test_parse_accepts_twitch_channel_urls():
    assert parse_twitch_login("https://twitch.tv/SomeChannel") == "somechannel"
    assert parse_twitch_login("https://www.twitch.tv/Some_Chan") == "some_chan"
    assert parse_twitch_login("twitch.tv/foobar") == "foobar"
    assert parse_twitch_login("http://twitch.tv/foobar/") == "foobar"
    assert parse_twitch_login("https://twitch.tv/foobar?referrer=x") == "foobar"


def test_parse_rejects_non_twitch_or_malformed():
    assert parse_twitch_login(None) is None
    assert parse_twitch_login("") is None
    assert parse_twitch_login("https://youtube.com/foo") is None
    assert parse_twitch_login("https://twitch.tv/foo") is None  # <4 chars
    assert parse_twitch_login("https://twitch.tv/videos/12345") is None  # path, not a channel
    assert parse_twitch_login("not a url") is None
    assert parse_twitch_login("https://evil.com/twitch.tv/foobar") is None


def test_is_blocked_matches_the_blocklist_substring():
    assert is_blocked("cleanchannel") is None
    assert is_blocked("pornstreamer") == "porn"  # substring hit
    assert is_blocked("PORNguy") == "porn"  # case-insensitive
