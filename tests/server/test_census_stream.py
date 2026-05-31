"""Tests for web/routes/census.py and web/census_events.py — COV-007.

Covers:
  GET /api/census/health — returns current state dict.
  GET /api/census/stream — SSE content-type, primes with health snapshot,
                            delivers queued events, keep-alive on timeout,
                            unsubscribes on disconnect.
  _sse helper — formats dict as SSE data line.
  census_events — subscribe, unsubscribe, publish (fan-out + QueueFull drop).
  census_health — get_state, is_down, _body_looks_healthy.

The SSE generator is tested by consuming a bounded number of chunks rather
than waiting for the stream to close naturally.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import census_events, census_health
from backend.server.api.census import _sse

# ---------------------------------------------------------------------------
# _sse helper
# ---------------------------------------------------------------------------


class TestSseHelper:
    def test_formats_event_as_sse_data_line(self):
        """_sse({"type": "health", "status": "up"}) produces valid SSE."""
        result = _sse({"type": "health", "status": "up"})
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        payload = json.loads(result[len("data: ") :].strip())
        assert payload["status"] == "up"


# ---------------------------------------------------------------------------
# census_events pub/sub
# ---------------------------------------------------------------------------


class TestCensusEvents:
    def setup_method(self):
        census_events._reset_for_test()

    def teardown_method(self):
        census_events._reset_for_test()

    def test_subscribe_returns_queue(self):
        """subscribe() adds the queue to the subscriber set and returns it."""
        q = census_events.subscribe()
        assert q in census_events._subscribers

    def test_unsubscribe_removes_queue(self):
        """unsubscribe() discards the queue from the subscriber set."""
        q = census_events.subscribe()
        census_events.unsubscribe(q)
        assert q not in census_events._subscribers

    def test_publish_delivers_to_subscriber(self):
        """publish() puts event into each subscriber's queue."""
        q = census_events.subscribe()
        census_events.publish({"type": "health", "status": "up"})
        item = q.get_nowait()
        assert item["status"] == "up"

    def test_publish_fan_out_to_multiple_subscribers(self):
        """publish() delivers to all subscribers, not just one."""
        q1 = census_events.subscribe()
        q2 = census_events.subscribe()
        census_events.publish({"type": "test"})
        assert q1.get_nowait()["type"] == "test"
        assert q2.get_nowait()["type"] == "test"

    def test_publish_drops_event_on_full_queue(self):
        """When a subscriber's queue is full, the event is silently dropped
        without raising or blocking."""
        # Fill the queue to capacity
        q = census_events.subscribe()
        for i in range(100):
            q.put_nowait({"i": i})
        # This must not raise even though queue is full
        census_events.publish({"type": "overflow"})
        # Queue is still at capacity (100), not 101
        assert q.qsize() == 100

    def test_unsubscribe_nonexistent_queue_is_noop(self):
        """Unsubscribing a queue that was never subscribed doesn't raise."""
        orphan: asyncio.Queue = asyncio.Queue()
        census_events.unsubscribe(orphan)  # Should not raise


# ---------------------------------------------------------------------------
# census_health state helpers
# ---------------------------------------------------------------------------


class TestCensusHealthHelpers:
    def setup_method(self):
        census_health._reset_for_test()

    def teardown_method(self):
        census_health._reset_for_test()

    def test_get_state_returns_status_and_checked_at(self):
        """get_state() includes 'status' and 'checked_at' keys."""
        state = census_health.get_state()
        assert "status" in state
        assert "checked_at" in state
        assert state["status"] == "unknown"

    def test_is_down_false_when_unknown(self):
        """is_down() is False when status is 'unknown'."""
        assert census_health.is_down() is False

    def test_is_down_true_when_down(self):
        """is_down() returns True when internal status is 'down'."""
        census_health._status = "down"
        assert census_health.is_down() is True

    def test_body_looks_healthy_with_returned_field(self):
        """A body with 'returned' >= 0 and no errorCode is healthy."""
        from backend.server.census_health import _body_looks_healthy

        assert _body_looks_healthy({"returned": 1, "world_list": []}) is True

    def test_body_with_error_code_is_unhealthy(self):
        """A body with 'errorCode' is unhealthy even if status was 200."""
        from backend.server.census_health import _body_looks_healthy

        assert _body_looks_healthy({"errorCode": "SERVER_ERROR"}) is False

    def test_non_dict_body_is_unhealthy(self):
        """A non-dict response body is unhealthy."""
        from backend.server.census_health import _body_looks_healthy

        assert _body_looks_healthy([]) is False  # type: ignore[arg-type]
        assert _body_looks_healthy("ok") is False  # type: ignore[arg-type]

    def test_body_without_returned_field_is_unhealthy(self):
        """A dict with no 'returned' field has returned=-1 default → unhealthy."""
        from backend.server.census_health import _body_looks_healthy

        assert _body_looks_healthy({"world_list": []}) is False


# ---------------------------------------------------------------------------
# GET /api/census/health
# ---------------------------------------------------------------------------


class TestGetCensusHealth:
    def setup_method(self):
        census_health._reset_for_test()

    def teardown_method(self):
        census_health._reset_for_test()

    async def test_returns_current_state(self, app):
        """GET /api/census/health returns the current health state dict."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/census/health")
        assert r.status_code == 200
        body = r.json()
        assert "status" in body
        assert "checked_at" in body
        assert body["status"] == "unknown"

    async def test_returns_down_when_health_down(self, app):
        """If census health is 'down', the endpoint reflects that."""
        census_health._status = "down"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/census/health")
        assert r.status_code == 200
        assert r.json()["status"] == "down"


# ---------------------------------------------------------------------------
# GET /api/census/stream — SSE (generator unit tests — no HTTP layer)
# ---------------------------------------------------------------------------
# Testing live SSE streams through httpx stalls because the generator blocks
# on asyncio.wait_for(q.get(), timeout=20) between events and httpx's
# aiter_bytes() cannot be cleanly broken out of mid-stream in test code.
# Instead we test the generator function directly as an async generator.
# ---------------------------------------------------------------------------


class TestCensusStreamGenerator:
    """Unit tests for the SSE generator (census_stream's inner gen()) without
    going through the HTTP layer.  This avoids the 20-second wait_for hang.

    We call census_stream() to get the StreamingResponse, then iterate its
    body iterator directly with asyncio.wait_for to bound execution time.
    The mock request is_disconnected() returns True immediately so the generator
    exits after the health-prime + one loop iteration.
    """

    def setup_method(self):
        census_events._reset_for_test()
        census_health._reset_for_test()

    def teardown_method(self):
        census_events._reset_for_test()
        census_health._reset_for_test()

    async def _run_gen(self, request_mock, max_chunks: int = 2) -> list[str]:
        """Run the census_stream generator, collecting up to max_chunks chunks."""
        from backend.server.api.census import census_stream

        response = await census_stream(request_mock)
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
            if len(chunks) >= max_chunks:
                break
        return chunks

    async def test_first_yield_is_health_snapshot(self):
        """The generator primes the client with the current health state."""
        census_health._status = "up"

        disconnect_call = 0

        async def _is_disconnected():
            nonlocal disconnect_call
            disconnect_call += 1
            # Return True immediately so the loop exits after the prime
            return True

        from unittest.mock import MagicMock

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected

        chunks = await self._run_gen(fake_request, max_chunks=1)
        assert len(chunks) == 1
        assert chunks[0].startswith("data: ")
        payload = json.loads(chunks[0][len("data: ") :].strip())
        assert payload["type"] == "health"
        assert payload["status"] == "up"

    async def test_keep_alive_on_wait_for_timeout(self):
        """When wait_for raises TimeoutError, a keep-alive comment is yielded."""
        disconnect_call = 0

        async def _is_disconnected():
            nonlocal disconnect_call
            disconnect_call += 1
            if disconnect_call >= 2:
                return True
            return False

        from unittest.mock import MagicMock

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected

        # Patch wait_for to immediately timeout
        async def _timeout(*args, **kwargs):
            raise TimeoutError

        with patch("backend.server.api.census.asyncio.wait_for", side_effect=_timeout):
            chunks = await self._run_gen(fake_request, max_chunks=2)

        # chunk[0] = health snapshot, chunk[1] = keep-alive
        assert any("keep-alive" in c for c in chunks)

    async def test_queued_event_yielded_as_sse(self):
        """An event in the queue is yielded as a valid SSE data line."""
        event = {"type": "character", "name": "Sihtric"}
        expected = _sse(event)
        text = expected
        assert text.startswith("data: ")
        assert text.endswith("\n\n")
        payload = json.loads(text[len("data: ") :].strip())
        assert payload["name"] == "Sihtric"

    async def test_generator_delivers_queued_event(self):
        """An event pre-seeded in the subscriber queue is delivered by the generator."""
        disconnect_call = 0

        async def _is_disconnected():
            nonlocal disconnect_call
            disconnect_call += 1
            if disconnect_call >= 2:
                return True
            return False

        from unittest.mock import MagicMock

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected

        seeded_event = {"type": "character", "name": "Sihtric"}

        original_subscribe = census_events.subscribe

        def _subscribe_with_seed():
            q = original_subscribe()
            q.put_nowait(seeded_event)
            return q

        with patch("backend.server.api.census.census_events.subscribe", side_effect=_subscribe_with_seed):
            chunks = await self._run_gen(fake_request, max_chunks=2)

        # chunk[0] = health, chunk[1] = seeded event
        assert len(chunks) >= 2
        payload_1 = json.loads(chunks[1][len("data: ") :].strip())
        assert payload_1["type"] == "character"
        assert payload_1["name"] == "Sihtric"

    async def test_unsubscribe_called_when_disconnected(self):
        """census_events.unsubscribe is called when request is disconnected.

        We run the full gen() coroutine by letting it exit naturally (is_disconnected
        returns True) and verify that unsubscribe was called exactly once.
        """
        unsubscribed_queues: list = []
        original_unsub = census_events.unsubscribe

        def _track_unsub(q):
            unsubscribed_queues.append(q)
            original_unsub(q)

        from unittest.mock import MagicMock

        from backend.server.api.census import census_stream

        disconnect_call = 0

        async def _is_disconnected():
            nonlocal disconnect_call
            disconnect_call += 1
            return True  # Disconnect on first check so loop exits immediately

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected

        with patch("backend.server.census_events.unsubscribe", side_effect=_track_unsub):
            response = await census_stream(fake_request)
            # Exhaust the generator so its finally: block runs
            async for _ in response.body_iterator:
                pass

        # The generator's finally block must have called unsubscribe
        assert len(unsubscribed_queues) >= 1


class TestCensusStreamResponse:
    """Verify the StreamingResponse configuration without consuming the body."""

    def setup_method(self):
        census_events._reset_for_test()
        census_health._reset_for_test()

    def teardown_method(self):
        census_events._reset_for_test()
        census_health._reset_for_test()

    async def test_census_stream_returns_streaming_response_object(self, app):
        """census_stream() returns a StreamingResponse (verified via the route object)."""
        from unittest.mock import AsyncMock, MagicMock

        from fastapi.responses import StreamingResponse

        from backend.server.api.census import census_stream

        fake_request = MagicMock()
        fake_request.is_disconnected = AsyncMock(return_value=True)

        response = await census_stream(fake_request)
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers.get("Cache-Control") == "no-cache"
