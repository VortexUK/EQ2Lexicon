"""
Dataclasses that mirror ACT's ODBC export schema at AttackType depth.

ACT writes (at depth 4) four tables we read:
  encounter_table   – one row per fight
  combatant_table   – one row per actor per fight (allies + enemies)
  damagetype_table  – one row per damage type per combatant per fight
  attacktype_table  – one row per ability per combatant per fight

Field naming on the Python side normalises ACT's column names: snake_case
throughout, `*_s` for second-based durations, and percentage VARCHARs
(e.g. '93%' or '--') are parsed to floats with `_to_perc`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _to_int(v) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def _to_float(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_perc(v) -> float:
    """Parse ACT's percentage strings ('100%', '93%', '--', '') into a float.

    `'--'` and blanks both coerce to 0.0 — ACT uses '--' when a combatant
    contributed no damage/heals so the percentage is meaningless.
    """
    if v is None or v == "" or v == "--":
        return 0.0
    s = str(v).strip().rstrip("%").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _to_bool_tf(v) -> bool:
    """ACT writes 'T'/'F' for boolean columns (e.g. combatant_table.ally)."""
    if v is None:
        return False
    return str(v).strip().upper() == "T"


def _to_ts(v) -> datetime | None:
    """Parse ACT's TIMESTAMP into a naive datetime. Empirically the SQLite
    ODBC driver writes them as `'YYYY-MM-DD HH:MM:SS'` (local clock)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Encounter:
    encid: str
    title: str
    zone: str | None
    started_at: datetime
    ended_at: datetime
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    # ACT's GetEncounterSuccessLevel(): 0=unknown, 1=win, 2=loss, 3=mixed.
    # Defaults to 0 so the local-ingest reader (which can't compute it) and
    # existing tests don't need to thread it through.
    success_level: int = 0


@dataclass(frozen=True)
class Combatant:
    encid: str
    name: str
    ally: bool  # ACT's 'T'/'F' for friendly vs enemy
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: int
    damage: int
    damage_perc: float  # '100%' / '--' parsed to float (0..100)
    kills: int
    healed: int
    healed_perc: float
    crit_heals: int
    heals: int
    cure_dispels: int
    power_drain: int
    power_replenish: int
    dps: float
    encdps: float
    enchps: float  # heals per second over encounter duration
    hits: int
    crit_hits: int
    blocked: int
    misses: int
    swings: int
    heals_taken: int
    damage_taken: int
    deaths: int
    to_hit: float
    crit_dam_perc: float  # '93%' parsed → 93.0
    crit_heal_perc: float
    crit_types: str | None  # raw, e.g. '0.8%L - 0.0%F - 0.0%M' or '-'
    threat_str: str | None  # raw, e.g. '+(0)20000/-(0)0'
    threat_delta: int


@dataclass(frozen=True)
class DamageType:
    encid: str
    combatant_name: str  # ACT column is `combatant`
    grouping_label: str | None  # ACT column is `grouping` (lives here, not on combatant)
    damage_type: str  # ACT column is `type`
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: int
    damage: int
    encdps: float
    char_dps: float
    dps: float
    average: float
    median: int
    min_hit: int
    max_hit: int
    hits: int
    crit_hits: int
    blocked: int
    misses: int
    swings: int
    to_hit: float
    average_delay: float
    crit_perc: float
    crit_types: str | None


@dataclass(frozen=True)
class AttackType:
    encid: str
    combatant_name: str  # ACT column is `attacker`
    victim: str | None
    swing_type: int  # 100='All' rollup (filtered at ingest); 1=melee; 2=spell, etc.
    attack_name: str  # ACT column is `type`
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: int
    damage: int
    encdps: float
    char_dps: float
    dps: float
    average: float
    median: int
    min_hit: int
    max_hit: int
    resist: str | None
    hits: int
    crit_hits: int
    blocked: int
    misses: int
    swings: int
    to_hit: float
    average_delay: float
    crit_perc: float
    crit_types: str | None
