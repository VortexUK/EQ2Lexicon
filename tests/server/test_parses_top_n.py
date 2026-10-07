"""Unit tests for the top-N ally encDPS helpers in backend/server/api/parses/list.py,
which feed the merger's mutual-containment gate.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.server.api.parses.list import _all_ally_names, _top_n_ally_names


@pytest.fixture
def conn(parses_db_conn: Any) -> Any:
    """Scratch parses-schema connection (dict rows, ``%s`` params).

    ``_insert`` stubs an encounter row per encounter_id (FK) and sets is_player
    for single-word, non-empty, non-'Unknown' names.
    """
    return parses_db_conn


def _insert(conn: Any, **kwargs) -> None:
    # Derive is_player from the name (single-word, non-empty, not 'Unknown')
    # so the multi-word/Unknown test cases exercise the is_player-based SQL helpers.
    name = kwargs["name"]
    ally = kwargs["ally"]
    encounter_id = kwargs["encounter_id"]
    is_player = 1 if (ally == 1 and name and name != "Unknown" and " " not in name) else 0
    conn.execute(
        "INSERT INTO encounters (id, act_encid, title, started_at, ended_at, duration_s, source_dsn, ingested_at) "
        "OVERRIDING SYSTEM VALUE VALUES (%s, %s, 'Fight', 0, 0, 0, 'test', 0) "
        "ON CONFLICT (id) DO NOTHING",
        (encounter_id, f"enc-{encounter_id}"),
    )
    conn.execute(
        "INSERT INTO combatants (encounter_id, name, ally, encdps, is_player) VALUES (%s, %s, %s, %s, %s)",
        (encounter_id, name, ally, kwargs["encdps"], is_player),
    )


def test_top_n_returns_top_three_by_encdps_desc(conn):
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=1, name="Bravo", ally=1, encdps=8000.0)
    _insert(conn, encounter_id=1, name="Charlie", ally=1, encdps=7000.0)
    _insert(conn, encounter_id=1, name="Delta", ally=1, encdps=6000.0)
    assert _top_n_ally_names(conn, 1, 3) == {"Alpha", "Bravo", "Charlie"}


def test_top_n_tiebreaker_is_name_ascending(conn):
    # Three combatants tied at the bottom slot — name ASC settles it.
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=1, name="Zeta", ally=1, encdps=5000.0)
    _insert(conn, encounter_id=1, name="Bravo", ally=1, encdps=5000.0)
    _insert(conn, encounter_id=1, name="Mike", ally=1, encdps=5000.0)
    # With N=2: Alpha is clear; tied second slot picks 'Bravo' (ASC).
    assert _top_n_ally_names(conn, 1, 2) == {"Alpha", "Bravo"}


def test_top_n_excludes_non_ally_rows(conn):
    _insert(conn, encounter_id=1, name="Player", ally=1, encdps=8000.0)
    _insert(conn, encounter_id=1, name="Mob", ally=0, encdps=100000.0)
    assert _top_n_ally_names(conn, 1, 3) == {"Player"}


def test_top_n_excludes_unknown_and_empty_names(conn):
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=1, name="Unknown", ally=1, encdps=8000.0)
    _insert(conn, encounter_id=1, name="", ally=1, encdps=7000.0)
    assert _top_n_ally_names(conn, 1, 3) == {"Alpha"}


def test_top_n_excludes_multi_word_names(conn):
    # Pets / NPCs typically have multi-word names — single-word filter
    # is the existing rule from _PLAYER_COUNT_SQL.
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=1, name="a krait warrior", ally=1, encdps=5000.0)
    _insert(conn, encounter_id=1, name="Bravo's Pet", ally=1, encdps=4000.0)
    assert _top_n_ally_names(conn, 1, 3) == {"Alpha"}


def test_top_n_returns_fewer_when_pool_is_smaller(conn):
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=1, name="Bravo", ally=1, encdps=8000.0)
    # Asking for 5 from a pool of 2 — returns the pool.
    assert _top_n_ally_names(conn, 1, 5) == {"Alpha", "Bravo"}


def test_top_n_returns_empty_set_for_no_allies(conn):
    _insert(conn, encounter_id=1, name="Mob", ally=0, encdps=10000.0)
    assert _top_n_ally_names(conn, 1, 3) == set()


def test_top_n_scopes_to_encounter_id(conn):
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=2, name="Bravo", ally=1, encdps=9000.0)
    assert _top_n_ally_names(conn, 1, 3) == {"Alpha"}
    assert _top_n_ally_names(conn, 2, 3) == {"Bravo"}


def test_all_ally_names_returns_every_qualifying_ally(conn):
    _insert(conn, encounter_id=1, name="Alpha", ally=1, encdps=9000.0)
    _insert(conn, encounter_id=1, name="Bravo", ally=1, encdps=8000.0)
    _insert(conn, encounter_id=1, name="Charlie", ally=1, encdps=7000.0)
    _insert(conn, encounter_id=1, name="Mob", ally=0, encdps=10000.0)
    _insert(conn, encounter_id=1, name="Unknown", ally=1, encdps=6000.0)
    _insert(conn, encounter_id=1, name="a krait warrior", ally=1, encdps=5000.0)
    assert _all_ally_names(conn, 1) == {"Alpha", "Bravo", "Charlie"}


def test_all_ally_names_returns_empty_when_no_qualifying(conn):
    _insert(conn, encounter_id=1, name="Mob", ally=0, encdps=10000.0)
    assert _all_ally_names(conn, 1) == set()
