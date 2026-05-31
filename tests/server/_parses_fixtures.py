"""Shared fake DB data for parses-related web tests.

Extracted from test_parses.py:16-185 per TEST-004 / Phase 2b.1.
These constants mirror what parses_db.recent_encounters /
get_combatants_for_encounter / get_top_attacks_for_combatant return
(dicts from sqlite3.Row).

Imported by: test_parses_list.py, test_parses_delete.py,
             test_parses_uploader_identity.py.
"""

from __future__ import annotations

_FAKE_ENCOUNTER = {
    "id": 1,
    "act_encid": "18cf3eb9",
    "title": "a krait patriarch",
    "zone": "Great Divide",
    "started_at": 1716561116,
    "ended_at": 1716561162,
    "duration_s": 46,
    "total_damage": 502718,
    "encdps": 10928.65,
    "kills": 4,
    "deaths": 0,
    "source_dsn": "eq2act",
    "uploaded_by": "Menludiir",
    "guild_name": "Exordium",
    "ingested_at": 1716561200,
}

_FAKE_COMBATANTS = [
    {
        "id": 10,
        "encounter_id": 1,
        "name": "Menludiir",
        "ally": 1,
        "is_player": 1,
        "duration_s": 47,
        "damage": 502718,
        "damage_perc": 100.0,
        "dps": 10696.13,
        "encdps": 10928.65,
        "healed": 11637,
        "enchps": 252.98,
        "heals": 40,
        "crit_heals": 1,
        "cure_dispels": 0,
        "power_drain": 0,
        "power_replenish": 0,
        "heals_taken": 11637,
        "damage_taken": 27557,
        "threat_delta": 20000,
        "deaths": 0,
        "kills": 4,
        "crit_hits": 123,
        "crit_dam_perc": 93.0,
    },
    {
        "id": 11,
        "encounter_id": 1,
        "name": "a krait patriarch",
        "ally": 0,
        "is_player": 0,
        "duration_s": 15,
        "damage": 5716,
        "damage_perc": 0.0,
        "dps": 381.07,
        "encdps": 124.26,
        "healed": 0,
        "enchps": 0.0,
        "heals": 0,
        "crit_heals": 0,
        "cure_dispels": 0,
        "power_drain": 0,
        "power_replenish": 0,
        "heals_taken": 0,
        "damage_taken": 145877,
        "threat_delta": 0,
        "deaths": 1,
        "kills": 0,
        "crit_hits": 0,
        "crit_dam_perc": 0.0,
    },
]

_FAKE_DAMAGE_TYPES = {
    10: [
        {
            "damage_type": "divine",
            "damage": 400000,
            "dps": 8500.0,
            "hits": 100,
            "swings": 100,
            "max_hit": 8000,
            "crit_perc": 90.0,
        },
        {
            "damage_type": "melee",
            "damage": 102718,
            "dps": 2185.0,
            "hits": 32,
            "swings": 32,
            "max_hit": 4500,
            "crit_perc": 100.0,
        },
    ],
    11: [
        {
            "damage_type": "physical",
            "damage": 5716,
            "dps": 381.07,
            "hits": 11,
            "swings": 12,
            "max_hit": 1297,
            "crit_perc": 0.0,
        },
    ],
}

_FAKE_TOP_ATTACKS = {
    10: [
        {
            "id": 100,
            "combatant_id": 10,
            "attack_name": "Smite",
            "damage": 400000,
            "hits": 100,
            "swings": 100,
            "crit_perc": 90.0,
            "max_hit": 8000,
        },
    ],
    11: [
        {
            "id": 200,
            "combatant_id": 11,
            "attack_name": "melee",
            "damage": 5716,
            "hits": 11,
            "swings": 12,
            "crit_perc": 0.0,
            "max_hit": 1297,
        },
    ],
}

_FAKE_TOP_HEALS = {
    10: [
        {
            "attack_name": "Reverence",
            "damage": 7818,  # amount healed
            "hits": 12,
            "swings": 12,
            "crit_perc": 0.0,
            "max_hit": 1297,
            "resist": "Hitpoints",
        },
        {
            "attack_name": "Stonewill",
            "damage": 3819,
            "hits": 12,
            "swings": 12,
            "crit_perc": 0.0,
            "max_hit": 1297,
            "resist": "Absorption",  # ward
        },
    ],
    11: [],
}

_FAKE_TOP_CURES = {
    10: [
        {"attack_name": "Cure", "damage": 4, "hits": 4, "max_hit": 1, "resist": "relieves"},
        {"attack_name": "Devoted Resolve", "damage": 2, "hits": 2, "max_hit": 1, "resist": "relieves"},
    ],
    11: [],
}

_FAKE_TOP_THREATS = {
    10: [
        {"attack_name": "Undeniable Malice", "damage": 27240, "hits": 10, "max_hit": 5000, "resist": "Increase"},
    ],
    11: [],
}
