"""Tests for web/lib/request_context — contextvar + adapter + filter shape."""

from __future__ import annotations

import logging

import pytest

from backend.server.core.request_context import (
    RequestContextFilter,
    get_logger,
    request_id_var,
    user_id_var,
    world_var,
)


def test_adapter_injects_contextvars(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    request_id_var.set("rid-test")
    user_id_var.set("uid-test")
    world_var.set("Varsoon")

    _log = get_logger("test.adapter")
    _log.info("hello")

    rec = caplog.records[-1]
    assert rec.request_id == "rid-test"  # type: ignore[attr-defined]
    assert rec.user_id == "uid-test"  # type: ignore[attr-defined]
    assert rec.world == "Varsoon"  # type: ignore[attr-defined]


def test_adapter_caller_extra_wins(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    request_id_var.set("rid-outer")
    _log = get_logger("test.adapter.collision")
    _log.info("hello", extra={"request_id": "rid-override"})

    rec = caplog.records[-1]
    assert rec.request_id == "rid-override"  # type: ignore[attr-defined]


def test_filter_sets_defaults_outside_request(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    # Reset contextvars to default.
    request_id_var.set(None)
    user_id_var.set(None)
    world_var.set(None)

    flt = RequestContextFilter()
    rec = logging.LogRecord(
        name="test.filter",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hi",
        args=(),
        exc_info=None,
    )
    assert flt.filter(rec) is True
    assert rec.request_id == "-"  # type: ignore[attr-defined]
    assert rec.user_id == "-"  # type: ignore[attr-defined]
    assert rec.world == "-"  # type: ignore[attr-defined]


def test_filter_preserves_caller_set_extra() -> None:
    """If a log call already specifies request_id via extra=, the filter
    must not overwrite it. Caller wins."""
    flt = RequestContextFilter()
    rec = logging.LogRecord(
        name="test.filter.preserve",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hi",
        args=(),
        exc_info=None,
    )
    rec.request_id = "rid-explicit"  # type: ignore[attr-defined]
    flt.filter(rec)
    assert rec.request_id == "rid-explicit"  # type: ignore[attr-defined]
