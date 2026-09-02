"""Unit tests for the RoK progression reduce logic (backend/server/progression.py).

Pure — no census, no HTTP. Fixtures use the real committed catalogs
(data/quests/*.json), so these also pin the catalog shape."""

from __future__ import annotations

from backend.server.progression import (
    reduce_epic,
    reduce_progression,
    reduce_tiers,
    reduce_trakanon,
)

# Catalog facts pinned from data/quests/*.json
TEMPLAR_MYTHICAL = 2252724587  # Bringing the Hammer Down on Venril
TRAK_CRC = 3449518442  # Taking on Trakanon
TRAK_KILL_ACH = 1100876855  # Trakanon's Tormenter


def _templar_chain_crcs() -> tuple[list[int], list[int]]:
    from backend.server.progression import _catalogs

    epics, _, _ = _catalogs()
    e = epics["classes"]["Templar"]
    return (
        [q["crc"] for q in e["fabled"]["quests"] if q.get("crc")],
        [q["crc"] for q in e["mythical"]["quests"] if q.get("crc")],
    )


# ── Epic ─────────────────────────────────────────────────────────────────────


def test_epic_none():
    e = reduce_epic("Templar", {}, {})
    assert e is not None
    assert e["state"] == "none"
    assert e["fabled"]["current_step"] is None


def test_epic_unknown_class_is_none():
    assert reduce_epic("", {}, {}) is None
    assert reduce_epic("NotAClass", {}, {}) is None


def test_epic_fabled_in_progress_with_stage_text():
    fabled, _ = _templar_chain_crcs()
    first, last = fabled[0], fabled[-1]
    completed = {first: "2026-09-10"}
    active = {last: {"crc": last, "stage": "I must confront Venril."}}
    e = reduce_epic("Templar", completed, active)
    assert e["state"] == "fabled_progress"
    f = e["fabled"]
    assert (f["steps_done"], f["steps_total"]) == (1, len(fabled))
    assert f["current_step"] == 2
    assert f["current_name"]  # the next quest's name from the catalog
    assert f["current_stage"] == "I must confront Venril."


def test_epic_fabled_done_then_mythical():
    fabled, mythical = _templar_chain_crcs()
    completed = {crc: "2026-09-12" for crc in fabled}
    e = reduce_epic("Templar", completed, {})
    assert e["state"] == "fabled"
    assert e["fabled"]["done"] and e["fabled"]["date"] == "2026-09-12"

    completed.update({crc: "2026-09-20" for crc in mythical})
    e = reduce_epic("Templar", completed, {})
    assert e["state"] == "mythical"
    assert e["mythical"]["done"]


def test_epic_active_only_counts_as_started():
    fabled, _ = _templar_chain_crcs()
    active = {fabled[0]: {"crc": fabled[0], "stage": "step one"}}
    e = reduce_epic("Templar", {}, active)
    assert e["state"] == "fabled_progress"
    assert e["fabled"]["current_step"] == 1


# ── Tiers ────────────────────────────────────────────────────────────────────


def test_tiers_partial_and_complete():
    earned = {1957990056: 100, 1320853425: 200}  # Imzok + Pawbuster, no Tairiza
    t = reduce_tiers(earned)
    assert (t["T1"]["earned"], t["T1"]["total"], t["T1"]["complete"]) == (2, 3, False)
    missing = [b["boss"] for b in t["T1"]["bosses"] if not b["earned"]]
    assert missing == ["Tairiza the Widow Mistress"]
    assert t["T4"]["earned"] == 0

    earned[706553905] = 300
    t = reduce_tiers(earned)
    assert t["T1"]["complete"] is True


# ── Trakanon ─────────────────────────────────────────────────────────────────


def test_trakanon_states():
    # not started
    t = reduce_trakanon({}, {}, {})
    assert t["state"] == "none" and t["killed_trakanon"] is False

    # completed + kill achievement
    t = reduce_trakanon({TRAK_CRC: "2026-10-01"}, {}, {TRAK_KILL_ACH: 1790000000})
    assert t["state"] == "completed" and t["date"] == "2026-10-01"
    assert t["killed_trakanon"] is True


def test_trakanon_in_progress_matches_bosses_by_name():
    entry = {
        "crc": TRAK_CRC,
        "stage": "I must kill all of the dragons and their guardians in Veeshan's Peak.",
        "requiredItem_list": [
            {"progress": 1, "quota": 1, "progress_text": "I must kill Druushk."},
            {"progress": 0, "quota": 1, "progress_text": "I must kill Phara Dar."},
            # per-character lists can OMIT canonical bosses (observed live) —
            # the missing ones must read as not-killed, not crash.
        ],
    }
    t = reduce_trakanon({}, {TRAK_CRC: entry}, {})
    assert t["state"] == "in_progress"
    assert t["killed"] == 1 and t["total"] == 12
    by_boss = {r["boss"]: r["killed"] for r in t["bosses"]}
    assert by_boss["Druushk"] is True
    assert by_boss["Phara Dar"] is False
    assert by_boss["Travenro the Skygazer"] is False  # omitted from the entry


def test_trakanon_ready_to_turn_in():
    entry = {
        "crc": TRAK_CRC,
        "stage": "I need to let Snyr'dok know that I have defeated all of the dragons and their guardians.",
    }
    t = reduce_trakanon({}, {TRAK_CRC: entry}, {})
    assert t["state"] == "ready_to_turn_in"


# ── Composition ──────────────────────────────────────────────────────────────


def test_reduce_progression_shapes():
    p = reduce_progression(
        "Templar",
        [{"crc": TEMPLAR_MYTHICAL, "completion_date": "2026-09-20"}],
        [],
        [{"id": 1957990056, "completed_timestamp": 1790000000}],
    )
    assert set(p.keys()) == {"epic", "tiers", "trakanon"}
    assert p["tiers"]["T1"]["earned"] == 1
    # Mythical completion without the fabled chain still reads mythical-side
    # progress correctly (the fabled chain is separate).
    assert p["epic"]["mythical"]["done"] is True
