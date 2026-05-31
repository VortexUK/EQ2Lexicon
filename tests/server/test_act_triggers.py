"""Tests for the ACT triggers + spell-timers routes.

Covers:
  * CRUD on /api/zones/{zone}/encounters/{position}/triggers
  * CRUD on /api/zones/{zone}/encounters/{position}/spell-timers
  * The XML export endpoints (single trigger + all triggers)
  * Auth gating (write paths require_editor)
  * 404 fall-through for unknown zone / position / id
  * UNIQUE-constraint → 409 on spell-timer name collisions

Encounter resolution is mocked at the helper boundary
(``_resolve_encounter_sync``) — the lazy-create logic against raids_db is
covered separately by the raids_db unit tests; here we just need to assert
the route layer behaves correctly given a resolved encounter."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


# A complete trigger row shape as returned by raids_db.get_act_trigger /
# list_act_triggers_for_encounter — booleans stored as 0/1 INTEGERs, all
# columns present.
_TRIGGER_ROW = {
    "id": 11,
    "raid_encounter_id": 42,
    "position": 0,
    "label": "Curable AE incoming",
    "notes": None,
    "active": 1,
    "regex": r"^\\\\aPC -1 (?<Caster>\S+)\\\\/a is casting Doom\.$",
    "sound_data": "Curables incoming",
    "sound_type": 3,
    "category_restrict": 0,
    "category": "Prince Thirneg",
    "timer": 1,
    "timer_name": "Doom Cooldown",
    "tabbed": 0,
    "last_edited_at": 1716_000_000,
    "last_edited_by": "admin-1",
    "created_at": 1716_000_000,
}


_SPELL_ROW = {
    "id": 7,
    "raid_encounter_id": 42,
    "name": "Doom Cooldown",
    "name_lower": "doom cooldown",
    "checked": 1,
    "timer_duration_s": 45,
    "only_master_ticks": 0,
    "restrict": 0,
    "absolute_": 0,
    "start_wav": "",
    "warning_wav": "",
    "warning_value": 10,
    "radial_display": 0,
    "modable": 0,
    "tooltip": "Boss reuses Doom every 45 s",
    "fill_color": -16776961,
    "panel1": 1,
    "panel2": 0,
    "remove_value": -15,
    "category": "Prince Thirneg",
    "restrict_category": 0,
    "last_edited_at": 1716_000_000,
    "last_edited_by": "admin-1",
    "created_at": 1716_000_000,
}


def _resolved() -> tuple[str, str, int]:
    """The standard (canonical_zone, mob_name, encounter_id) tuple our
    ``_resolve_encounter_sync`` returns for these tests."""
    return ("The Emerald Halls", "Prince Thirneg", 42)


def _writer_client(app):
    """Override require_editor so the test acts as an authorised editor —
    bypasses the admin/contributor/officer fanout."""
    from backend.server.auth_deps import require_editor

    app.dependency_overrides[require_editor] = lambda: {
        "id": "admin-1",
        "username": "admin",
    }
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Encounter resolution — 404 paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_triggers_unknown_encounter_is_404(app):
    """When ``_resolve_encounter_sync`` returns None, every route 404s."""
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/Nowhere/encounters/1/triggers")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_spell_timers_unknown_encounter_is_404(app):
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/Nowhere/encounters/1/spell-timers")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Trigger CRUD — list / get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_triggers_returns_rows(app):
    """Resolved encounter + one trigger row → 200 with the entry shape."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_triggers_for_encounter",
            return_value=[_TRIGGER_ROW],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    t = body[0]
    assert t["id"] == 11
    assert "Doom" in t["regex"]
    assert t["active"] is True  # 0/1 → bool conversion
    assert t["timer"] is True
    assert t["timer_name"] == "Doom Cooldown"
    assert t["category"] == "Prince Thirneg"


@pytest.mark.asyncio
async def test_get_trigger_belongs_to_encounter(app):
    """The route guards against editing a trigger that lives on a different
    encounter (someone guessing IDs)."""
    other_encounter = {**_TRIGGER_ROW, "raid_encounter_id": 999}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=other_encounter),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/11")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_trigger_happy_path(app):
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=_TRIGGER_ROW),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/11")
    assert r.status_code == 200
    assert r.json()["id"] == 11


# ---------------------------------------------------------------------------
# Trigger CRUD — create / update / delete (auth-gated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_trigger_requires_auth(app):
    """No session → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/zones/The Emerald Halls/encounters/1/triggers",
            json={"regex": "boom"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_trigger_rejects_empty_regex(app):
    """Pydantic min_length=1 on regex → 422."""
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()):
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers",
                json={"regex": ""},
            )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_trigger_defaults_category_to_mob_name(app):
    """If the request omits ``category``, the route should stamp the mob name
    on save (so ACT groups it under the boss)."""
    new_row = {**_TRIGGER_ROW, "category": "Prince Thirneg"}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger", return_value=11) as m_upsert,
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=new_row),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers",
                json={"regex": r"^Doom\.$"},
            )
    assert r.status_code == 201
    assert r.json()["category"] == "Prince Thirneg"
    # The upsert should have been called with category derived from mob name
    assert m_upsert.call_args.kwargs["category"] == "Prince Thirneg"
    assert m_upsert.call_args.kwargs["edited_by"] == "admin-1"


@pytest.mark.asyncio
async def test_update_trigger_belongs_to_encounter(app):
    """Updating a trigger that belongs to a *different* encounter → 404."""
    other = {**_TRIGGER_ROW, "raid_encounter_id": 999}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=other),
    ):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/triggers/11",
                json={"regex": "new"},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_trigger_writes_and_returns_row(app):
    updated = {**_TRIGGER_ROW, "regex": "^new regex$"}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch(
            "backend.server.api.act.triggers.raids_db.get_act_trigger",
            side_effect=[_TRIGGER_ROW, updated],  # ownership check, then re-read
        ),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger", return_value=11),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/triggers/11",
                json={"regex": "^new regex$"},
            )
    assert r.status_code == 200
    assert r.json()["regex"] == "^new regex$"


@pytest.mark.asyncio
async def test_delete_trigger_happy_path(app):
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=_TRIGGER_ROW),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers.raids_db.delete_act_trigger", return_value=True),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.delete("/api/zones/The Emerald Halls/encounters/1/triggers/11")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_delete_trigger_belongs_to_encounter(app):
    other = {**_TRIGGER_ROW, "raid_encounter_id": 999}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=other),
    ):
        async with _writer_client(app) as client:
            r = await client.delete("/api/zones/The Emerald Halls/encounters/1/triggers/11")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Spell-timer CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_spell_timers_returns_rows(app):
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch(
            "backend.server.api.act.spell_timers.raids_db.list_act_spell_timers_for_encounter",
            return_value=[_SPELL_ROW],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/spell-timers")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    s = body[0]
    assert s["name"] == "Doom Cooldown"
    assert s["timer_duration_s"] == 45
    assert s["absolute"] is False  # ``absolute_`` → ``absolute`` in the API
    assert s["checked"] is True


@pytest.mark.asyncio
async def test_create_spell_timer_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/zones/The Emerald Halls/encounters/1/spell-timers",
            json={"name": "x", "timer_duration_s": 10},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_spell_timer_rejects_zero_duration(app):
    """Pydantic gt=0 → 422."""
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()):
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/spell-timers",
                json={"name": "x", "timer_duration_s": 0},
            )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_spell_timer_writes_and_returns_row(app):
    """Body without category → uses mob name. ``absolute=true`` on the API
    side maps to ``absolute_=True`` for the DB helper."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.spell_timers.raids_db.upsert_act_spell_timer", return_value=7) as m_upsert,
        patch("backend.server.api.act.spell_timers.raids_db.get_act_spell_timer", return_value=_SPELL_ROW),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/spell-timers",
                json={"name": "Doom Cooldown", "timer_duration_s": 45, "absolute": True},
            )
    assert r.status_code == 201
    # The DB helper takes absolute_ (with trailing underscore); confirm the
    # API→DB renaming wired through correctly.
    assert m_upsert.call_args.kwargs["absolute_"] is True
    assert m_upsert.call_args.kwargs["category"] == "Prince Thirneg"


@pytest.mark.asyncio
async def test_create_spell_timer_name_collision_is_409(app):
    """UNIQUE (encounter_id, name_lower) violation should surface as 409."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.init_db") as m_init,
        patch(
            "backend.server.api.act.spell_timers.raids_db.upsert_act_spell_timer",
            side_effect=sqlite3.IntegrityError("UNIQUE constraint failed"),
        ),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/spell-timers",
                json={"name": "Doom Cooldown", "timer_duration_s": 45},
            )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_update_spell_timer_belongs_to_encounter(app):
    other = {**_SPELL_ROW, "raid_encounter_id": 999}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.get_act_spell_timer", return_value=other),
    ):
        async with _writer_client(app) as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/spell-timers/7",
                json={"name": "x", "timer_duration_s": 10},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_spell_timer_happy_path(app):
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.get_act_spell_timer", return_value=_SPELL_ROW),
        patch("backend.server.api.act.spell_timers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.spell_timers.raids_db.delete_act_spell_timer", return_value=True),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.delete("/api/zones/The Emerald Halls/encounters/1/spell-timers/7")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ---------------------------------------------------------------------------
# XML export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_single_trigger_includes_referenced_spell_timer(app):
    """Single-trigger export bundles the spell timer the trigger references."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=_TRIGGER_ROW),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_spell_timers_for_encounter",
            return_value=[_SPELL_ROW],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/11/export.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    body = r.text
    # Frame
    assert body.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<Config>" in body
    assert "<CustomTriggers>" in body
    assert "<SpellTimers>" in body
    assert "<SettingsSerializer />" in body
    # Trigger payload
    assert 'Active="True"' in body
    assert 'TimerName="Doom Cooldown"' in body
    # Referenced spell timer is included
    assert 'Name="Doom Cooldown"' in body
    assert 'Timer="45"' in body
    # Content-Disposition gives a friendly filename
    assert "attachment" in r.headers["content-disposition"]
    assert ".xml" in r.headers["content-disposition"]


@pytest.mark.asyncio
async def test_export_single_trigger_without_timer_omits_spell_section(app):
    """A trigger with ``timer=False`` shouldn't pull in any <Spell> rows."""
    no_timer = {**_TRIGGER_ROW, "timer": 0, "timer_name": None}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=no_timer),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/11/export.xml")
    assert r.status_code == 200
    body = r.text
    assert "<CustomTriggers>" in body
    assert 'Timer="False"' in body
    # Empty <SpellTimers/> section is still present (so ACT's parser is happy)
    # but contains no Spell rows.
    assert "<Spell " not in body


@pytest.mark.asyncio
async def test_export_all_triggers_dedupes_spell_timers(app):
    """If two triggers reference the same spell timer name, the exported
    document should still only contain one <Spell> row for it."""
    t1 = {**_TRIGGER_ROW, "id": 11}
    t2 = {**_TRIGGER_ROW, "id": 12, "label": "Second match"}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_triggers_for_encounter",
            return_value=[t1, t2],
        ),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_spell_timers_for_encounter",
            return_value=[_SPELL_ROW],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/export.xml")
    assert r.status_code == 200
    body = r.text
    assert body.count("<Trigger ") == 2
    assert body.count("<Spell ") == 1  # deduped


@pytest.mark.asyncio
async def test_export_all_triggers_empty_encounter(app):
    """Encounter with zero triggers still gives a valid (empty) document."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_triggers_for_encounter",
            return_value=[],
        ),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_spell_timers_for_encounter",
            return_value=[],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/export.xml")
    assert r.status_code == 200
    body = r.text
    assert "<Config>" in body
    assert "<CustomTriggers>" in body
    assert "</Config>" in body
    assert "<Trigger " not in body


@pytest.mark.asyncio
async def test_export_single_trigger_wrong_encounter_is_404(app):
    """Ownership check applies to the XML export too — can't export a
    trigger that lives under a different encounter."""
    other = {**_TRIGGER_ROW, "raid_encounter_id": 999}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=other),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/11/export.xml")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# XML paste-import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_xml_requires_auth(app):
    """No session → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
            json={"xml": "<Trigger R='x' />"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_import_xml_short_form(app):
    """The ACT 'shareable' short form (R/SD/ST/CR/C/T/TN/Ta) — the exact
    shape the user pastes after a right-click → Copy as Shareable XML."""
    fresh_row = {
        **_TRIGGER_ROW,
        "id": 99,
        "regex": "Taskmaster Nichok summons your entire party to him",
        "sound_data": "Teleport",
        "timer": 1,
        "timer_name": "Taskmaster Out",
        "category": "Prince Thirneg",  # restamped to mob_name
        "notes": 'Imported via paste-XML (Category="Veeshan\'s Peak")',
    }
    payload = {
        "xml": (
            '<Trigger R="Taskmaster Nichok summons your entire party to him" '
            'SD="Teleport" ST="3" CR="F" C="Veeshan&apos;s Peak" '
            'T="T" TN="Taskmaster Out" Ta="F" />'
        )
    }
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers._trigger_already_exists_sync", return_value=None),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger", return_value=99) as m_upsert,
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=fresh_row),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json=payload,
            )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["triggers_added"] == 1
    assert body["triggers_skipped_existing"] == 0
    assert body["spell_timers_added"] == 0
    assert len(body["triggers"]) == 1

    # The upsert call should reflect the short-form decoding:
    kw = m_upsert.call_args.kwargs
    assert kw["regex"] == "Taskmaster Nichok summons your entire party to him"
    assert kw["sound_data"] == "Teleport"
    assert kw["sound_type"] == 3
    assert kw["category_restrict"] is False
    assert kw["timer"] is True
    assert kw["timer_name"] == "Taskmaster Out"
    assert kw["tabbed"] is False
    # Category restamped to the encounter's mob name, original preserved in notes
    assert kw["category"] == "Prince Thirneg"
    assert "Veeshan's Peak" in kw["notes"]


@pytest.mark.asyncio
async def test_import_xml_long_form(app):
    """The verbose Regex/SoundData/Timer attribute names (what
    spell_timers.xml uses) should also parse."""
    payload = {
        "xml": (
            '<Trigger Active="True" Regex="Pull!" SoundData="Pull" '
            'SoundType="3" CategoryRestrict="False" Category="Boss" '
            'Timer="False" TimerName="" Tabbed="False" />'
        )
    }
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers._trigger_already_exists_sync", return_value=None),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger", return_value=99) as m_upsert,
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=_TRIGGER_ROW),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json=payload,
            )
    assert r.status_code == 201
    kw = m_upsert.call_args.kwargs
    assert kw["regex"] == "Pull!"
    assert kw["sound_data"] == "Pull"
    assert kw["active"] is True


@pytest.mark.asyncio
async def test_import_xml_skips_duplicates(app):
    """If a trigger with the same (regex, sound_data) already exists,
    the import path skips it rather than 409'ing or duplicating."""
    payload = {"xml": '<Trigger R="x" SD="y" />'}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers._trigger_already_exists_sync", return_value=11),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger") as m_upsert,
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json=payload,
            )
    assert r.status_code == 201
    body = r.json()
    assert body["triggers_added"] == 0
    assert body["triggers_skipped_existing"] == 1
    m_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_import_xml_multiple_elements(app):
    """A paste containing multiple `<Trigger>` siblings (no wrapping
    container) should still parse — the route synthesises a root."""
    payload = {"xml": ('<Trigger R="a" SD="alpha" /><Trigger R="b" SD="beta" />')}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers._trigger_already_exists_sync", return_value=None),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger", side_effect=[101, 102]),
        patch(
            "backend.server.api.act.triggers.raids_db.get_act_trigger",
            side_effect=[{**_TRIGGER_ROW, "id": 101}, {**_TRIGGER_ROW, "id": 102}],
        ),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json=payload,
            )
    assert r.status_code == 201
    body = r.json()
    assert body["triggers_added"] == 2
    assert len(body["triggers"]) == 2


@pytest.mark.asyncio
async def test_import_xml_spell_short_form(app):
    """The shareable short form for <Spell> uses N/T/OM/R/A/WV/RD/M/Tt/FC/RV/C/RC.
    This is the exact shape ACT's right-click → Copy as Shareable XML
    produces for a spell timer. Verifies every key maps correctly."""
    payload = {
        "xml": (
            '<Spell N="Taskmaster Out" T="45" OM="F" R="F" A="F" WV="12" '
            'RD="F" M="F" Tt="" FC="-16776961" RV="-15" C="Taskmaster" RC="F" />'
        )
    }
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers._spell_timer_id_for_name_sync", return_value=None),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_spell_timer", return_value=7) as m_spell,
        patch("backend.server.api.act.triggers.raids_db.get_act_spell_timer", return_value=_SPELL_ROW),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json=payload,
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["triggers_added"] == 0
    assert body["spell_timers_added"] == 1

    kw = m_spell.call_args.kwargs
    # Every short key decoded to its proper column value
    assert kw["name"] == "Taskmaster Out"
    assert kw["timer_duration_s"] == 45
    assert kw["only_master_ticks"] is False
    assert kw["restrict"] is False
    assert kw["absolute_"] is False
    assert kw["warning_value"] == 12
    assert kw["radial_display"] is False
    assert kw["modable"] is False
    assert kw["tooltip"] == ""
    assert kw["fill_color"] == -16776961
    assert kw["remove_value"] == -15
    assert kw["restrict_category"] is False
    # Category restamped to the encounter's mob name (Prince Thirneg here),
    # not the source ACT category (Taskmaster).
    assert kw["category"] == "Prince Thirneg"


@pytest.mark.asyncio
async def test_import_xml_with_spell_sibling(app):
    """A paste containing both a `<Trigger>` and a `<Spell>` (e.g. the
    user copies the whole pair from ACT) writes both to the encounter."""
    payload = {
        "xml": (
            '<Trigger R="Boss casts Doom" SD="Doom" T="T" TN="Doom Cooldown" />'
            '<Spell Name="Doom Cooldown" Timer="45" WarningValue="10" '
            'FillColor="-16776961" Panel1="True" />'
        )
    }
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.init_db") as m_init,
        patch("backend.server.api.act.triggers._trigger_already_exists_sync", return_value=None),
        patch("backend.server.api.act.triggers._spell_timer_id_for_name_sync", return_value=None),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_trigger", return_value=99),
        patch("backend.server.api.act.triggers.raids_db.upsert_act_spell_timer", return_value=7) as m_spell,
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=_TRIGGER_ROW),
        patch("backend.server.api.act.triggers.raids_db.get_act_spell_timer", return_value=_SPELL_ROW),
    ):
        m_init.return_value.close = lambda: None
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json=payload,
            )
    assert r.status_code == 201
    body = r.json()
    assert body["triggers_added"] == 1
    assert body["spell_timers_added"] == 1
    assert m_spell.call_args.kwargs["name"] == "Doom Cooldown"
    assert m_spell.call_args.kwargs["timer_duration_s"] == 45


@pytest.mark.asyncio
async def test_import_xml_malformed_is_400(app):
    """Unparseable XML → 400, not a 500."""
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()):
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json={"xml": "<Trigger R='unclosed"},
            )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_xml_no_recognisable_elements_is_400(app):
    """Valid XML but no `<Trigger>` or `<Spell>` → 400 so the UI can
    surface a clear error rather than silently succeed with 0 changes."""
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()):
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json={"xml": "<SomethingElse foo='bar' />"},
            )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_xml_empty_body_is_400(app):
    """Empty input is a different failure mode than malformed — same
    outcome, different message. We also exercise Pydantic min_length=1
    here: an empty string is a 422 by validation; whitespace-only would be
    a 400 from our parser. Pick the validator path."""
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()):
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/The Emerald Halls/encounters/1/triggers/import-xml",
                json={"xml": ""},
            )
    # Pydantic validation 422 — proves the model constraint is in place.
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_import_xml_unknown_encounter_is_404(app):
    with patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=None):
        async with _writer_client(app) as client:
            r = await client.post(
                "/api/zones/Nowhere/encounters/1/triggers/import-xml",
                json={"xml": '<Trigger R="x" />'},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_all_triggers_includes_standalone_spell_timer(app):
    """A spell timer that NO trigger references must still appear in the boss
    export (standalone timers fire off ACT's native skill/CA name match)."""
    trigger_no_timer = {**_TRIGGER_ROW, "id": 11, "timer": 0, "timer_name": None}
    standalone = {**_SPELL_ROW, "id": 99, "name": "Manaward Reuse", "name_lower": "manaward reuse"}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_triggers_for_encounter",
            return_value=[trigger_no_timer],
        ),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_spell_timers_for_encounter",
            return_value=[standalone],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/export.xml")
    assert r.status_code == 200
    body = r.text
    assert "<Trigger " in body
    assert "<Spell " in body
    assert 'Name="Manaward Reuse"' in body


# ---------------------------------------------------------------------------
# Per-spell-timer XML export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_spell_timer_happy_path(app):
    """Single-timer export returns valid XML with the <Spell> row."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.get_act_spell_timer", return_value=_SPELL_ROW),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/spell-timers/7/export.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    body = r.text
    assert body.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<SpellTimers>" in body
    assert 'Name="Doom Cooldown"' in body
    assert 'Timer="45"' in body
    assert "attachment" in r.headers["content-disposition"]
    assert ".xml" in r.headers["content-disposition"]


@pytest.mark.asyncio
async def test_export_spell_timer_404_when_missing(app):
    """Unknown timer_id → 404."""
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.get_act_spell_timer", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/spell-timers/9999/export.xml")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_spell_timer_404_when_wrong_encounter(app):
    """Timer exists but belongs to a different encounter → 404 (ownership check)."""
    wrong_encounter = {**_SPELL_ROW, "raid_encounter_id": 999}
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.spell_timers.raids_db.get_act_spell_timer", return_value=wrong_encounter),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/spell-timers/7/export.xml")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_trigger_quotes_attributes_safely(app):
    """Regex content with the literal quote/ampersand chars must be escaped
    in the XML output so the file still parses."""
    dangerous = {
        **_TRIGGER_ROW,
        "regex": r'^"hello" & <world>$',
        "sound_data": 'say "danger"',
    }
    with (
        patch("backend.server.api.act._shared._resolve_encounter_sync", return_value=_resolved()),
        patch("backend.server.api.act.triggers.raids_db.get_act_trigger", return_value=dangerous),
        patch(
            "backend.server.api.act.triggers.raids_db.list_act_spell_timers_for_encounter",
            return_value=[_SPELL_ROW],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/encounters/1/triggers/11/export.xml")
    assert r.status_code == 200
    body = r.text
    # The raw, unescaped quote/ampersand must NOT appear inside attribute
    # values (would be a parse error). The escaped forms should.
    assert "&amp;" in body
    assert "&lt;" in body or "<world>" not in body
    # quoteattr uses single quotes around the value if it contains a "
    assert "&quot;" in body or "'" in body
