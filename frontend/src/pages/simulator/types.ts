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
  effect_lines: string[]
  has_unparsed_damage: boolean
}

export interface CharacterRotationData {
  character_name: string
  cls: string | null
  level: number | null
  abilities: RotationAbility[]
}

/** The subset of CharacterStats the engine reads (all optional — a stat
 * the sheet lacks contributes zero). */
export interface SimStats {
  crit_chance?: number | null
  crit_bonus?: number | null
  ability_mod?: number | null
  casting_speed?: number | null
  reuse_speed?: number | null
  recovery_speed?: number | null
  dps?: number | null
  attack_speed?: number | null
  double_attack?: number | null
  primary_min?: number | null
  primary_max?: number | null
  primary_delay?: number | null
  secondary_min?: number | null
  secondary_max?: number | null
  secondary_delay?: number | null
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
  /** Defaults to a lone unconditional boss dummy. */
  target?: SimTarget
  /** base_names whose DoT is HELD: the ability is not re-cast (even when
   * its recast is up) until the live application's final tick has landed,
   * so re-casting never clips ticks. Off = re-cast freely by priority. */
  dotRefreshHold?: string[]
  /** Per-base_name damage calibration multipliers (default 1). */
  calibration?: Record<string, number>
}

export interface TimelineSegment {
  t: number
  dur: number
  ability: string // base_name; '' for idle
}

export interface AbilityBreakdown {
  ability: string
  casts: number
  damage: number
  pct: number
  avgPerCast: number
  clippedDotTicks: number
}

export interface SimResult {
  totalDamage: number
  dps: number
  perAbility: AbilityBreakdown[]
  autoAttackDamage: number
  timeline: TimelineSegment[]
  idlePct: number
}
