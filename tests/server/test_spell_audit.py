"""Spell audit (backend/server/spell_audit.py): flagged characters' parses are
barred from the RANKINGS and reported — never hidden, deleted or refused.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.census.models import CharacterSpells, SpellEntry
from backend.server import spell_audit
from backend.server.parses import db as parses_db
from backend.server.parses import fights
from tests.fixtures.users import make_fake_admin
from tests.server._parses_ingest_fixtures import _fake_require_user, _minimal_payload, _signed_post_kwargs

WORLD = "Varsoon"
SINCE = spell_audit.since_ts()


@pytest.fixture(autouse=True)
def _schema(parses_db_path: str) -> str:
    return parses_db_path


@pytest.fixture(autouse=True)
def _no_rankings_refresh():
    with patch("backend.server.spell_audit._refresh_after_change", new=AsyncMock()):
        yield


def _conn() -> Any:
    return parses_db.store.init_db()


def _seed(conn: Any, *, started_at: int, players: list[str], uploaded_by: str = "Up", title: str = "Tarinax") -> int:
    row = conn.execute(
        "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, duration_s, success_level,"
        " source_dsn, uploaded_by, guild_name, ingested_at)"
        " VALUES (%s, %s, %s, 'Vetrovia', %s, %s, 60, 1, 'plugin:disc-9', %s, 'Exordium', %s) RETURNING id",
        (WORLD, f"enc-{started_at}-{uploaded_by}", title, started_at, started_at + 60, uploaded_by, started_at),
    ).fetchone()
    eid = row["id"]
    for i, name in enumerate(players):
        conn.execute(
            "INSERT INTO combatants (encounter_id, name, ally, is_player, encdps, damage) VALUES (%s, %s, 1, 1, %s, %s)",
            (eid, name, float(100 - i), 1000 - i),
        )
    fights.attach_encounter(conn, eid)
    conn.commit()
    return eid


def _state(conn: Any, eid: int) -> dict:
    """(visible?, bar reason, is its fight's ranking primary?)"""
    conn.rollback()
    r = conn.execute(
        "SELECT e.hidden_at, e.ranking_barred_reason, f.primary_winning_encounter_id AS pw"
        " FROM encounters e LEFT JOIN fights f ON f.id = e.fight_id WHERE e.id = %s",
        (eid,),
    ).fetchone()
    return {"visible": r["hidden_at"] is None, "barred": r["ranking_barred_reason"], "ranks": r["pw"] == eid}


def _reports(conn: Any) -> list[dict]:
    conn.rollback()
    return [
        dict(r)
        for r in conn.execute(
            "SELECT act_encid, reason, uploader_discord_id, guild_name FROM tamper_reports"
        ).fetchall()
    ]


def _spells(*tiers: str) -> CharacterSpells:
    return CharacterSpells(
        character_name="x",
        entries=[SpellEntry(name=f"Spell {t}", tier=t, spell_type="spells", level=50) for t in tiers],
    )


def test_offending_spells_is_ancient_and_celestial_only():
    bad = spell_audit.offending_spells(_spells("Master", "Grandmaster", "ancient", "Celestial", "Expert").entries)
    assert [b["tier"] for b in bad] == ["ancient", "Celestial"]
    assert spell_audit.offending_spells(_spells("Grandmaster", "Master").entries) == []


def test_incident_window_is_open_ended_until_lifted(monkeypatch):
    assert spell_audit.in_incident_window(SINCE - 1) is False
    assert spell_audit.in_incident_window(SINCE) is True
    assert spell_audit.in_incident_window(SINCE + 10**7) is True
    monkeypatch.setattr(spell_audit, "SPELL_AUDIT_UNTIL_TS", SINCE + 1000)
    assert spell_audit.in_incident_window(SINCE + 999) is True
    assert spell_audit.in_incident_window(SINCE + 1000) is False


def test_flag_bars_from_rankings_and_reports_but_never_hides():
    conn = _conn()
    try:
        old = _seed(conn, started_at=SINCE - 100, players=["Cheater", "Clean"])
        new1 = _seed(conn, started_at=SINCE + 1000, players=["Cheater", "Clean"], uploaded_by="Alpha")
        new2 = _seed(conn, started_at=SINCE + 2000, players=["Clean"], uploaded_by="Bravo")
        assert _state(conn, new1) == {"visible": True, "barred": None, "ranks": True}

        first = spell_audit.flag_sync(
            WORLD, "Cheater", reason=spell_audit.REASON_SPELLS, details={"spells": [{"name": "X", "tier": "Ancient"}]}
        )
        assert first == {"new": True, "barred": 1, "reports": 1}
        assert _state(conn, old) == {"visible": True, "barred": None, "ranks": True}  # before the cutoff
        assert _state(conn, new1) == {"visible": True, "barred": spell_audit.FLAG_BAR_REASON, "ranks": False}
        assert _state(conn, new2) == {"visible": True, "barred": None, "ranks": True}  # cheater not in it
        assert [(r["reason"], r["uploader_discord_id"], r["guild_name"]) for r in _reports(conn)] == [
            (spell_audit.TAMPER_REASON, "disc-9", "Exordium")
        ]

        again = spell_audit.flag_sync(WORLD, "cheater", reason=spell_audit.REASON_SPELLS, details=None)
        assert again == {"new": False, "barred": 0, "reports": 0}  # idempotent
        assert len(_reports(conn)) == 1
    finally:
        conn.close()


def test_clear_lets_flag_barred_parses_rank_again_but_not_window_stamped_ones():
    conn = _conn()
    try:
        both = _seed(conn, started_at=SINCE + 1000, players=["Cheater", "Other"])
        solo = _seed(conn, started_at=SINCE + 2000, players=["Cheater", "Clean"], uploaded_by="Alpha")
        stamped = _seed(conn, started_at=SINCE + 3000, players=["Cheater"], uploaded_by="Bravo")
        parses_db.store.bar_encounter_from_rankings(conn, stamped, reason=spell_audit.INCIDENT_REASON, now=SINCE + 1)
        conn.commit()
        spell_audit.flag_sync(WORLD, "Cheater", reason=spell_audit.REASON_SPELLS, details=None)
        spell_audit.flag_sync(WORLD, "Other", reason=spell_audit.REASON_HIDDEN, details=None)

        assert spell_audit.clear_sync(WORLD, "Cheater", by="admin-1") == {"cleared": True, "restored": 1}
        assert _state(conn, solo) == {"visible": True, "barred": None, "ranks": True}
        assert _state(conn, both)["barred"] == spell_audit.FLAG_BAR_REASON  # Other still active
        # The incident-window stamp is permanent: clearing a flag never lifts it.
        assert _state(conn, stamped) == {"visible": True, "barred": spell_audit.INCIDENT_REASON, "ranks": False}

        assert spell_audit.clear_sync(WORLD, "Cheater", by="admin-1")["cleared"] is False
        assert spell_audit.clear_sync(WORLD, "Other", by="admin-1") == {"cleared": True, "restored": 1}
        assert _state(conn, both) == {"visible": True, "barred": None, "ranks": True}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_flagged_participants_is_case_insensitive_and_active_only():
    spell_audit.flag_sync(WORLD, "Cheater", reason=spell_audit.REASON_MANUAL, details=None, by="admin-1")
    assert await spell_audit.flagged_participants(WORLD, ["CHEATER", "Clean", ""]) == ["cheater"]
    spell_audit.clear_sync(WORLD, "Cheater", by="admin-1")
    assert await spell_audit.flagged_participants(WORLD, ["Cheater"]) == []
    assert await spell_audit.flagged_participants("Wuoshi", ["Cheater"]) == []


def test_bar_uploaded_parse_keeps_the_parse_and_reports_it():
    conn = _conn()
    try:
        eid = _seed(conn, started_at=SINCE + 1000, players=["Cheater", "Clean"])
        assert spell_audit.bar_uploaded_parse_sync(WORLD, eid, ["cheater"]) is True
        assert _state(conn, eid) == {"visible": True, "barred": spell_audit.FLAG_BAR_REASON, "ranks": False}
        assert len(_reports(conn)) == 1
        assert spell_audit.bar_uploaded_parse_sync(WORLD, 999_999, ["cheater"]) is False
    finally:
        conn.close()


def _fake_client(answers: dict[str, CharacterSpells | None]):
    client = MagicMock()
    client.get_character_spells = AsyncMock(side_effect=lambda name, world: answers.get(name.lower()))

    @asynccontextmanager
    async def _ctx():
        yield client

    return client, _ctx


@pytest.mark.asyncio
async def test_sweep_flags_cheaters_and_unverifiable_characters_and_lifts_the_latter(monkeypatch):
    monkeypatch.setattr(spell_audit, "_PACING_S", 0.0)
    monkeypatch.setattr(spell_audit, "_MISS_RETRY_S", 0.0)
    conn = _conn()
    try:
        kill = _seed(conn, started_at=SINCE + 1000, players=["Cheater", "Clean", "Ghost"])
        answers: dict[str, CharacterSpells | None] = {
            "cheater": _spells("Master", "Ancient"),
            "clean": _spells("Master", "Grandmaster"),
            "ghost": None,
        }
        client, ctx = _fake_client(answers)
        with (
            patch("backend.server.spell_audit.shared_census_client", ctx),
            patch("backend.server.spell_audit.census_health.is_down", return_value=False),
            patch("backend.server.spell_audit.failures.tripped", return_value=False),
        ):
            assert await spell_audit.run_spell_audit() == {"scanned": 3, "flagged": 1, "hidden": 1, "errors": 0}
            conn.rollback()
            flags = {r["name_lower"]: r for r in parses_db.store.list_flagged_characters(conn, WORLD)}
            assert flags["cheater"]["reason"] == spell_audit.REASON_SPELLS
            assert "Ancient" in (flags["cheater"]["details"] or "")
            assert flags["ghost"]["reason"] == spell_audit.REASON_HIDDEN
            assert "clean" not in flags
            assert _state(conn, kill) == {"visible": True, "barred": spell_audit.FLAG_BAR_REASON, "ranks": False}

            assert (await spell_audit.run_spell_audit())["scanned"] == 0  # daily throttle
            answers["ghost"] = _spells("Master")
            forced = await spell_audit.run_spell_audit(force_rescan=True)
            assert forced["scanned"] == 2 and forced["flagged"] == 0 and forced["hidden"] == 0
            scanned_names = sorted(c.args[0].lower() for c in client.get_character_spells.await_args_list[-2:])
            assert scanned_names == ["clean", "ghost"]
            conn.rollback()
            flags = {r["name_lower"]: r for r in parses_db.store.list_flagged_characters(conn, WORLD)}
            assert "ghost" not in flags and "cheater" in flags
            assert _state(conn, kill)["ranks"] is False  # the cheater's flag still covers it
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sweep_never_flags_on_a_census_outage(monkeypatch):
    monkeypatch.setattr(spell_audit, "_PACING_S", 0.0)
    monkeypatch.setattr(spell_audit, "_MISS_RETRY_S", 0.0)
    conn = _conn()
    try:
        kill = _seed(conn, started_at=SINCE + 1000, players=["Flaky"])
        _, ctx = _fake_client({"flaky": None})
        with (
            patch("backend.server.spell_audit.shared_census_client", ctx),
            patch("backend.server.spell_audit.census_health.is_down", return_value=False),
            patch("backend.server.spell_audit.failures.tripped", return_value=True),
        ):
            result = await spell_audit.run_spell_audit()
        assert result["errors"] == 1 and result["hidden"] == 0
        conn.rollback()
        assert parses_db.store.list_flagged_characters(conn, WORLD) == []
        assert _state(conn, kill) == {"visible": True, "barred": None, "ranks": True}
        with patch("backend.server.spell_audit.census_health.is_down", return_value=True):
            assert (await spell_audit.run_spell_audit())["skipped"] == "census down"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_ingest_stores_a_parse_with_a_flagged_participant_and_bars_it(app):
    """An upload is NEVER refused because of a flag: it is stored, then
    barred from the rankings and reported."""
    payload = _minimal_payload()
    insert = MagicMock(return_value=("inserted", 41, 1, 0, 0))
    quarantine = MagicMock(return_value=777)
    bar = MagicMock(return_value=True)
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=insert),
        patch("backend.server.api.parses.ingest._quarantine_encounter_sync", new=quarantine),
        patch(
            "backend.server.api.parses.ingest.spell_audit.flagged_participants", new=AsyncMock(return_value=["cheater"])
        ),
        patch("backend.server.api.parses.ingest.spell_audit.bar_uploaded_parse_sync", new=bar),
        # The post-response fill would call live Census for the payload's players.
        patch("backend.server.api.parses.ingest._resolve_and_update_snapshots", new=AsyncMock()),
        patch("backend.server.api.parses.ingest._sync_rankings_for_encounter", new=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201
    assert r.json()["status"] == "inserted" and r.json()["encounter_id"] == 41
    insert.assert_called_once()
    quarantine.assert_not_called()
    bar.assert_called_once_with("Varsoon", 41, ["cheater"])


_ADMIN = make_fake_admin(id="admin-1")


def _fake_admin(request=None):  # noqa: ARG001
    return _ADMIN


@pytest.mark.asyncio
async def test_admin_can_flag_list_and_clear(app):
    conn = _conn()
    try:
        kill = _seed(conn, started_at=SINCE + 1000, players=["Cheater", "Clean"])
    finally:
        conn.close()
    with patch("backend.server.api.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            empty = await client.get("/api/admin/flagged-characters")
            assert empty.status_code == 200
            assert empty.json()["results"] == [] and empty.json()["tiers"] == ["Ancient", "Celestial"]
            assert empty.json()["since"] == SINCE

            flagged = await client.post("/api/admin/flagged-characters", json={"name": "Cheater", "note": "seen it"})
            assert flagged.status_code == 200, flagged.text
            assert flagged.json()["new"] is True and flagged.json()["barred"] == 1

            assert (await client.post("/api/admin/flagged-characters", json={"name": "not a name!"})).status_code == 400

            (row,) = (await client.get("/api/admin/flagged-characters")).json()["results"]
            assert row["name"] == "Cheater" and row["reason"] == "manual" and row["details"]["note"] == "seen it"
            assert row["flagged_by"] == "admin-1"

            cleared = await client.delete("/api/admin/flagged-characters/Cheater")
            assert cleared.status_code == 200 and cleared.json()["restored"] == 1
            assert (await client.delete("/api/admin/flagged-characters/Cheater")).status_code == 404
            shown = await client.get("/api/admin/flagged-characters?include_cleared=true")
            assert shown.json()["results"][0]["cleared_by"] == "admin-1"
    conn = _conn()
    try:
        assert _state(conn, kill) == {"visible": True, "barred": None, "ranks": True}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_admin_run_starts_a_sweep(app):
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.spell_audit.run_spell_audit", new=AsyncMock(return_value={})) as run,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/spell-audit/run?force_rescan=true")
    assert r.status_code == 200 and r.json() == {"started": True}
    run.assert_called_once_with(force_rescan=True)


def test_lift_all_restores_every_barred_parse_to_the_rankings():
    """The whole filter is reversible: one call removes window stamps and
    flag bars alike and the fights rank again."""
    conn = _conn()
    try:
        windowed = _seed(conn, started_at=SINCE + 1000, players=["Clean"])
        flagged = _seed(conn, started_at=SINCE + 2000, players=["Cheater"], uploaded_by="Alpha")
        parses_db.store.bar_encounter_from_rankings(conn, windowed, reason=spell_audit.INCIDENT_REASON, now=SINCE + 1)
        fights.refresh_fight(conn, fights.fight_id_of(conn, windowed))
        conn.commit()
        spell_audit.flag_sync(WORLD, "Cheater", reason=spell_audit.REASON_SPELLS, details=None)
        assert _state(conn, windowed)["ranks"] is False and _state(conn, flagged)["ranks"] is False

        assert spell_audit.lift_all_sync() == {"unbarred": 2, "fights": 2}
        assert _state(conn, windowed) == {"visible": True, "barred": None, "ranks": True}
        assert _state(conn, flagged) == {"visible": True, "barred": None, "ranks": True}
        assert spell_audit.lift_all_sync() == {"unbarred": 0, "fights": 0}  # idempotent
    finally:
        conn.close()
