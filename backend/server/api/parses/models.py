"""Pydantic models shared across the parses route sub-modules.

Carved out of the original 1687-line web/routes/parses.py. NOTHING in this
file imports from another parses sub-module — keep it that way to avoid
circular-import pain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Response models — GET /parses and GET /parses/{id}
# ---------------------------------------------------------------------------


class ParsePermissions(BaseModel):
    """Per-row flags so the UI can render delete buttons only when allowed.
    Computed against the logged-in session: admin gets all true, officer of
    the row's guild gets can_delete=true, original uploader gets it for their
    own rows."""

    can_delete: bool = False


class ParseUploadSummary(BaseModel):
    """One raider's submission within a mirror group. Smaller than
    ParseEncounterSummary — just the fields the expansion UI on /parses
    actually needs (per-uploader link, duration, damage, dps, deletion
    rights).

    ``uploaded_by`` is the EQ2 character name (ACT's logger_name) for
    backward compatibility — still useful for "whose POV is this parse
    from?". ``uploader_discord_id`` + ``uploader_display_name`` are the
    Discord identity of the human who uploaded it, resolved from
    ``source_dsn`` (``"plugin:<discord_id>"``) at response build time.
    Pre-plugin or non-plugin uploads (``source_dsn = "eq2act"`` /
    ``"local"``) carry None for both Discord fields."""

    id: int
    uploaded_by: str
    uploader_discord_id: str | None = None
    uploader_display_name: str | None = None
    started_at: int
    duration_s: int
    total_damage: int
    encdps: float
    success_level: int
    permissions: ParsePermissions = ParsePermissions()


class ParseEncounterSummary(BaseModel):
    """One FIGHT. Top-level fields are from the canonical upload (the
    raider whose ACT captured the longest duration); `uploads` holds every
    raider's view of the same fight. Mirror grouping is by
    (guild_name, title, started_at within ±MIRROR_WINDOW_S) and only ever
    merges uploads from *distinct* uploaders."""

    id: int
    act_encid: str
    title: str
    zone: str | None
    started_at: int  # unix seconds, UTC
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    success_level: int  # ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
    combatant_count: int
    player_count: int  # ally combatants with single-word names, excluding 'Unknown'
    # Backed by web/routes/parses/list.py:_classify_zone — Raid / Dungeon /
    # Other bucketing for the ParsesPage hierarchy. Computed at query time
    # from the zone field against zones.db; not persisted on the encounters
    # table.
    category: Literal["raid", "dungeon", "other"]
    uploaded_by: str  # who ingested the canonical upload; 'local' for local-only era
    # Discord identity of the canonical upload's submitter — same shape as
    # ParseUploadSummary's fields. Surfaced here too so the list view can
    # render the badge directly without needing to dig into uploads[0].
    uploader_discord_id: str | None = None
    uploader_display_name: str | None = None
    guild_name: str | None  # stamped at ingest time from uploader's Census guild
    permissions: ParsePermissions = ParsePermissions()
    uploads: list[ParseUploadSummary] = []  # always at least 1 (the canonical itself)


class ParsesListResponse(BaseModel):
    results: list[ParseEncounterSummary]
    total: int  # total number of FIGHTS matching the filter (pre-limit)


class AttackSummary(BaseModel):
    attack_name: str
    damage: int
    hits: int
    swings: int
    crit_perc: float
    max_hit: int


class HealSummary(BaseModel):
    """Per-ability heal rollup. ACT writes heals into attacktype_table at
    swing_type=3; the `damage` column there is the amount healed, and
    `resist` distinguishes regular heals ('Hitpoints') from wards
    ('Absorption')."""

    heal_name: str
    healed: int
    hits: int
    swings: int
    crit_perc: float
    max_hit: int
    heal_type: str | None  # 'Hitpoints' (regular heal) or 'Absorption' (ward)


class CureSummary(BaseModel):
    """Cure events (swing_type=20). `effects_removed` is the count of
    detrimental effects cleared (ACT writes this into the `damage` column);
    `times_cast` is hit count."""

    cure_name: str
    effects_removed: int
    times_cast: int
    max_at_once: int


class ThreatSummary(BaseModel):
    """Threat / buff proc (swing_type=100, type != 'All'). For threat
    procs `value` is the threat amount; `procs` is how many times it fired."""

    ability_name: str
    value: int
    procs: int
    max_proc: int
    kind: str | None  # ACT's `resist` column — 'Increase' for threat procs


class DamageTypeBreakdown(BaseModel):
    damage_type: str
    damage: int
    dps: float
    hits: int
    swings: int
    max_hit: int
    crit_perc: float


class CombatantSummary(BaseModel):
    id: int
    name: str
    ally: bool
    # Pet-detection pipeline output (see parses/pet_detection.py).
    # Authoritative player/pet signal — drives the frontend Allies/Pets
    # split on the parse detail page. Bucket-fill-promoted combatants
    # are visually identical to Census-resolved players (per the spec's
    # "keep it clean" UX call).
    is_player: bool
    # Identity frozen at ingest time (resolved from character_cache). NULL for
    # pets/NPCs, unresolved players, and parses ingested before this existed —
    # the frontend falls back to the live /api/characters/lookup for those.
    level: int | None = None
    guild_name: str | None = None
    cls: str | None = None
    duration_s: int
    damage: int
    damage_perc: float
    dps: float
    encdps: float
    # encDPS/encHPS ranked against this class's best for this boss (class leader
    # = 100), for percentile colouring on the parse page; None for non-players /
    # unresolved characters / no data. *_best_overall flags the all-class best.
    dps_percentile: int | None = None
    dps_best_overall: bool = False
    hps_percentile: int | None = None
    hps_best_overall: bool = False
    healed: int
    enchps: float
    heals: int
    crit_heals: int
    cure_dispels: int
    power_drain: int
    power_replenish: int
    heals_taken: int
    damage_taken: int
    threat_delta: int
    deaths: int
    kills: int
    crit_hits: int
    crit_dam_perc: float
    top_attacks: list[AttackSummary]
    top_heals: list[HealSummary]
    top_cures: list[CureSummary]
    top_threats: list[ThreatSummary]
    damage_types: list[DamageTypeBreakdown]


class ParseDetailResponse(BaseModel):
    id: int
    act_encid: str
    title: str
    zone: str | None
    started_at: int
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    success_level: int  # ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
    # Who uploaded this specific parse. ``uploaded_by`` is the character
    # name (kept for backward compatibility and "whose POV is this?");
    # ``uploader_discord_id`` + ``uploader_display_name`` carry the
    # Discord identity for badge rendering. None on pre-plugin uploads
    # (``source_dsn`` = ``"eq2act"`` / ``"local"``).
    uploaded_by: str = "local"
    uploader_discord_id: str | None = None
    uploader_display_name: str | None = None
    hidden: bool = False  # True when the parse is soft-deleted (still openable via a ranking link)
    combatants: list[CombatantSummary]


# ---------------------------------------------------------------------------
# Ingest sub-object models — typed wrappers for combatants/damage/attack rows
# ---------------------------------------------------------------------------


class IngestCombatant(BaseModel):
    """One ACT combatant row as shipped by the plugin.

    ``model_config = {"extra": "allow"}`` ensures new plugin fields pass
    through unchanged — forward compatibility with future plugin versions
    that may add columns.
    """

    model_config = {"extra": "allow"}

    name: str = ""
    ally: str | None = None  # "T" / "F" in ACT's export
    starttime: str | None = None
    endtime: str | None = None
    duration: int | None = None
    damage: int | None = None
    damageperc: str | None = None
    kills: int | None = None
    healed: int | None = None
    healedperc: str | None = None
    critheals: int | None = None
    heals: int | None = None
    curedispels: int | None = None
    powerdrain: int | None = None
    powerreplenish: int | None = None
    dps: float | None = None
    encdps: float | None = None
    enchps: float | None = None
    hits: int | None = None
    crithits: int | None = None
    blocked: int | None = None
    misses: int | None = None
    swings: int | None = None
    healstaken: int | None = None
    damagetaken: int | None = None
    deaths: int | None = None
    tohit: float | None = None
    critdamperc: str | None = None
    crithealperc: str | None = None
    crittypes: str | None = None
    threatstr: str | None = None
    threatdelta: int | None = None


class IngestDamageType(BaseModel):
    """One ACT damage-type row as shipped by the plugin."""

    model_config = {"extra": "allow"}

    combatant: str = ""
    type: str = ""  # damage type label, e.g. "Slashing"
    grouping: str | None = None
    starttime: str | None = None
    endtime: str | None = None
    duration: int | None = None
    damage: int | None = None
    encdps: float | None = None
    chardps: float | None = None
    dps: float | None = None
    average: float | None = None
    median: int | None = None
    minhit: int | None = None
    maxhit: int | None = None
    hits: int | None = None
    crithits: int | None = None
    blocked: int | None = None
    misses: int | None = None
    swings: int | None = None
    tohit: float | None = None
    averagedelay: float | None = None
    critperc: str | None = None
    crittypes: str | None = None


class IngestAttackType(BaseModel):
    """One ACT attack-type row as shipped by the plugin."""

    model_config = {"extra": "allow"}

    attacker: str = ""
    type: str = ""  # attack name, e.g. "Crushing Blow"
    victim: str | None = None
    swingtype: int | None = None
    starttime: str | None = None
    endtime: str | None = None
    duration: int | None = None
    damage: int | None = None
    encdps: float | None = None
    chardps: float | None = None
    dps: float | None = None
    average: float | None = None
    median: int | None = None
    minhit: int | None = None
    maxhit: int | None = None
    resist: str | None = None
    hits: int | None = None
    crithits: int | None = None
    blocked: int | None = None
    misses: int | None = None
    swings: int | None = None
    tohit: float | None = None
    averagedelay: float | None = None
    critperc: str | None = None
    crittypes: str | None = None


# ---------------------------------------------------------------------------
# Ingest models — POST /parses/ingest
# ---------------------------------------------------------------------------


class IngestEncounter(BaseModel):
    encid: str = Field(min_length=1, max_length=16)
    title: str
    zone: str | None = None
    starttime: str
    endtime: str
    duration: int = 0
    damage: int = 0
    encdps: float = 0
    kills: int = 0
    deaths: int = 0
    # ACT's GetEncounterSuccessLevel(): 0=unknown, 1=win, 2=loss, 3=mixed.
    success: int = 0


class IngestRequest(BaseModel):
    """ACT-shaped upload payload. Sub-lists use typed Pydantic models so the
    keys are validated. ``model_config = {"extra": "allow"}`` on the sub-models
    keeps forward-compatibility with newer plugin versions that may add columns.
    Column names match the ACT export keys; the plugin's PayloadBuilder (in
    the EQ2LexiconACTPlugin repo) is the canonical wire-shape reference."""

    logger_name: str = Field(min_length=1, max_length=64)
    # EQ2 server the upload came from (Varsoon, Kaladim, Butcherblock,
    # …). Plugin v0.1.10+ detects this from the log file's parent
    # directory and stamps it on every upload; older versions and the
    # local-ingest path omit it and the route falls back to EQ2_WORLD.
    # Optional so older plugins keep working through the rollout.
    logger_server: str | None = Field(default=None, max_length=64)
    encounter: IngestEncounter
    combatants: list[IngestCombatant] = []
    damage_types: list[IngestDamageType] = []
    attack_types: list[IngestAttackType] = []
    # Soft warnings the plugin (v0.1.15+) attaches when something looked
    # off but not bad enough to block the upload. Currently just
    # ``"folder_hint_mismatch"`` — ACT's per-encounter HistoryRecord.FolderHint
    # disagreed with the detected logger_server. Hard tamper signals
    # (rename, import) go via the separate /parses/tamper-report channel
    # and never reach here.
    #
    # Caps mirror the plugin-side defensive limits (32 entries × 64 chars)
    # so a hostile/buggy client can't fill the column with megabytes of
    # garbage. Old plugins never send the field; the column stays NULL.
    client_warnings: list[str] | None = Field(default=None, max_length=32)


class IngestResponse(BaseModel):
    status: str  # 'inserted', 'revived', or 'skipped'
    encounter_id: int | None  # our internal id (None for skipped)
    act_encid: str
    combatants: int
    damage_types: int
    attack_types: int
    guild_name: str | None


# ---------------------------------------------------------------------------
# Tamper-report models — POST /parses/tamper-report
# ---------------------------------------------------------------------------


class TamperReportResponse(BaseModel):
    """Acknowledgement the audit POST landed. The plugin is fire-and-forget
    here — it never surfaces this to the user — so the response is minimal.
    ``id`` is returned for log correlation only."""

    id: int
    reason: str


# ---------------------------------------------------------------------------
# Delete-response model
# ---------------------------------------------------------------------------


class DeleteParsesResponse(BaseModel):
    deleted: int
