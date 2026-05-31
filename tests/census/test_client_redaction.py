"""Tests for census.client URL redaction + TraceConfig + session lifecycle.

Phase 1 shipped only _redact_url. Phase 3.5 extends to _build_trace_config
(smoke test) and the _session_ lazy-creation + reopen-after-close lifecycle.

Security contract: the SERVICE_ID segment of Census URLs (/s:<id>/) must
never appear in log output at INFO or above. _redact_url is the single
choke-point; these tests pin its behaviour so a refactor can't accidentally
reintroduce the leakage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.census.client import CensusClient, _build_trace_config, _redact_url


class TestRedactUrl:
    def test_redacts_service_id_in_canonical_url(self):
        """The /s:<service_id>/ segment is replaced with /s:REDACTED/."""
        url = "https://census.daybreakgames.com/s:my-secret-key/json/get/eq2/item/?id=1"
        result = _redact_url(url)
        assert result == "https://census.daybreakgames.com/s:REDACTED/json/get/eq2/item/?id=1"

    def test_redacts_service_id_with_special_chars(self):
        """Service IDs with dots, dashes, and underscores are fully removed."""
        url = "https://census.daybreakgames.com/s:key.with-dashes_and_dots/json/get/eq2/"
        result = _redact_url(url)
        assert "REDACTED" in result
        assert "key.with-dashes_and_dots" not in result

    def test_passes_through_url_without_service_id_segment(self):
        """A URL lacking the /s:<id>/ pattern is returned unchanged."""
        url = "https://example.com/no-service-id-here/"
        assert _redact_url(url) == url

    def test_redacts_only_the_service_id_path_segment(self):
        """Only the /s:<id>/ segment is scrubbed; query-string occurrences of 's:'
        are NOT mangled because the regex requires a trailing slash."""
        url = "https://census.daybreakgames.com/s:secret/json/get/eq2/item/?id=1&extra=s:not-a-key"
        result = _redact_url(url)
        assert "/s:REDACTED/" in result
        # The query-string 's:not-a-key' has no trailing slash so the regex
        # won't match it — it must be preserved verbatim.
        assert "extra=s:not-a-key" in result

    def test_output_contains_no_original_service_id(self):
        """The redacted URL must not contain any part of the real service ID."""
        url = "https://census.daybreakgames.com/s:super-secret-prod-key/json/get/eq2/character/"
        result = _redact_url(url)
        assert "super-secret-prod-key" not in result
        assert "/s:REDACTED/" in result

    def test_redacted_url_preserves_full_path_and_query(self):
        """Path and query params are intact after redaction — only the service segment changes."""
        url = "https://census.daybreakgames.com/s:key123/json/get/eq2/guild/?name=Exordium&world=Varsoon"
        result = _redact_url(url)
        assert result == "https://census.daybreakgames.com/s:REDACTED/json/get/eq2/guild/?name=Exordium&world=Varsoon"


# ---------------------------------------------------------------------------
# _build_trace_config (smoke test)
# ---------------------------------------------------------------------------


class TestBuildTraceConfig:
    def test_returns_trace_config_object(self):
        """_build_trace_config returns an aiohttp.TraceConfig (smoke test)."""
        import aiohttp

        tc = _build_trace_config()
        assert isinstance(tc, aiohttp.TraceConfig)

    def test_trace_config_has_hooks_attached(self):
        """The TraceConfig has at least one hook in the on_request_start/end/exception lists."""
        tc = _build_trace_config()
        # aiohttp TraceConfig stores hooks as Signal objects; check they're non-empty
        assert len(tc.on_request_start) > 0  # type: ignore[arg-type]
        assert len(tc.on_request_end) > 0  # type: ignore[arg-type]
        assert len(tc.on_request_exception) > 0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CensusClient._session_ lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_creates_session_on_first_call(self):
        client = CensusClient(service_id="test")
        assert client._session is None
        session = client._session_()
        assert session is not None
        assert client._session is session
        await client.close()

    @pytest.mark.asyncio
    async def test_reuses_open_session(self):
        client = CensusClient(service_id="test")
        s1 = client._session_()
        s2 = client._session_()
        assert s1 is s2
        await client.close()

    @pytest.mark.asyncio
    async def test_reopens_session_after_close(self):
        """After close(), _session_ creates a fresh ClientSession."""
        client = CensusClient(service_id="test")
        s1 = client._session_()
        await client.close()
        assert client._session.closed  # type: ignore[union-attr]
        s2 = client._session_()
        assert s2 is not s1
        assert not s2.closed
        await client.close()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """Calling close() twice should not raise."""
        client = CensusClient(service_id="test")
        client._session_()
        await client.close()
        await client.close()  # second call — should not raise
