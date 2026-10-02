// Stat math for the rotation simulator — the TOOLTIP-VALIDATED TLE model
// (fitted on Divine Smite VII / Warring Deities V / Smite Corruption I
// tooltips, then blind-verified on Divine Strike VII to 0.15%):
//
//   coefficient = (1 + primaryStatBonus + gear/AA base-damage%, ADDITIVE
//                  in one bucket) × (1 + potency)
//   per component application = B̄ × (coefficient + ½)
//   the PRIMARY unconditional hit additionally gets the full ability mod
//   (uncapped); DoT ticks and conditional riders get none.
//   Crit/fervor/doublecast multiply DEALT damage only (tooltips exclude
//   them). Cast/recast are divisive: base ÷ (1 + speed/100).
//
// Scope refinements (blind-fitted on Divine Demonstration's two tooltip
// ends + Exorcise across two stat loadouts): encounter components use a
// reduced flat fraction (~0.1) and HALF the ability mod; self-pulse blue
// AoEs keep the ½ but carry NO mod. Proc payloads (log-validated on Bolt
// of Power + Blessed Armament to ≤1%) deal tooltip + AM/3.
// Every constant is exported and tunable; calibration absorbs residual
// error. Kept free of React (aaplanner convention).

import type { DamageComponent, RotationAbility, SimStats, SimTarget } from './types'

/** A lone unconditional boss dummy — the default sim target. */
export const DEFAULT_TARGET: SimTarget = { count: 1, encounter: false, activeConditions: [] }

/** Every component application carries a flat +½·B̄ on top of the
 * coefficient-scaled base (fitted to <0.2% on hit tooltips; ticks are
 * ambiguous at ±8% on tiny values — uniform rule kept for one-rule
 * simplicity). */
export const COMPONENT_BASE_FLAT_FRACTION = 0.5
/** Encounter (green AE) components fit a much smaller flat fraction:
 * Divine Demonstration's two tooltip ends give slope = coeff + 0.10
 * (chain+½ overshoots the spread by 9%). Provisional — one ability's
 * fit; Blaze of Faith still runs +4% over under this rule. */
export const ENCOUNTER_BASE_FLAT_FRACTION = 0.1
/** Attack-proc payloads DEAL tooltip + AM×⅓ (log-fitted on Bolt of
 * Power and Blessed Armament dealt damage: means ≤1.1%, mins ≤0.3%).
 * Applied to single-target proc payloads only — maintained pulse
 * streams (aoe scope) verifiably carry none. */
export const PROC_AM_SHARE = 1 / 3
/** Crit multiplier = 1.3 + crit bonus (modern engine). */
export const CRIT_BASE_MULT = 1.3
export const CAST_SPEED_CAP = 100
export const REUSE_SPEED_CAP = 100
export const RECOVERY_SPEED_CAP = 100
/** Haste & DPS-mod diminishing returns (community-documented anchors:
 * linear to 100, 200 stat = 125% actual, hard cap): the intermediate
 * curve is modeled linear at quarter rate — both anchors exact. */
export const SPEED_STAT_DR_START = 100
export const SPEED_STAT_SOFT_CAP = 200
export const SPEED_STAT_MAX_EFFECT = 125
/** Multi Attack is LINEAR (237 MA = 2 guaranteed extra swings + 37%
 * chance of a third), soft-capped at 600. */
export const MULTI_ATTACK_CAP = 600
export const FLURRY_CAP = 100
/** A flurry proc strikes the target 2-4 times (community description) —
 * modeled as this many EXTRA hits per proc. Tunable. */
export const FLURRY_EXTRA_HITS = 2
/** DoTs with no known duration assume this many seconds (flagged "est."). */
export const FALLBACK_DOT_DURATION_S = 12

const pct = (v: number | null | undefined, cap: number) => Math.min(Math.max(v ?? 0, 0), cap) / 100

/** Expected crit multiplier over many casts. */
export function critMultiplier(stats: SimStats): number {
  const chance = Math.min(Math.max(stats.crit_chance ?? 0, 0), 100) / 100
  const bonus = (stats.crit_bonus ?? 0) / 100
  return 1 + chance * (CRIT_BASE_MULT - 1 + bonus)
}

/** B × (1 + Potency): the potency-scaled base every ability starts from. */
export const potencyMultiplier = (stats: SimStats) => 1 + Math.max(stats.potency ?? 0, 0) / 100

/** Primary-stat soft cap for a spell of level L: C = 15·L + 20. */
export const PRIMARY_STAT_CAP_PER_LEVEL = 15
export const PRIMARY_STAT_CAP_BASE = 20
/** The flat bonus plateau between the level cap and 1200. */
export const PRIMARY_STAT_MIDBAND_BONUS = 0.65
export const PRIMARY_STAT_LOG_THRESHOLD = 1200

/** Primary-stat damage bonus (fraction) for a spell of level L:
 *    S <  C(L)  → linear ramp to the plateau (the sub-cap curve f(S,C)
 *                 isn't documented; raid characters sit far above it)
 *    C ≤ S<1200 → 0.65
 *    S ≥ 1200   → 0.28·log₂(S) − 2.2
 */
export function primaryStatBonus(stat: number | null | undefined, spellLevel: number): number {
  const s = Math.max(stat ?? 0, 0)
  if (s <= 0) return 0
  const cap = PRIMARY_STAT_CAP_PER_LEVEL * Math.max(spellLevel, 1) + PRIMARY_STAT_CAP_BASE
  if (s < cap) return PRIMARY_STAT_MIDBAND_BONUS * (s / cap)
  if (s < PRIMARY_STAT_LOG_THRESHOLD) return PRIMARY_STAT_MIDBAND_BONUS
  return 0.28 * Math.log2(s) - 2.2
}

/** Fervor multiplies the final ability damage (autos excluded). */
export const fervorMultiplier = (stats: SimStats) => 1 + Math.max(stats.fervor ?? 0, 0) / 100

/** Ability doublecast: expected extra casts as a damage multiplier. */
export const doublecastMultiplier = (stats: SimStats) =>
  1 + Math.min(Math.max(stats.ability_doublecast ?? 0, 0), 100) / 100

/** The combined post-mod multiplier on every ability hit: expected crit ×
 * fervor × doublecast. Potency is NOT here — it scales the base before
 * the ability-mod cap. */
export const abilityDamageMultiplier = (stats: SimStats) =>
  critMultiplier(stats) * fervorMultiplier(stats) * doublecastMultiplier(stats)

/** Effective cast time under casting speed. */
export const effCastTime = (castSecs: number, stats: SimStats) =>
  castSecs / (1 + pct(stats.casting_speed, CAST_SPEED_CAP))

/** Effective recast under reuse speed. */
export const effRecast = (recastSecs: number, stats: SimStats) =>
  recastSecs / (1 + pct(stats.reuse_speed, REUSE_SPEED_CAP))

/** Effective recovery under recovery speed. */
export const effRecovery = (recoverySecs: number, stats: SimStats) =>
  recoverySecs / (1 + pct(stats.recovery_speed, RECOVERY_SPEED_CAP))

/** Number of DoT ticks over its (possibly estimated) duration. */
export function dotTicks(comp: DamageComponent): number {
  const interval = comp.interval_s ?? 1
  const duration = comp.duration_s ?? FALLBACK_DOT_DURATION_S
  if (interval <= 0) return 0
  return Math.floor(duration / interval)
}

/** How many times a component lands per application against this target:
 * 0 when its condition isn't toggled on; the target count for AoE; the
 * target count for encounter scope when the mobs are linked (else 1). */
export function componentTargetMultiplier(comp: DamageComponent, target: SimTarget): number {
  if (comp.condition && !target.activeConditions.includes(comp.condition)) return 0
  const count = Math.max(1, Math.floor(target.count))
  if (comp.target_scope === 'aoe') return count
  if (comp.target_scope === 'encounter') return target.encounter ? count : 1
  return 1
}

/** Sum of SINGLE-target base average damage across the ability's ACTIVE
 * components (a DoT counts all its ticks) — the base the ability-mod cap
 * applies to. Target count deliberately excluded: the mod applies per
 * hit on each target, so multi-target scaling happens after the cap. */
export function abilityBaseAvg(ability: RotationAbility, target: SimTarget = DEFAULT_TARGET): number {
  let total = 0
  for (const c of ability.components) {
    if (componentTargetMultiplier(c, target) <= 0) continue
    const avg = (c.min_dmg + c.max_dmg) / 2
    total += c.kind === 'dot' ? avg * dotTicks(c) : avg
  }
  return total
}

/** The validated multiplier chain on raw base damage:
 * (1 + primaryStatBonus + gear/AA base-damage% — one ADDITIVE bucket)
 * × (1 + potency). */
export function damageCoefficient(stats: SimStats, spellLevel: number): number {
  return (
    (1 +
      primaryStatBonus(stats.primary_stat, spellLevel) +
      Math.max(stats.base_damage_bonus_pct ?? 0, 0) / 100) *
    potencyMultiplier(stats)
  )
}

/** Ability-mod share by primary-component target scope (blind-validated):
 * single-target abilities carry the FULL mod (Divine Strike ±0.15%),
 * encounter AEs carry HALF (Divine Demonstration fit c ≈ AM/2), and
 * self-pulse blue AoEs carry NONE (Exorcise across two stat loadouts —
 * its small residual flat tracks the stat chain, not AM, and is left to
 * the per-ability calibration factor). */
export const AM_SHARE_ENCOUNTER = 0.5
export const AM_SHARE_AOE = 0.0

export function abilityModShare(comp: DamageComponent | null): number {
  if (!comp) return 0
  if (comp.target_scope === 'encounter') return AM_SHARE_ENCOUNTER
  if (comp.target_scope === 'aoe') return AM_SHARE_AOE
  return 1
}

/** Flat fraction by component scope: single/aoe carry the validated ½;
 * encounter fits ~0.1 (see ENCOUNTER_BASE_FLAT_FRACTION). */
export function componentFlatFraction(comp: DamageComponent): number {
  return comp.target_scope === 'encounter' ? ENCOUNTER_BASE_FLAT_FRACTION : COMPONENT_BASE_FLAT_FRACTION
}

/** What one PROC payload hit deals (before crit/fervor): payload avg ×
 * (coefficient + ½) + AM×⅓ for single-target payloads. Log-validated on
 * Bolt of Power and Blessed Armament. */
export function procHitDamage(components: DamageComponent[], stats: SimStats, level: number): number {
  if (!components.length) return 0
  const per = damageCoefficient(stats, level) + COMPONENT_BASE_FLAT_FRACTION
  const singleTarget = components.every(c => !c.target_scope || c.target_scope === 'single')
  const amShare = singleTarget ? PROC_AM_SHARE : 0
  return componentsAvg(components) * per + Math.max(stats.ability_mod ?? 0, 0) * amShare
}

/** The primary component: the first unconditional 'hit' (else the first
 * unconditional component) — the one that carries the ability mod. */
export function primaryComponent(ability: RotationAbility): DamageComponent | null {
  return (
    ability.components.find(c => c.kind === 'hit' && !c.condition) ??
    ability.components.find(c => !c.condition) ??
    null
  )
}

/** Expected damage of ONE cast of the ability under the given stats:
 * per component, B̄ × (coefficient + ½) × target multiplier (× ticks for
 * DoTs), the ability mod ONCE on the primary component, all times
 * expected crit × fervor × doublecast and the calibration factor. */
export function expectedCastDamage(
  ability: RotationAbility,
  stats: SimStats,
  calibration = 1,
  target: SimTarget = DEFAULT_TARGET,
): number {
  const coeff = damageCoefficient(stats, ability.level)
  const primary = primaryComponent(ability)
  let total = 0
  for (const c of ability.components) {
    const mult = componentTargetMultiplier(c, target)
    if (mult <= 0) continue
    const avg = (c.min_dmg + c.max_dmg) / 2
    total += avg * (coeff + componentFlatFraction(c)) * (c.kind === 'dot' ? dotTicks(c) : 1) * mult
    if (c === primary) total += Math.max(stats.ability_mod ?? 0, 0) * abilityModShare(c) * mult
  }
  return total * abilityDamageMultiplier(stats) * calibration
}

/** Predicted MINIMUM of the primary hit's TOOLTIP range — directly
 * comparable to the low end the user reads in game (tooltips exclude
 * crit, fervor and doublecast): B_min × (coefficient + ½) + ability mod.
 * The max derives from it via the base ratio, so min carries all the
 * calibration signal. */
export function predictedTooltipMin(ability: RotationAbility, stats: SimStats): number | null {
  const primary = primaryComponent(ability)
  if (!primary || primary.kind !== 'hit') return null
  if (primary.min_dmg <= 0) return null
  return (
    primary.min_dmg * (damageCoefficient(stats, ability.level) + componentFlatFraction(primary)) +
    Math.max(stats.ability_mod ?? 0, 0) * abilityModShare(primary)
  )
}

/** Average damage of a component list — a DoT counts all its ticks.
 * Conditions/targets ignored (used for proc payloads, which are
 * unconditional single-target hits). */
export function componentsAvg(components: DamageComponent[]): number {
  let total = 0
  for (const c of components) {
    const avg = (c.min_dmg + c.max_dmg) / 2
    total += c.kind === 'dot' ? avg * dotTicks(c) : avg
  }
  return total
}

/** Auto-attack hits per second across both weapons — haste (DR curve)
 * shortens delay; multi attack + flurry add extra hits (each proc-able). */
export function autoSwingRate(stats: SimStats): number {
  const haste = 1 + speedStatEffective(stats.attack_speed) / 100
  const swingsPer = extraSwingFactor(stats)
  let rate = 0
  for (const [min, max, delay] of [
    [stats.primary_min, stats.primary_max, stats.primary_delay],
    [stats.secondary_min, stats.secondary_max, stats.secondary_delay],
  ] as const) {
    if (min == null || max == null || delay == null || delay <= 0) continue
    rate += swingsPer / (delay / haste)
  }
  return rate
}

/** BASE swing rate — no multi-attack/flurry extras. This is the PROC
 * trigger pool: log-validated (pure-wand session: 190 procs on exactly
 * 190 base swings out of 304 total auto hits — MA extras never proc). */
export function baseAutoSwingRate(stats: SimStats): number {
  const haste = 1 + speedStatEffective(stats.attack_speed) / 100
  let rate = 0
  for (const [min, max, delay] of [
    [stats.primary_min, stats.primary_max, stats.primary_delay],
    [stats.secondary_min, stats.secondary_max, stats.secondary_delay],
  ] as const) {
    if (min == null || max == null || delay == null || delay <= 0) continue
    rate += 1 / (delay / haste)
  }
  return rate
}

/** Haste / DPS-mod stat → ACTUAL percent effect (diminishing returns). */
export function speedStatEffective(stat: number | null | undefined): number {
  const t = Math.max(stat ?? 0, 0)
  if (t <= SPEED_STAT_DR_START) return t
  if (t >= SPEED_STAT_SOFT_CAP) return SPEED_STAT_MAX_EFFECT
  return (
    SPEED_STAT_DR_START +
    ((t - SPEED_STAT_DR_START) * (SPEED_STAT_MAX_EFFECT - SPEED_STAT_DR_START)) /
      (SPEED_STAT_SOFT_CAP - SPEED_STAT_DR_START)
  )
}

/** Extra hits per main swing from Multi Attack + Flurry (both are
 * per-swing chances for additional strikes; MA linear to 600). */
export function extraSwingFactor(stats: SimStats): number {
  const ma = Math.min(Math.max(stats.double_attack ?? 0, 0), MULTI_ATTACK_CAP) / 100
  const flurry = (Math.min(Math.max(stats.flurry ?? 0, 0), FLURRY_CAP) / 100) * FLURRY_EXTRA_HITS
  return 1 + ma + flurry
}

/** Expected value of a CRIT swing for a uniform roll on [min, max]:
 * crit = max(critMult × roll, max+1) — the max-hit+1 floor, measured on
 * live logs (75% of observed crits sat exactly at the floor). */
export function expectedCritSwing(min: number, max: number, critMult: number): number {
  const floor = max + 1
  if (critMult <= 0) return floor
  const t = Math.min(Math.max(floor / critMult, min), max) // roll where critMult×roll clears the floor
  const span = max - min
  if (span <= 0) return Math.max(critMult * max, floor)
  const belowShare = (t - min) / span
  const aboveAvg = (critMult * (t + max)) / 2
  return belowShare * floor + (1 - belowShare) * aboveAvg
}

/** Continuous auto-attack DPS from the weapon lines (0 when no weapon).
 *
 * VALIDATED MODEL (Menludiir wand session, 184 swings): the census
 * sheet's weapon min/max/delay are already fully cooked — the game folds
 * STR/DPS-stat scaling in — so the roll is uniform[min, max] with NO
 * further sheet multipliers. Crits use the max+1 floor. `dps` and
 * `attack_speed` in stats are treated as BUFF-WINDOW DELTAS only (the
 * caller zeroes the sheet values). */
export function autoAttackDps(stats: SimStats): number {
  const chance = Math.min(Math.max(stats.crit_chance ?? 0, 0), 100) / 100
  const critMult = CRIT_BASE_MULT + (stats.crit_bonus ?? 0) / 100
  const dpsMod = 1 + speedStatEffective(stats.dps) / 100
  const haste = 1 + speedStatEffective(stats.attack_speed) / 100
  const swingsPer = extraSwingFactor(stats)
  let total = 0
  for (const [min, max, delay] of [
    [stats.primary_min, stats.primary_max, stats.primary_delay],
    [stats.secondary_min, stats.secondary_max, stats.secondary_delay],
  ] as const) {
    if (min == null || max == null || delay == null || delay <= 0) continue
    const avg = (min + max) / 2
    const swing = (1 - chance) * avg + chance * expectedCritSwing(min, max, critMult)
    total += (swing * dpsMod * swingsPer) / (delay / haste)
  }
  return total
}
