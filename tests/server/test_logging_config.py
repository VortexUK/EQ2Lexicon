"""Tests for backend/core/logging_config.configure_logging."""

from __future__ import annotations

import logging

import pytest

from backend.core import logging_config


def test_default_level_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    logging_config.configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_log_level_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logging_config.configure_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_invalid_log_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "BANANAS")
    logging_config.configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_third_party_loggers_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    logging_config.configure_logging()
    assert logging.getLogger("discord").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("aiohttp.access").level == logging.WARNING


def test_json_format_produces_json_records(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "json")
    logging_config.configure_logging()
    logging.getLogger("eq2.test").info("hello world")
    captured = capsys.readouterr()
    # Last record (startup line first, then our hello). Find the "hello" line.
    lines = [line for line in captured.err.splitlines() if "hello world" in line]
    assert lines, captured.err
    parsed = json.loads(lines[-1])
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
