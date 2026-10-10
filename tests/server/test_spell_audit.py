"""Spell audit (backend/server/spell_audit.py): out-of-era spells bar the
whole parse, retroactively to the cutoff, with a tamper report per parse;
unverifiable (Census-hidden) characters are barred the same way until they
show clean; clearing restores; ingest quarantines flagged participants.
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
from tests.fixtures.users import make_fake_admin
from tests.server._parses_ingest_fixtures import _fake_require_user, _minimal_payload, _signed_post_kwargs

WORLD = "Varsoon"
SINCE = spell_audit.since_ts()


@pytest.fixture(autouse=True)
def _schema(parses_db_path: str) -> str:
    return parses_db_path


@pytest.fixture(autouse=True)
def _no_rankings_refresh():
    """Enforcement schedules a rankings rebuild; keep the tests on the store."""
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
    conn.commit()
    return eid


def _hidden_by(conn: Any, eid: int) -> str | None:
    return conn.execute("SELECT hidden_by FROM encounters WHERE id = %s", (eid,)).fetchone()["hidden_by"]


def _reports(conn: Any) -> list[dict]:
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


# ── the rule ─────────────────────────────────────────────────────────────────


def test_offending_spells_is_ancient_and_celestial_only():
    bad = spell_audit.offending_spells(_spells("Master", "Grandmaster", "ancient", "Celestial", "Expert").entries)
    assert [b["tier"] for b in bad] == ["ancient", "Celestial"]
    assert spell_audit.offending_spells(_spells("Grandmaster", "Master").entries) == []


# ── enforcement ──────────────────────────────────────────────────────────────


def test_flag_hides_every_parse_since_the_cutoff_and_files_reports():
    conn = _conn()
    try:
        old = _seed(conn, started_at=SINCE - 100, players=["Cheater", "Clean"])
        new1 = _seed(conn, started_at=SINCE + 100, players=["Cheater", "Clean"], uploaded_by="Alpha")
        new2 = _seed(conn, started_at=SINCE + 200, players=["Clean"], uploaded_by="Bravo")

        first = spell_audit.flag_sync(
            WORLD, "Cheater", reason=spell_audit.REASON_SPELLS, details={"spells": [{"name": "X", "tier": "Ancient"}]}
        )
        assert first["new"] is True and first["hidden"] == 1 and first["reports"] == 1
        assert _hidden_by(conn, old) is None  # before the cutoff: untouched
        assert _hidden_by(conn, new1) == spell_audit.SOURCE
        assert _hidden_by(conn, new2) is None  # the cheater wasn't in it
        reports = _reports(conn)
        assert [(r["reason"], r["uploader_discord_id"], r["guild_name"]) for r in reports] == [
            (spell_audit.TAMPER_REASON, "disc-9", "Exordium")
        ]

        again = spell_audit.flag_sync(WORLD, "cheater", reason=spell_audit.REASON_SPELLS, details=None)
        assert again["new"] is False and again["hidden"] == 0  # idempotent
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_flagged_participants_is_case_insensitive_and_active_only():
    spell_audit.flag_sync(WORLD, "Cheater", reason=spell_audit.REASON_MANUAL, details=None, by="admin-1")
    assert await spell_audit.flagged_participants(WORLD, ["CHEATER", "Clean", ""]) == ["cheater"]
    spell_audit.clear_sync(WORLD, "Cheater", by="admin-1")
    assert await spell_audit.flagged_participants(WORLD, ["Cheater"]) == []
    assert await spell_audit.flagged_participants("Wuoshi", ["Cheater"]) == []


def test_clear_restores_unless_another_flag_still_covers_the_parse():
    conn = _conn()
    try:
        both = _seed(conn, started_at=SINCE + 10, players=["Cheater", "Other"])
        solo = _seed(conn, started_at=SINCE + 20, players=["Cheater", "Clean"], uploaded_by="Alpha")
        spell_audit.flag_sync(WORLD, "Cheater", reason=spell_audit.REASON_SPELLS, details=None)
        spell_audit.flag_sync(WORLD, "Other", reason=spell_audit.REASON_HIDDEN, details=None)
        assert _hidden_by(conn, both) == spell_audit.SOURCE and _hidden_by(conn, solo) == spell_audit.SOURCE

        res = spell_audit.clear_sync(WORLD, "Cheater", by="admin-1")
        assert res == {"cleared": True, "restored": 1}
        assert _hidden_by(conn, solo) is None
        assert _hidden_by(conn, both) == spell_audit.SOURCE  # Other still active

        assert spell_audit.clear_sync(WORLD, "Cheater", by="admin-1")["cleared"] is False
        assert spell_audit.clear_sync(WORLD, "Other", by="admin-1") == {"cleared": True, "restored": 1}
        assert _hidden_by(conn, both) is None
    finally:
        conn.close()


# ── the sweep ────────────────────────────────────────────────────────────────


def _fake_client(answers: dict[str, CharacterSpells | None]):
    client = MagicMock()
    client.get_character_spells = AsyncMock(side_effect=lambda name, world: answers.get(name.lower()))

    @asynccontextmanager
    async def _ctx():
        yield client

    return client, _ctx


@pytest.mark.asyncio
async def test_sweep_flags_cheaters_bars_hidden_characters_and_lifts_them_when_they_show(monkeypatch):
    monkeypatch.setattr(spell_audit, "_PACING_S", 0.0)
    monkeypatch.setattr(spell_audit, "_MISS_RETRY_S", 0.0)
    conn = _conn()
    try:
        kill = _seed(conn, started_at=SINCE + 100, players=["Cheater", "Clean", "Ghost"])
        answers: dict[str, CharacterSpells | None] = {
            "cheater": _spells("Master", "Ancient"),
            "clean": _spells("Master", "Grandmaster"),
            "ghost": None,  # Census has no record → unverifiable
        }
        client, ctx = _fake_client(answers)
        with (
            patch("backend.server.spell_audit.shared_census_client", ctx),
            patch("backend.server.spell_audit.census_health.is_down", return_value=False),
            patch("backend.server.spell_audit.failures.tripped", return_value=False),
        ):
            result = await spell_audit.run_spell_audit()
            assert result == {"scanned": 3, "flagged": 1, "hidden": 1, "errors": 0}
            flags = {r["name_lower"]: r for r in parses_db.store.list_flagged_characters(conn, WORLD)}
            assert flags["cheater"]["reason"] == spell_audit.REASON_SPELLS
            assert "Ancient" in (flags["cheater"]["details"] or "")
            assert flags["ghost"]["reason"] == spell_audit.REASON_HIDDEN
            assert "clean" not in flags
            assert _hidden_by(conn, kill) == spell_audit.SOURCE

            # Daily throttle: nothing to scan again right away …
            assert (await spell_audit.run_spell_audit())["scanned"] == 0
            # … but a forced re-check re-visits the hidden character (not the
            # sticky cheater), and once Census shows Ghost clean the flag lifts.
            answers["ghost"] = _spells("Master")
            forced = await spell_audit.run_spell_audit(force_rescan=True)
            assert forced["scanned"] == 2 and forced["flagged"] == 0 and forced["hidden"] == 0
            scanned_names = sorted(c.args[0].lower() for c in client.get_character_spells.await_args_list[-2:])
            assert scanned_names == ["clean", "ghost"]
            flags = {r["name_lower"]: r for r in parses_db.store.list_flagged_characters(conn, WORLD)}
            assert "ghost" not in flags and "cheater" in flags
            # The kill stays hidden: the cheater's sticky flag still covers it.
            assert _hidden_by(conn, kill) == spell_audit.SOURCE
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sweep_never_bars_on_a_census_outage(monkeypatch):
    monkeypatch.setattr(spell_audit, "_PACING_S", 0.0)
    monkeypatch.setattr(spell_audit, "_MISS_RETRY_S", 0.0)
    conn = _conn()
    try:
        kill = _seed(conn, started_at=SINCE + 100, players=["Flaky"])
        _, ctx = _fake_client({"flaky": None})
        with (
            patch("backend.server.spell_audit.shared_census_client", ctx),
            patch("backend.server.spell_audit.census_health.is_down", return_value=False),
            patch("backend.server.spell_audit.failures.tripped", return_value=True),  # the breaker is open
        ):
            result = await spell_audit.run_spell_audit()
        assert result["errors"] == 1 and result["hidden"] == 0
        assert parses_db.store.list_flagged_characters(conn, WORLD) == []
        assert _hidden_by(conn, kill) is None
        with patch("backend.server.spell_audit.census_health.is_down", return_value=True):
            assert (await spell_audit.run_spell_audit())["skipped"] == "census down"
    finally:
        conn.close()


# ── ingest: a flagged participant quarantines the upload ─────────────────────


@pytest.mark.asyncio
async def test_ingest_quarantines_a_parse_with_a_flagged_participant(app):
    payload = _minimal_payload()
    insert = MagicMock(return_value=("inserted", 1, 1, 0, 0))
    quarantine = MagicMock(return_value=777)
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=insert),
        patch("backend.server.api.parses.ingest._quarantine_encounter_sync", new=quarantine),
        patch(
            "backend.server.api.parses.ingest.spell_audit.flagged_participants", new=AsyncMock(return_value=["cheater"])
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201
    assert r.json()["status"] == "quarantined"
    quarantine.assert_called_once()
    assert quarantine.call_args.kwargs["reason"] == spell_audit.REASON_SPELLS
    insert.assert_not_called()


# ── admin routes ─────────────────────────────────────────────────────────────

_ADMIN = make_fake_admin(id="admin-1")


def _fake_admin(request=None):  # noqa: ARG001
    return _ADMIN


@pytest.mark.asyncio
async def test_admin_can_flag_list_and_clear(app):
    conn = _conn()
    try:
        kill = _seed(conn, started_at=SINCE + 5, players=["Cheater", "Clean"])
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
            assert flagged.json()["new"] is True and flagged.json()["hidden"] == 1

            bad = await client.post("/api/admin/flagged-characters", json={"name": "not a name!"})
            assert bad.status_code == 400

            listed = await client.get("/api/admin/flagged-characters")
            (row,) = listed.json()["results"]
            assert row["name"] == "Cheater" and row["reason"] == "manual" and row["details"]["note"] == "seen it"
            assert row["flagged_by"] == "admin-1"

            cleared = await client.delete("/api/admin/flagged-characters/Cheater")
            assert cleared.status_code == 200 and cleared.json()["restored"] == 1
            assert (await client.delete("/api/admin/flagged-characters/Cheater")).status_code == 404
            shown = await client.get("/api/admin/flagged-characters?include_cleared=true")
            assert shown.json()["results"][0]["cleared_by"] == "admin-1"
    conn = _conn()
    try:
        assert _hidden_by(conn, kill) is None
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


# ── the incident window: a permanent ranking bar ─────────────────────────────


def test_incident_window_is_open_ended_until_lifted(monkeypatch):
    assert spell_audit.in_incident_window(SINCE - 1) is False
    assert spell_audit.in_incident_window(SINCE) is True
    assert spell_audit.in_incident_window(SINCE + 10**7) is True  # open while SPELL_AUDIT_UNTIL is unset
    monkeypatch.setattr(spell_audit, "SPELL_AUDIT_UNTIL_TS", SINCE + 1000)
    assert spell_audit.in_incident_window(SINCE + 999) is True
    assert spell_audit.in_incident_window(SINCE + 1000) is False


def test_barred_parse_is_never_a_fights_ranking_primary_even_after_restore():
    from backend.server.parses import fights

    conn = _conn()
    try:
        eid = _seed(conn, started_at=SINCE + 100, players=["Cheater", "Clean"])
        fights.attach_encounter(conn, eid)
        conn.commit()
        fid = fights.fight_id_of(conn, eid)
        assert fid is not None
        assert fights.get_fight(conn, fid)["primary_winning_encounter_id"] == eid  # ranks before the stamp

        assert parses_db.store.bar_encounter_from_rankings(
            conn, eid, reason=spell_audit.INCIDENT_REASON, now=SINCE + 200
        )
        assert parses_db.store.bar_encounter_from_rankings(conn, eid, reason="x", now=SINCE + 300) is False
        fights.refresh_fight(conn, fid)
        conn.commit()
        f = fights.get_fight(conn, fid)
        assert f["primary_encounter_id"] == eid  # still the list's canonical row …
        assert f["primary_winning_encounter_id"] is None  # … but never a ranking kill

        # Flag → hide → clear → restore: the parse comes back to the list but
        # the stamp keeps it off the boards.
        spell_audit.flag_sync(WORLD, "Cheater", reason=spell_audit.REASON_SPELLS, details=None)
        assert _hidden_by(conn, eid) == spell_audit.SOURCE
        spell_audit.clear_sync(WORLD, "Cheater", by="admin-1")
        assert _hidden_by(conn, eid) is None
        assert fights.get_fight(conn, fid)["primary_winning_encounter_id"] is None
        row = conn.execute("SELECT ranking_barred_reason FROM encounters WHERE id = %s", (eid,)).fetchone()
        assert row["ranking_barred_reason"] == spell_audit.INCIDENT_REASON
    finally:
        conn.close()
