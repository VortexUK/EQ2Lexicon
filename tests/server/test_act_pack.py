"""Tests for GET /api/act/pack — the EQ2Parser sync surface — and the
closed-vocabulary validators behind the enrichment fields."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.eq2db.raids import RaidCatalogue
from backend.server.api.act.vocabulary import normalize_control_effect, normalize_damage_type

# ---------------------------------------------------------------------------
# Vocabulary validators
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_damage_type_preserves_dominance_order(self):
        assert normalize_damage_type("Cold, Divine") == "cold, divine"
        assert normalize_damage_type("divine,cold") == "divine, cold"

    def test_damage_type_dedupes_and_skips_blanks(self):
        assert normalize_damage_type("poison, , poison, disease") == "poison, disease"

    def test_damage_type_rejects_unknown_school(self):
        with pytest.raises(ValueError, match="unknown damage school"):
            normalize_damage_type("poison, shadow")

    def test_control_effect_accepts_known_and_blank(self):
        assert normalize_control_effect("Stifle") == "stifle"
        assert normalize_control_effect("") == ""

    def test_control_effect_rejects_unknown(self):
        with pytest.raises(ValueError, match="unknown control effect"):
            normalize_control_effect("sleep")


# ---------------------------------------------------------------------------
# The pack endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_raids_db(tmp_path, monkeypatch):
    """A raids.db with one curated encounter (trigger + timer, enriched) and
    one bare encounter (no ACT content — must be excluded from the pack),
    swapped in as the shared catalogue's path."""
    from backend.eq2db import raids as raids_module

    p = tmp_path / "raids.db"
    conn = RaidCatalogue(p).init_db()
    zone_id = raids_module.catalogue.upsert_raid_zone(
        conn, zone_name="Freethinker Hideout", expansion_short="EoF", source="manual"
    )
    enc_id = raids_module.catalogue.upsert_raid_encounter(
        conn, raid_zone_id=zone_id, mob_name="Malkonis D'Morte", position=4, strategy_md=None, source="manual"
    )
    raids_module.catalogue.upsert_raid_encounter(
        conn, raid_zone_id=zone_id, mob_name="Madmar", position=0, strategy_md="strategy only", source="manual"
    )
    RaidCatalogue.upsert_act_trigger(
        conn,
        raid_encounter_id=enc_id,
        regex="Feed, my pets!",
        sound_data="Knock up",
        timer=True,
        timer_name="Malkonis Knock Up",
        cooldown_seconds=2.0,
    )
    RaidCatalogue.upsert_act_spell_timer(
        conn,
        raid_encounter_id=enc_id,
        name="Malkonis Knock Up",
        timer_duration_s=27,
        warning_value=5,
        control_effect="knockup",
        damage_type="crushing",
    )
    conn.close()

    monkeypatch.setattr(raids_module.catalogue, "path", p)
    return p


@pytest.mark.asyncio
async def test_pack_shape_and_enrichment(app, seeded_raids_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/act/pack")
    assert r.status_code == 200
    pack = r.json()

    assert pack["trigger_count"] == 1
    assert pack["spell_timer_count"] == 1
    assert pack["version"].count(".") == 2

    zone = next(z for z in pack["zones"] if z["zone"] == "Freethinker Hideout")
    assert zone["expansion"] == "EoF"
    # The strategy-only encounter carries no ACT content — excluded.
    assert [e["mob"] for e in zone["encounters"]] == ["Malkonis D'Morte"]

    enc = zone["encounters"][0]
    assert enc["position"] == 4
    trigger = enc["triggers"][0]
    assert trigger["regex"] == "Feed, my pets!"
    assert trigger["cooldown_seconds"] == 2.0
    timer = enc["spell_timers"][0]
    assert timer["name"] == "Malkonis Knock Up"
    assert timer["timer_duration_s"] == 27
    assert timer["damage_type"] == "crushing"
    assert timer["control_effect"] == "knockup"


@pytest.mark.asyncio
async def test_pack_version_changes_on_edit(app, seeded_raids_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = (await client.get("/api/act/pack")).json()["version"]

    from backend.eq2db.raids import catalogue

    conn = RaidCatalogue(seeded_raids_db).init_db()
    enc = conn.execute("SELECT id FROM raid_encounters WHERE mob_name = 'Malkonis D''Morte'").fetchone()[0]
    RaidCatalogue.upsert_act_trigger(
        conn, raid_encounter_id=enc, regex="Come out and join me my brethren!", sound_data="Adds"
    )
    conn.close()
    assert catalogue.path == seeded_raids_db  # the endpoint reads the seeded file

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        after = (await client.get("/api/act/pack")).json()["version"]
    assert after != before
