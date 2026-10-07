"""Tests for the optional `client_warnings` field on POST /parses/ingest: a valid list
round-trips as JSON, absent or empty stores NULL (older plugins never send it), and
entries are sanitised (empties dropped, long ones truncated, duplicates deduped).
"""

from __future__ import annotations

import json

import pytest

from backend.server.parses import db as parses_db
from tests.server._parses_ingest_fixtures import _minimal_payload


def _read_client_warnings(conn, encounter_id: int) -> str | None:
    """Return the raw client_warnings column for a row."""
    row = conn.execute(
        "SELECT client_warnings FROM encounters WHERE id = %s",
        (encounter_id,),
    ).fetchone()
    return None if row is None else row["client_warnings"]


def _ingest_and_get(payload: dict) -> int:
    """Drive ``_ingest_payload_sync`` against the leased scratch schema (the
    ``parses_db_path`` fixture has already re-pointed the shared store) and
    return the new encounter id. Reads go through ``parses_db.store.init_db()``
    which targets the same schema."""
    from backend.server.api.parses import IngestRequest
    from backend.server.api.parses.ingest import _ingest_payload_sync

    req = IngestRequest(**payload)
    status, eid, *_ = _ingest_payload_sync(req, "Menludiir", "Exordium", "plugin:123", {})
    assert status == "inserted"
    assert eid is not None
    return eid


def test_client_warnings_persisted_as_json(parses_db_path):
    payload = _minimal_payload()
    payload["client_warnings"] = ["folder_hint_mismatch"]

    eid = _ingest_and_get(payload)

    conn = parses_db.store.init_db()
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is not None
    decoded = json.loads(raw)
    assert decoded == ["folder_hint_mismatch"]


def test_client_warnings_absent_leaves_column_null(parses_db_path):
    """The plugin omits the key entirely when there's nothing to flag.
    That's the resting state for non-tampered uploads — column stays
    NULL so the admin UI can use a single `warnings?.length` check to
    decide whether to render the ⚠ chip."""
    payload = _minimal_payload()
    assert "client_warnings" not in payload  # baseline

    eid = _ingest_and_get(payload)

    conn = parses_db.store.init_db()
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is None


def test_client_warnings_empty_list_stored_as_null(parses_db_path):
    """An empty array round-trips to NULL, NOT to "[]". Either form means
    "no warnings" semantically, but NULL is the canonical resting state
    and lets the admin column-filter just check IS NULL / IS NOT NULL."""
    payload = _minimal_payload()
    payload["client_warnings"] = []

    eid = _ingest_and_get(payload)

    conn = parses_db.store.init_db()
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is None


def test_client_warnings_sanitises_entries(parses_db_path):
    """Defence-in-depth sanitisation at storage time. The plugin already
    enforces these caps client-side; we re-enforce on ingest so a
    tampered build of the plugin can't blow them past:
      * empty/whitespace entries dropped
      * entries longer than 64 chars truncated to 64
      * duplicate entries deduped (case-sensitive — codes are stable
        ASCII so this matches a future code being literally identical)
    """
    payload = _minimal_payload()
    payload["client_warnings"] = [
        "folder_hint_mismatch",
        "",
        "   ",
        "folder_hint_mismatch",  # exact dupe
        "x" * 100,  # over-long
    ]

    eid = _ingest_and_get(payload)

    conn = parses_db.store.init_db()
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is not None
    decoded = json.loads(raw)
    # Order is preserved: the duplicate's second occurrence is dropped,
    # the long one is truncated to 64 chars, empty/whitespace are removed.
    assert decoded == ["folder_hint_mismatch", "x" * 64]


def test_client_warnings_pydantic_rejects_oversized_list():
    """The Pydantic validator on IngestRequest caps the list at 32 entries
    — a malformed/hostile payload doesn't reach the storage layer at all.
    Pin that ValidationError raises so the cap survives future refactors."""
    from pydantic import ValidationError

    from backend.server.api.parses import IngestRequest

    payload = _minimal_payload()
    payload["client_warnings"] = ["folder_hint_mismatch"] * 33  # 1 over the cap

    with pytest.raises(ValidationError):
        IngestRequest(**payload)
