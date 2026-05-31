"""
Dataclasses for the server's in-memory representation of a parse.

Four nested levels that mirror what the ACT plugin uploads to
``POST /api/parses/ingest``:
  Encounter   – one fight
  Combatant   – one actor per fight (allies + enemies)
  DamageType  – one damage type per combatant per fight
  AttackType  – one ability per combatant per fight

Field naming is snake_case throughout, `*_s` for second-based durations,
and percentage strings (e.g. '93%' or '--') are parsed to floats via
``_to_perc``. The coercion helpers handle the looseness of the wire
shape: missing values, empty strings, ACT's 'T'/'F' bool encoding.

The shape itself predates the HTTP ingest path — these dataclasses
originally mirrored ACT's ODBC SQLite export at AttackType depth, used
by the now-removed ``parses.act_reader`` + ``parses.ingest`` CLIs. The
plugin's JSON payload carries the same shape forward so the dataclasses
serve the v0.1.8+ upload path unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


def _to_unix(dt: datetime | None) -> int:
    """Convert a datetime to unix-seconds for storage.

    Naive datetimes are treated as UTC (legacy plugin-v0.1.0 behaviour —
    see :func:`_to_ts`). ``None`` becomes 0 since the storage columns are
    ``INTEGER NOT NULL DEFAULT 0`` for the started_at/ended_at columns.
    """
    if dt is None:
        return 0
    if dt.tzinfo is None:
        return int(dt.replace(tzinfo=UTC).timestamp())
    return int(dt.timestamp())


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
    """Parse a timestamp string into a datetime.

    Two input shapes:
      * Plugin v0.1.1+ → ``"YYYY-MM-DDTHH:MM:SSZ"`` — explicit UTC, returns
        a tz-aware datetime.
      * Plugin v0.1.0 (now well below the version gate) → ``"YYYY-MM-DD HH:MM:SS"``
        — naive (represents the player's local clock). ``_to_unix`` later
        treats naive datetimes as UTC, which is the legacy behaviour:
        off by the local-vs-UTC offset for cross-timezone viewers, but
        self-consistent for a single user.
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    # ISO-with-Z form first — fromisoformat (Python 3.11+) handles trailing Z.
    if s.endswith("Z"):
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    # Legacy naive shapes from older plugin / local ingest.
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

    def as_db_params(
        self,
        *,
        world: str,
        source_dsn: str,
        ingested_at: int,
        uploaded_by: str = "local",
        guild_name: str | None = None,
    ) -> dict[str, object]:
        """Return a ``{column_name: value}`` dict ready for the ``:name``-style
        ``insert_encounter`` SQL. The model field ``encid`` maps to the
        ``act_encid`` column; datetime fields are converted to unix-seconds."""
        return {
            "world": world,
            "act_encid": self.encid,
            "title": self.title,
            "zone": self.zone,
            "started_at": _to_unix(self.started_at),
            "ended_at": _to_unix(self.ended_at),
            "duration_s": self.duration_s,
            "total_damage": self.total_damage,
            "encdps": self.encdps,
            "kills": self.kills,
            "deaths": self.deaths,
            "success_level": self.success_level,
            "source_dsn": source_dsn,
            "uploaded_by": uploaded_by,
            "guild_name": guild_name,
            "ingested_at": ingested_at,
        }


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

    def as_db_params(
        self,
        *,
        encounter_id: int,
        snapshot: CombatantSnapshot,
    ) -> dict[str, object]:
        """Return a ``{column_name: value}`` dict for ``insert_combatant``.
        ``ally`` is converted to its 0/1 stored form; datetimes to unix.
        ``snapshot`` carries the identity fields (level/guild/cls/ilvl)
        resolved at ingest time (NULLs are fine — non-players don't resolve)."""
        return {
            "encounter_id": encounter_id,
            "name": self.name,
            "ally": 1 if self.ally else 0,
            "started_at": _to_unix(self.started_at),
            "ended_at": _to_unix(self.ended_at),
            "duration_s": self.duration_s,
            "damage": self.damage,
            "damage_perc": self.damage_perc,
            "kills": self.kills,
            "healed": self.healed,
            "healed_perc": self.healed_perc,
            "crit_heals": self.crit_heals,
            "heals": self.heals,
            "cure_dispels": self.cure_dispels,
            "power_drain": self.power_drain,
            "power_replenish": self.power_replenish,
            "dps": self.dps,
            "encdps": self.encdps,
            "enchps": self.enchps,
            "hits": self.hits,
            "crit_hits": self.crit_hits,
            "blocked": self.blocked,
            "misses": self.misses,
            "swings": self.swings,
            "heals_taken": self.heals_taken,
            "damage_taken": self.damage_taken,
            "deaths": self.deaths,
            "to_hit": self.to_hit,
            "crit_dam_perc": self.crit_dam_perc,
            "crit_heal_perc": self.crit_heal_perc,
            "crit_types": self.crit_types,
            "threat_str": self.threat_str,
            "threat_delta": self.threat_delta,
            "level": snapshot.level,
            "guild_name": snapshot.guild_name,
            "cls": snapshot.cls,
            "ilvl": snapshot.ilvl,
        }


@dataclass(frozen=True)
class CombatantSnapshot:
    """A character's identity FROZEN at parse-ingest time. Resolved from the
    website's character_cache (Census-backed) when an upload arrives, then
    written onto the combatant row so it never changes if the player later
    levels up or switches guild. All fields are None for non-players (pets,
    NPCs) and for players we couldn't resolve at ingest time."""

    level: int | None = None
    guild_name: str | None = None
    cls: str | None = None
    ilvl: float | None = None


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

    def as_db_params(self, *, combatant_id: int) -> dict[str, object]:
        """Return a ``{column_name: value}`` dict for ``insert_damage_type``.
        ``combatant_name`` (the model's natural key) is replaced by the
        resolved ``combatant_id`` the caller passes in after the parent
        insert; datetimes convert to unix-seconds."""
        return {
            "combatant_id": combatant_id,
            "grouping_label": self.grouping_label,
            "damage_type": self.damage_type,
            "started_at": _to_unix(self.started_at),
            "ended_at": _to_unix(self.ended_at),
            "duration_s": self.duration_s,
            "damage": self.damage,
            "encdps": self.encdps,
            "char_dps": self.char_dps,
            "dps": self.dps,
            "average": self.average,
            "median": self.median,
            "min_hit": self.min_hit,
            "max_hit": self.max_hit,
            "hits": self.hits,
            "crit_hits": self.crit_hits,
            "blocked": self.blocked,
            "misses": self.misses,
            "swings": self.swings,
            "to_hit": self.to_hit,
            "average_delay": self.average_delay,
            "crit_perc": self.crit_perc,
            "crit_types": self.crit_types,
        }


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

    def as_db_params(self, *, combatant_id: int) -> dict[str, object]:
        """Return a ``{column_name: value}`` dict for ``insert_attack_type``.
        Same shape as :meth:`DamageType.as_db_params` plus the ``victim``,
        ``swing_type``, ``attack_name`` and ``resist`` columns."""
        return {
            "combatant_id": combatant_id,
            "victim": self.victim,
            "swing_type": self.swing_type,
            "attack_name": self.attack_name,
            "started_at": _to_unix(self.started_at),
            "ended_at": _to_unix(self.ended_at),
            "duration_s": self.duration_s,
            "damage": self.damage,
            "encdps": self.encdps,
            "char_dps": self.char_dps,
            "dps": self.dps,
            "average": self.average,
            "median": self.median,
            "min_hit": self.min_hit,
            "max_hit": self.max_hit,
            "resist": self.resist,
            "hits": self.hits,
            "crit_hits": self.crit_hits,
            "blocked": self.blocked,
            "misses": self.misses,
            "swings": self.swings,
            "to_hit": self.to_hit,
            "average_delay": self.average_delay,
            "crit_perc": self.crit_perc,
            "crit_types": self.crit_types,
        }
