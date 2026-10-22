"""An upload's end time is never stored before its start.

EQ2Parser up to v0.6.1 sent year 1 (.NET's empty date) as the end of a fight
in which no ally dealt damage. 1,780 such rows were stored by 2026-10-22, and
attendance reconstruction crashed on one of them.
"""

from __future__ import annotations

from backend.server.api.parses.ingest import _encounter_from_payload
from backend.server.api.parses.models import IngestEncounter
from backend.server.parses.models import _to_unix


def _enc(start: str, end: str, duration: int = 0) -> IngestEncounter:
    return IngestEncounter(encid="e1", title="Unknown", starttime=start, endtime=end, duration=duration)


def test_a_year_one_end_becomes_the_start():
    enc = _encounter_from_payload(_enc("2026-10-16T19:48:00Z", "0001-01-01T00:00:00Z"))
    assert enc is not None
    assert _to_unix(enc.ended_at) == _to_unix(enc.started_at)


def test_a_normal_end_is_kept():
    enc = _encounter_from_payload(_enc("2026-10-16T19:48:00Z", "2026-10-16T19:52:30Z", duration=270))
    assert enc is not None
    assert _to_unix(enc.ended_at) - _to_unix(enc.started_at) == 270
