"""Tests for the optional `client_warnings` field on POST /parses/ingest.

The plugin (v0.1.15+) attaches a list of soft-warning codes when something
looks off but isn't bad enough to block the upload — currently just
``folder_hint_mismatch``. The field is purely additive: old plugins
never send it, and the column stays NULL for those rows. We pin:

  * round-trip: a valid list is persisted as a JSON-encoded string
  * absence: omitting the field leaves the column NULL
  * empty list: stored as NULL (same resting state as absent)
  * sanitisation: empty entries dropped, over-long entries truncated,
    duplicates deduped, list-cap enforced upstream by Pydantic
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.server.parses import db as parses_db
from tests.server._parses_ingest_fixtures import _minimal_payload


def _read_client_warnings(conn, encounter_id: int) -> str | None:
    """Return the raw client_warnings column for a row."""
    row = conn.execute(
        "SELECT client_warnings FROM encounters WHERE id = ?",
        (encounter_id,),
    ).fetchone()
    return None if row is None else row[0]


def _ingest_and_get(payload: dict, *, tmp_path, monkeypatch) -> tuple[int, Path]:
    """Drive ``_ingest_payload_sync`` against a fresh tmp DB and return
    (encounter_id, db_file). The db_file path MUST be passed explicitly
    when reading back — ``parses_db.init_db()``'s ``path: Path = DB_PATH``
    default arg captures DB_PATH at function-definition time, so
    monkey-patching the module attribute doesn't affect the no-arg call.
    All reads in these tests therefore go through ``init_db(db_file)``.
    """
    from backend.server.api.parses import IngestRequest
    from backend.server.api.parses.ingest import _ingest_payload_sync

    db_file: Path = tmp_path / "backend.server.parses.db"
    monkeypatch.setattr(parses_db, "DB_PATH", db_file)
    parses_db.init_db(db_file).close()

    req = IngestRequest(**payload)
    status, eid, *_ = _ingest_payload_sync(req, "Menludiir", "Exordium", "plugin:123", {})
    assert status == "inserted"
    assert eid is not None
    return eid, db_file


def test_client_warnings_persisted_as_json(tmp_path, monkeypatch):
    payload = _minimal_payload()
    payload["client_warnings"] = ["folder_hint_mismatch"]

    eid, db_file = _ingest_and_get(payload, tmp_path=tmp_path, monkeypatch=monkeypatch)

    conn = parses_db.init_db(db_file)
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is not None
    decoded = json.loads(raw)
    assert decoded == ["folder_hint_mismatch"]


def test_client_warnings_absent_leaves_column_null(tmp_path, monkeypatch):
    """The plugin omits the key entirely when there's nothing to flag.
    That's the resting state for non-tampered uploads — column stays
    NULL so the admin UI can use a single `warnings?.length` check to
    decide whether to render the ⚠ chip."""
    payload = _minimal_payload()
    assert "client_warnings" not in payload  # baseline

    eid, db_file = _ingest_and_get(payload, tmp_path=tmp_path, monkeypatch=monkeypatch)

    conn = parses_db.init_db(db_file)
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is None


def test_client_warnings_empty_list_stored_as_null(tmp_path, monkeypatch):
    """An empty array round-trips to NULL, NOT to "[]". Either form means
    "no warnings" semantically, but NULL is the canonical resting state
    and lets the admin column-filter just check IS NULL / IS NOT NULL."""
    payload = _minimal_payload()
    payload["client_warnings"] = []

    eid, db_file = _ingest_and_get(payload, tmp_path=tmp_path, monkeypatch=monkeypatch)

    conn = parses_db.init_db(db_file)
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is None


def test_client_warnings_sanitises_entries(tmp_path, monkeypatch):
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

    eid, db_file = _ingest_and_get(payload, tmp_path=tmp_path, monkeypatch=monkeypatch)

    conn = parses_db.init_db(db_file)
    try:
        raw = _read_client_warnings(conn, eid)
    finally:
        conn.close()

    assert raw is not None
    decoded = json.loads(raw)
    # Order is preserved: the duplicate's second occurrence is dropped,
    # the long one is truncated to 64 chars, empty/whitespace are removed.
    assert decoded == ["folder_hint_mismatch", "x" * 64]


def test_client_warnings_pydantic_rejects_oversized_list(tmp_path, monkeypatch):
    """The Pydantic validator on IngestRequest caps the list at 32 entries
    — a malformed/hostile payload doesn't reach the storage layer at all.
    Pin that ValidationError raises so the cap survives future refactors."""
    from pydantic import ValidationError

    from backend.server.api.parses import IngestRequest

    payload = _minimal_payload()
    payload["client_warnings"] = ["folder_hint_mismatch"] * 33  # 1 over the cap

    with pytest.raises(ValidationError):
        IngestRequest(**payload)
