"""RoK progression reduction — epics, raid-tier flags, Trakanon access.

Pure logic over the three committed catalogs in data/quests/ (built by
scripts/dev/build_epics_json.py + census research, 2026-09-02):

  epics.json      class -> fabled/mythical quest chains with census crcs
  rok_access.json T1-T4 tier ladder -> per-boss kill achievement ids
  trakanon.json   the Taking on Trakanon access quest (per-boss objectives)

Inputs come from two census fetches per character:
  character_misc (by char id) -> completed_quest_list [{crc, completion_date}]
                                 + quest_list (active; stage text + per-
                                 objective progress)
  character      (by char id) -> achievements.achievement_list
                                 [{id, completed_timestamp}]

Everything here is synchronous + side-effect free so it unit-tests without
census. The API layer (api/progression.py) owns fetching and caching.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "quests"

_KILL_RE = re.compile(r"I must kill (.+?)\.?$")


@lru_cache(maxsize=1)
def _catalogs() -> tuple[dict, dict, dict]:
    epics = json.loads((_DATA_DIR / "epics.json").read_text(encoding="utf-8"))
    access = json.loads((_DATA_DIR / "rok_access.json").read_text(encoding="utf-8"))
    trakanon = json.loads((_DATA_DIR / "trakanon.json").read_text(encoding="utf-8"))
    return epics, access, trakanon


def clear_caches() -> None:
    _catalogs.cache_clear()


# ---------------------------------------------------------------------------
# Epic (fabled + mythical)
# ---------------------------------------------------------------------------


def _reduce_chain(
    chain: list[dict],
    completed: dict[int, str | None],
    active: dict[int, dict],
) -> dict:
    """Reduce one quest chain (fabled or mythical) to progress.

    Steps with crc null (book/item steps census can't see) are excluded from
    the step math. "Current step" is the first not-completed detectable step;
    when that step is ACTIVE we also carry its live stage text.
    """
    steps = [q for q in chain if q.get("crc")]
    total = len(steps)
    done = 0
    current_name: str | None = None
    current_stage: str | None = None
    completion_date: str | None = None
    for q in steps:
        crc = q["crc"]
        if crc in completed:
            done += 1
            completion_date = completed[crc] or completion_date
            continue
        if current_name is None:
            current_name = q["name"]
            entry = active.get(crc)
            if entry:
                current_stage = entry.get("stage") or None
        # keep counting further completed steps (chains are linear; a gap
        # just means census missed one — count what we can see)
    finished = total > 0 and done >= total
    started = done > 0 or any(q["crc"] in active for q in steps)
    mid_chain = started and not finished
    return {
        "done": finished,
        "date": completion_date if finished else None,
        "steps_done": done,
        "steps_total": total,
        # Only meaningful mid-chain: the next step's position, quest name and
        # (when that quest is active) its live stage text.
        "current_step": done + 1 if mid_chain else None,
        "current_name": current_name if mid_chain else None,
        "current_stage": current_stage if mid_chain else None,
        "started": started,
    }


def reduce_epic(cls: str, completed: dict[int, str | None], active: dict[int, dict]) -> dict | None:
    """Epic progress for a class. None when the class has no epic catalog
    entry (unknown/blank class)."""
    epics, _, _ = _catalogs()
    entry = epics["classes"].get(cls)
    if entry is None:
        return None
    fabled = _reduce_chain(entry["fabled"]["quests"], completed, active)
    mythical = _reduce_chain(entry["mythical"]["quests"], completed, active)
    if mythical["done"]:
        state = "mythical"
    elif fabled["done"]:
        state = "mythical_progress" if mythical["started"] else "fabled"
    elif fabled["started"]:
        state = "fabled_progress"
    else:
        state = "none"
    return {
        "weapon": entry["fabled"]["weapon"],
        "state": state,
        "fabled": fabled,
        "mythical": mythical,
    }


# ---------------------------------------------------------------------------
# Raid-tier flags (kill achievements)
# ---------------------------------------------------------------------------


def reduce_tiers(achievements: dict[int, int | None]) -> dict:
    """Tier ladder state from earned achievement ids -> unix timestamps."""
    _, access, _ = _catalogs()
    out: dict = {}
    for tier, bosses in access["tiers"].items():
        rows = []
        for b in bosses:
            aid = b["achievement"]["id"]
            ts = achievements.get(aid)
            rows.append(
                {
                    "boss": b["boss"],
                    "zone": b["zone"],
                    "earned": ts is not None,
                    "earned_at": ts,
                }
            )
        out[tier] = {
            "earned": sum(1 for r in rows if r["earned"]),
            "total": len(rows),
            "complete": all(r["earned"] for r in rows),
            "bosses": rows,
        }
    return out


# ---------------------------------------------------------------------------
# Trakanon access quest
# ---------------------------------------------------------------------------


def reduce_trakanon(
    completed: dict[int, str | None], active: dict[int, dict], achievements: dict[int, int | None]
) -> dict:
    """Taking on Trakanon state + per-boss objective progress + the
    Trakanon kill itself (achievement)."""
    _, access, trak = _catalogs()
    crc = trak["quest"]["crc"]
    canonical = trak["bosses"]

    kill_aid = next(
        (e["achievement"]["id"] for e in access.get("extra", []) if e["boss"] == "Trakanon"),
        None,
    )
    trak_kill_ts = achievements.get(kill_aid) if kill_aid else None

    if crc in completed:
        return {
            "state": "completed",
            "date": completed[crc],
            "killed_trakanon": trak_kill_ts is not None,
            "killed_trakanon_at": trak_kill_ts,
            "bosses": None,
        }

    entry = active.get(crc)
    if entry is None:
        return {
            "state": "none",
            "date": None,
            "killed_trakanon": trak_kill_ts is not None,
            "killed_trakanon_at": trak_kill_ts,
            "bosses": None,
        }

    # In progress. Per-character objective lists can omit bosses vs the
    # canonical 12 (observed live) — match by name from progress_text, and
    # treat canonical bosses absent from the list as not-yet-killed.
    by_name: dict[str, dict] = {}
    for item in entry.get("requiredItem_list", []) or []:
        m = _KILL_RE.search(item.get("progress_text") or "")
        if m:
            by_name[m.group(1)] = item
    rows = []
    for boss in canonical:
        item = by_name.get(boss)
        killed = bool(item) and (item.get("progress") or 0) >= (item.get("quota") or 1)
        rows.append({"boss": boss, "killed": killed})
    stage_text = entry.get("stage") or ""
    ready = "Snyr'dok" in stage_text
    return {
        "state": "ready_to_turn_in" if ready else "in_progress",
        "date": None,
        "killed_trakanon": trak_kill_ts is not None,
        "killed_trakanon_at": trak_kill_ts,
        "bosses": rows,
        "killed": len(rows) if ready else sum(1 for r in rows if r["killed"]),
        "total": len(rows),
        "stage": stage_text or None,
    }


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def reduce_progression(
    cls: str | None,
    completed_quests: list[dict],
    active_quests: list[dict],
    achievement_list: list[dict],
) -> dict:
    """Full RoK progression for one character from raw census lists."""
    completed = {q["crc"]: q.get("completion_date") for q in completed_quests if "crc" in q}
    active = {q["crc"]: q for q in active_quests if "crc" in q}
    achievements = {a["id"]: a.get("completed_timestamp") for a in achievement_list if "id" in a}
    return {
        "epic": reduce_epic(cls or "", completed, active),
        "tiers": reduce_tiers(achievements),
        "trakanon": reduce_trakanon(completed, active, achievements),
    }
