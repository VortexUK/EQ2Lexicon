// Rotation simulator types — mirrors /api/character/{name}/rotation-data
// plus the engine's config/result shapes. Kept React-free so the engine
// and its tests import from here without pulling components in.

export interface DamageComponent {
  kind: 'hit' | 'dot'
  min_dmg: number
  max_dmg: number
  school: string
  target_scope: 'single' | 'encounter' | 'aoe'
  /** Raw condition text ("If target is undead") — null = unconditional.
   * Conditional damage only counts when the sim target toggles it on. */
  condition: string | null
  interval_s: number | null
  duration_s: number | null
  duration_estimated: boolean
  /** Census unscaled-tooltip disease: the damage text reads "1 - 2" on a
   * lv71 spell. Number carried as-is; the UI badges it as bad data. */
  suspect_low_value: boolean
  /** Generated from an "Applies X … every N seconds" pulse wrapper. */
  from_pulse?: boolean
}

/** Attack-driven proc damage ("On any combat or spell hit this spell has
 * a 50% chance to cast Bolt of Power…"). */
export interface Proc {
  trigger: 'any_hit' | 'melee_hit' | 'ability_cast' | 'spell_cast' | 'when_damaged' | 'target_cast' | string
  chance_pct: number
  name: string
  /** Rate-limited procs ("Triggers about 3.0 times per minute") — when
   * set, the rate replaces the trigger-event count. */
  per_minute: number | null
  /** "Grants a total of N triggers" — a per-application budget: the
   * payload fires exactly N times per cast of the carrying ability
   * (Slothful Spirit's Sloth's Habitat ×3). */
  trigger_count?: number | null
  components: DamageComponent[]
}

export interface RotationAbility {
  name: string
  base_name: string
  crc: number | null
  tier_name: string
  level: number
  spell_type: 'spells' | 'arts' | string
  beneficial: boolean
  cast_secs: number
  recast_secs: number
  recovery_secs: number
  target_type: string | null
  icon_id: number | null
  icon_backdrop: number | null
  duration_s: number | null
  power_cost: number | null
  components: DamageComponent[]
  procs: Proc[]
  effect_lines: string[]
  has_unparsed_damage: boolean
  source: 'spell' | 'aa' | string
  rank: number | null
  /** Self-target pulse with no duration ("Until Cancelled" — Exorcise):
   * kept up permanently; modeled as a continuous pulse stream in the
   * maintained section, never cast in rotation. */
  maintained?: boolean
  /** For buff-granted proc passives: the SUPPLIER's stat snapshot —
   * PotM's Precise Note hits with the bard's chain, not the player's.
   * Absent ⇒ the player's stats scale the proc (own AAs, own gear). */
  proc_stats?: Partial<SimStats>
}

/** One entry of a class's group/raid/ally buff book
 * (GET /api/simulator/class-buffs?cls=). */
export interface ClassBuff {
  name: string
  base_name: string
  tier_name: string
  level: number
  target_scope: 'group' | 'raid' | 'ally' | string
  icon_id: number | null
  icon_backdrop: number | null
  /** Modelable stat mods parsed from the effect text (BuffMods keys). */
  mods: Record<string, number>
  procs: Proc[]
  effect_lines: string[]
  /** Set ⇒ a temp buff the group member rotates (windows from
   * duration/recast); null/long ⇒ permanent while they're in group. */
  duration_s: number | null
  recast_s: number
  /** AA effect lines that adjusted this buff's reuse/duration (the
   * member's Enhance:/Focus: nodes) — already applied to the values. */
  aa_adjustments?: string[]
}

/** A specific group member's buff book at THEIR owned spell ranks
 * (GET /api/simulator/character-buffs?name=). */
export interface GroupMemberBook {
  character_name: string
  cls: string | null
  level: number | null
  buffs: ClassBuff[]
  /** The member's proc-scaling stat snapshot (SimStats-shaped keys) —
   * buff-granted proc damage scales off the SUPPLIER, not the player. */
  stats: Partial<SimStats>
}

export interface DerivedSource {
  kind: 'item' | 'set' | 'aa' | string
  name: string
  detail: string
}

/** Hidden modifiers auto-derived from worn gear + adorns + active set
 * bonuses + class-tree AAs — none appear in census sheet stats. */
export interface DerivedModifiers {
  base_damage_bonus_pct: number
  cast_speed_bonus_pct: number
  reuse_bonus_pct: number
  sources: DerivedSource[]
}

export interface CharacterRotationData {
  character_name: string
  cls: string | null
  level: number | null
  abilities: RotationAbility[]
  /** Always-on proc passives (AA innates + active set-bonus procs). */
  passives: RotationAbility[]
  derived: DerivedModifiers
}

/** The subset of CharacterStats the engine reads (all optional — a stat
 * the sheet lacks contributes zero). */
export interface SimStats {
  /** The class's primary attribute value (WIS priest / INT mage / AGI
   * scout / STR fighter), resolved by the caller — feeds the
   * level-capped primary-stat damage bonus. */
  primary_stat?: number | null
  /** WHICH attribute primary_stat is — lets buff windows apply only the
   * pertinent flat attribute add (AGI on a templar does nothing). */
  primary_attr?: 'str' | 'agi' | 'wis' | 'int'
  potency?: number | null
  fervor?: number | null
  /** Gear/AA "increases base damage" percentages (e.g. choker +25%,
   * Pact of the Faithful +10%) — additive with the primary-stat bonus.
   * Hidden from census sheets; user-entered. */
  base_damage_bonus_pct?: number | null
  crit_chance?: number | null
  crit_bonus?: number | null
  ability_mod?: number | null
  ability_doublecast?: number | null
  casting_speed?: number | null
  reuse_speed?: number | null
  recovery_speed?: number | null
  dps?: number | null
  attack_speed?: number | null
  double_attack?: number | null
  flurry?: number | null
  primary_min?: number | null
  primary_max?: number | null
  primary_delay?: number | null
  secondary_min?: number | null
  secondary_max?: number | null
  secondary_delay?: number | null
}

/** Stat deltas a buff applies while its window is active. All additive
 * onto the sheet stats except dmgPct, which multiplies ability damage. */
export interface BuffMods {
  castSpeedPct?: number
  reuseSpeedPct?: number
  recoverySpeedPct?: number
  dmgPct?: number
  critChancePct?: number
  critBonusPct?: number
  hastePct?: number
  dpsModPct?: number
  doubleAttackPct?: number
  abilityModFlat?: number
  potencyPct?: number
  fervorPct?: number
  /** Flat attribute adds — only the character's PRIMARY attribute does
   * anything (applyMods maps "<primary_attr>Flat" onto primary_stat). */
  strFlat?: number
  agiFlat?: number
  wisFlat?: number
  intFlat?: number
}

/** One active buff window on the timeline. Windows with the same buffId
 * never stack — overlap merges (two troubs' Jester's Cap = one cap). */
export interface BuffWindow {
  buffId: string
  start: number
  end: number
  mods: BuffMods
}

/** UI config: an external buff with how many providers rotate it. */
export interface ExternalBuffConfig {
  buffId: string
  providers: number
  /** Optional user overrides of the data-file timing (census-drifted). */
  duration_s?: number
  recast_s?: number
  /** The CHARACTER supplying this buff (raid-wide myth buffs come from
   * bards OUTSIDE the group) — their real rank/AA timing/procs/stats
   * replace the sheet's curated estimates. */
  supplier?: string
}

/** A raid-buff supplier resolution: the named character's owned rank of
 * the sheet buff (when found), plus their proc-scaling stats. */
export interface SuppliedBuffInfo {
  supplier: string
  status: 'loading' | 'failed' | 'no-spell' | 'ok'
  entry?: ClassBuff
  stats?: Partial<SimStats>
}

/** What the rotation is hitting. */
export interface SimTarget {
  /** Total mobs stacked on the target's position (≥ 1). */
  count: number
  /** True when the extra mobs are one linked encounter — 'encounter'-scope
   * components then hit all of them; otherwise they hit only the target. */
  encounter: boolean
  /** Condition strings toggled ON (e.g. "If target is undead"). Conditional
   * components whose condition is not listed here deal no damage. */
  activeConditions: string[]
}

export interface SimConfig {
  /** Priority-ordered ability names (base_name keys into `abilities`). */
  rotation: string[]
  abilities: Record<string, RotationAbility>
  stats: SimStats
  fightDurationS: number
  autoAttack: boolean
  /** Results-row label for the auto-attack stream ("Auto-attack (melee)"). */
  autoAttackLabel?: string
  /** Defaults to a lone unconditional boss dummy. */
  target?: SimTarget
  /** base_names whose DoT is HELD: the ability is not re-cast (even when
   * its recast is up) until the live application's final tick has landed,
   * so re-casting never clips ticks. Off = re-cast freely by priority. */
  dotRefreshHold?: string[]
  /** Precomputed external buff windows (see buffs.buildExternalWindows). */
  buffWindows?: BuffWindow[]
  /** Enabled always-on proc passives (their procs become damage streams).
   * Maintained toggles (Exorcise) ride along here as pulse streams. */
  passives?: RotationAbility[]
  /** Per-passive proc-chance corrections (keyed by passive NAME) — census
   * effect text can be stale vs live (Bolt of Power reads 50% at rank 10
   * but fires on every attack in the log). */
  procChanceOverrides?: Record<string, number>
  /** Hits per minute landing on whatever carries the character's
   * damage-shield buffs (Divine Light on the tank) — drives
   * 'when_damaged' proc streams. 0/absent = those procs contribute
   * nothing. */
  incomingHitsPerMinute?: number
  /** Curated mods for the character's OWN temp buffs, keyed by base_name.
   * Casting a beneficial ability opens a window at cast end for the
   * ability's duration; without an entry here the window has no mods
   * (visible on the timeline, zero effect). */
  selfBuffMods?: Record<string, BuffMods>
  /** Per-base_name damage calibration multipliers (default 1). */
  calibration?: Record<string, number>
}

export interface TimelineSegment {
  t: number
  dur: number
  ability: string // base_name; '' for idle
  /** Buff ids active at the cast start (cast segments only). */
  buffs?: string[]
}

export interface AbilityBreakdown {
  ability: string
  casts: number
  damage: number
  pct: number
  avgPerCast: number
  clippedDotTicks: number
  /** Display name when the row isn't keyed by a rotation ability
   * (passive proc streams). */
  label?: string
  /** True for passive proc rows: `casts` is the expected proc count. */
  isProc?: boolean
}

export interface SimResult {
  totalDamage: number
  dps: number
  perAbility: AbilityBreakdown[]
  autoAttackDamage: number
  timeline: TimelineSegment[]
  idlePct: number
  /** buffId → % of the fight the buff was up (externals + self temps). */
  buffUptimes: Record<string, number>
}
