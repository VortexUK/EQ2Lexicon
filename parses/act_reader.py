"""
Read encounters from the SQLite file ACT writes via its ODBC export.

ACT writes through the SQLite ODBC driver, but once the .db file exists we
can read it directly with stdlib sqlite3 — no need for pyodbc on the read
side. We open read-only via URI to avoid locking ACT out.

ACT's tables at AttackType depth (option 4):
  encounter_table   – fight-level
  combatant_table   – per-actor-per-fight
  damagetype_table  – per-combatant per damage type
  attacktype_table  – per-combatant per ability

Column-name reality (confirmed against a real ACT export):
  - combatant_table has NO `class`/`role`/`maxhit`/`grouping` columns
  - damagetype_table uses `combatant` (not `attacker`) and contains `grouping`
  - attacktype_table uses `attacker` and includes a swingtype=100 'All' rollup
    row per combatant — we skip those, they're aggregates we'd otherwise
    double-count.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from parses.models import (
    AttackType,
    Combatant,
    DamageType,
    Encounter,
    _to_bool_tf,
    _to_float,
    _to_int,
    _to_perc,
    _to_str_or_none,
    _to_ts,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _default_act_db_path() -> Path:
    env = os.getenv("DB_ACT_EXPORT_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "parses" / "act_export.db"


ACT_DB_PATH: Path = _default_act_db_path()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def open_act_db(path: Path = ACT_DB_PATH) -> sqlite3.Connection:
    """Open ACT's export DB read-only. Caller is responsible for closing."""
    if not path.exists():
        raise FileNotFoundError(
            f"ACT export DB not found at {path}. "
            "Has ACT written at least one encounter? "
            "See data/parses/ and your ODBC DSN configuration."
        )
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Encounters
# ---------------------------------------------------------------------------


def list_encounter_ids(
    conn: sqlite3.Connection,
    since_encid: str | None = None,
) -> list[str]:
    """Encids in starttime ascending order.

    Filters out half-written encounters: requires a non-empty `endtime` AND
    at least one combatant.
    """
    if since_encid is None:
        rows = conn.execute(
            """
            SELECT e.encid
            FROM encounter_table e
            WHERE e.endtime IS NOT NULL AND e.endtime <> ''
              AND EXISTS (
                SELECT 1 FROM combatant_table c WHERE c.encid = e.encid
              )
            ORDER BY e.starttime ASC
            """
        ).fetchall()
        return [r["encid"] for r in rows]

    since_row = conn.execute(
        "SELECT starttime FROM encounter_table WHERE encid = ?",
        (since_encid,),
    ).fetchone()
    if since_row is None:
        return list_encounter_ids(conn)
    rows = conn.execute(
        """
        SELECT e.encid
        FROM encounter_table e
        WHERE e.starttime > ?
          AND e.endtime IS NOT NULL AND e.endtime <> ''
          AND EXISTS (
            SELECT 1 FROM combatant_table c WHERE c.encid = e.encid
          )
        ORDER BY e.starttime ASC
        """,
        (since_row["starttime"],),
    ).fetchall()
    return [r["encid"] for r in rows]


def get_encounter(conn: sqlite3.Connection, encid: str) -> Encounter | None:
    row = conn.execute(
        """
        SELECT encid, title, zone, starttime, endtime, duration,
               damage, encdps, kills, deaths
        FROM encounter_table
        WHERE encid = ?
        """,
        (encid,),
    ).fetchone()
    if row is None:
        return None
    started = _to_ts(row["starttime"])
    ended = _to_ts(row["endtime"])
    if started is None or ended is None:
        return None
    return Encounter(
        encid=row["encid"],
        title=str(row["title"] or ""),
        zone=_to_str_or_none(row["zone"]),
        started_at=started,
        ended_at=ended,
        duration_s=_to_int(row["duration"]),
        total_damage=_to_int(row["damage"]),
        encdps=_to_float(row["encdps"]),
        kills=_to_int(row["kills"]),
        deaths=_to_int(row["deaths"]),
    )


# ---------------------------------------------------------------------------
# Combatants
# ---------------------------------------------------------------------------


def get_combatants(conn: sqlite3.Connection, encid: str) -> list[Combatant]:
    rows = conn.execute(
        """
        SELECT encid, name, ally,
               starttime, endtime, duration,
               damage, damageperc, kills,
               healed, healedperc, critheals, heals, curedispels,
               powerdrain, powerreplenish,
               dps, encdps, enchps,
               hits, crithits, blocked, misses, swings,
               healstaken, damagetaken, deaths,
               tohit, critdamperc, crithealperc, crittypes,
               threatstr, threatdelta
        FROM combatant_table
        WHERE encid = ?
        """,
        (encid,),
    ).fetchall()
    return [
        Combatant(
            encid=r["encid"],
            name=str(r["name"] or ""),
            ally=_to_bool_tf(r["ally"]),
            started_at=_to_ts(r["starttime"]),
            ended_at=_to_ts(r["endtime"]),
            duration_s=_to_int(r["duration"]),
            damage=_to_int(r["damage"]),
            damage_perc=_to_perc(r["damageperc"]),
            kills=_to_int(r["kills"]),
            healed=_to_int(r["healed"]),
            healed_perc=_to_perc(r["healedperc"]),
            crit_heals=_to_int(r["critheals"]),
            heals=_to_int(r["heals"]),
            cure_dispels=_to_int(r["curedispels"]),
            power_drain=_to_int(r["powerdrain"]),
            power_replenish=_to_int(r["powerreplenish"]),
            dps=_to_float(r["dps"]),
            encdps=_to_float(r["encdps"]),
            enchps=_to_float(r["enchps"]),
            hits=_to_int(r["hits"]),
            crit_hits=_to_int(r["crithits"]),
            blocked=_to_int(r["blocked"]),
            misses=_to_int(r["misses"]),
            swings=_to_int(r["swings"]),
            heals_taken=_to_int(r["healstaken"]),
            damage_taken=_to_int(r["damagetaken"]),
            deaths=_to_int(r["deaths"]),
            to_hit=_to_float(r["tohit"]),
            crit_dam_perc=_to_perc(r["critdamperc"]),
            crit_heal_perc=_to_perc(r["crithealperc"]),
            crit_types=_to_str_or_none(r["crittypes"]),
            threat_str=_to_str_or_none(r["threatstr"]),
            threat_delta=_to_int(r["threatdelta"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Damage types
# ---------------------------------------------------------------------------


def get_damage_types(
    conn: sqlite3.Connection,
    encid: str,
    combatant_name: str | None = None,
) -> list[DamageType]:
    base_select = """
        SELECT encid, combatant, "grouping" AS grouping_label,
               type, starttime, endtime, duration,
               damage, encdps, chardps, dps,
               average, median, minhit, maxhit,
               hits, crithits, blocked, misses, swings,
               tohit, averagedelay, critperc, crittypes
        FROM damagetype_table
    """
    if combatant_name is None:
        rows = conn.execute(base_select + " WHERE encid = ?", (encid,)).fetchall()
    else:
        rows = conn.execute(
            base_select + " WHERE encid = ? AND combatant = ?",
            (encid, combatant_name),
        ).fetchall()
    return [
        DamageType(
            encid=r["encid"],
            combatant_name=str(r["combatant"] or ""),
            grouping_label=_to_str_or_none(r["grouping_label"]),
            damage_type=str(r["type"] or ""),
            started_at=_to_ts(r["starttime"]),
            ended_at=_to_ts(r["endtime"]),
            duration_s=_to_int(r["duration"]),
            damage=_to_int(r["damage"]),
            encdps=_to_float(r["encdps"]),
            char_dps=_to_float(r["chardps"]),
            dps=_to_float(r["dps"]),
            average=_to_float(r["average"]),
            median=_to_int(r["median"]),
            min_hit=_to_int(r["minhit"]),
            max_hit=_to_int(r["maxhit"]),
            hits=_to_int(r["hits"]),
            crit_hits=_to_int(r["crithits"]),
            blocked=_to_int(r["blocked"]),
            misses=_to_int(r["misses"]),
            swings=_to_int(r["swings"]),
            to_hit=_to_float(r["tohit"]),
            average_delay=_to_float(r["averagedelay"]),
            crit_perc=_to_perc(r["critperc"]),
            crit_types=_to_str_or_none(r["crittypes"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Attack types
# ---------------------------------------------------------------------------

# ACT writes a per-combatant rollup row with type='All' across various
# swingtypes (commonly 100, but observed at swingtype=2 for synthetic
# combatants like 'Unknown'). We filter on type='All' alone — that's the
# reliable signal.
#
# IMPORTANT: do NOT also filter `swingtype=100`. ACT uses swingtype=100 for
# more than just the 'All' rollup — it also stores per-ability stat-increase
# rows (threat procs like 'Undeniable Malice' with resist='Increase', and
# likely other proc/buff categories at depth 4). Filtering by swingtype here
# silently drops legitimate non-damage event rows from our DB.
_SKIP_ALL_ROLLUP = "type <> 'All'"


def get_attack_types(
    conn: sqlite3.Connection,
    encid: str,
    combatant_name: str | None = None,
) -> list[AttackType]:
    base_select = """
        SELECT encid, attacker, victim, swingtype,
               type AS attack_name,
               starttime, endtime, duration,
               damage, encdps, chardps, dps,
               average, median, minhit, maxhit, resist,
               hits, crithits, blocked, misses, swings,
               tohit, averagedelay, critperc, crittypes
        FROM attacktype_table
    """
    where = f" WHERE encid = ? AND {_SKIP_ALL_ROLLUP}"
    if combatant_name is None:
        rows = conn.execute(base_select + where, (encid,)).fetchall()
    else:
        rows = conn.execute(
            base_select + where + " AND attacker = ?",
            (encid, combatant_name),
        ).fetchall()
    return [
        AttackType(
            encid=r["encid"],
            combatant_name=str(r["attacker"] or ""),
            victim=_to_str_or_none(r["victim"]),
            swing_type=_to_int(r["swingtype"]),
            attack_name=str(r["attack_name"] or ""),
            started_at=_to_ts(r["starttime"]),
            ended_at=_to_ts(r["endtime"]),
            duration_s=_to_int(r["duration"]),
            damage=_to_int(r["damage"]),
            encdps=_to_float(r["encdps"]),
            char_dps=_to_float(r["chardps"]),
            dps=_to_float(r["dps"]),
            average=_to_float(r["average"]),
            median=_to_int(r["median"]),
            min_hit=_to_int(r["minhit"]),
            max_hit=_to_int(r["maxhit"]),
            resist=_to_str_or_none(r["resist"]),
            hits=_to_int(r["hits"]),
            crit_hits=_to_int(r["crithits"]),
            blocked=_to_int(r["blocked"]),
            misses=_to_int(r["misses"]),
            swings=_to_int(r["swings"]),
            to_hit=_to_float(r["tohit"]),
            average_delay=_to_float(r["averagedelay"]),
            crit_perc=_to_perc(r["critperc"]),
            crit_types=_to_str_or_none(r["crittypes"]),
        )
        for r in rows
    ]
