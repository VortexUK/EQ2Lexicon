"""Tests for web/lib/audit_log.audit_log."""

from __future__ import annotations

import logging

import pytest

from web.lib.audit_log import audit_log
from web.lib.request_context import request_id_var, world_var


def test_emits_extra_with_action_and_actor(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="eq2.audit")
    audit_log("claim_approved", actor="user-123", claim_id=42)
    rec = caplog.records[-1]
    assert rec.action == "claim_approved"  # type: ignore[attr-defined]
    assert rec.actor == "user-123"  # type: ignore[attr-defined]
    assert rec.claim_id == "42"  # type: ignore[attr-defined]  # scrubbed via str()


def test_picks_request_id_and_world_from_contextvar(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="eq2.audit")
    request_id_var.set("rid-xyz")
    world_var.set("Varsoon")
    audit_log("role_granted", actor="admin-1", role="contributor")
    rec = caplog.records[-1]
    assert rec.request_id == "rid-xyz"  # type: ignore[attr-defined]
    assert rec.world == "Varsoon"  # type: ignore[attr-defined]


def test_scrubs_crlf_in_field_values(caplog: pytest.LogCaptureFixture) -> None:
    """A hostile value containing CR/LF must not produce two log lines."""
    caplog.set_level(logging.INFO, logger="eq2.audit")
    audit_log("server_settings_updated", actor="admin-1", world="Var\nsoon")
    rec = caplog.records[-1]
    assert "\n" not in rec.world  # type: ignore[attr-defined]


def test_actor_none_becomes_dash(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="eq2.audit")
    audit_log("system_migration", actor=None, step="schema_v3")
    rec = caplog.records[-1]
    assert rec.actor == "-"  # type: ignore[attr-defined]
