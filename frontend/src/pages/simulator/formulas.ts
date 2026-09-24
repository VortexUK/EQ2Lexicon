// Stat math for the rotation simulator — RoK-era (T8) model. Every
// constant is exported and tunable; the calibration layer (user-entered
// in-game datapoints) absorbs model error, so these are deliberately
// simple, documented rules rather than an attempt at exact mechanics.
// Kept free of React so it unit-tests directly (aaplanner engine.ts
// convention).

import type { DamageComponent, RotationAbility, SimStats, SimTarget } from './types'

/** A lone unconditional boss dummy — the default sim target. */
export const DEFAULT_TARGET: SimTarget = { count: 1, encounter: false, activeConditions: [] }

/** EQ2 rule of thumb: ability mod contributes at most 50% of base damage. */
export const ABILITY_MOD_CAP_FRACTION = 0.5
/** RoK-era crit multiplier ≈ 1.3× before crit bonus. */
export const CRIT_BASE_MULT = 1.3
/** Potency doesn't exist in the RoK era (introduced in Sentinel's Fate). */
export const POTENCY_ENABLED = false
export const CAST_SPEED_CAP = 100
export const REUSE_SPEED_CAP = 100
export const RECOVERY_SPEED_CAP = 100
export const HASTE_CAP = 100
export const DA_CAP = 100
/** DoTs with no known duration assume this many seconds (flagged "est."). */
export const FALLBACK_DOT_DURATION_S = 12

const pct = (v: number | null | undefined, cap: number) => Math.min(Math.max(v ?? 0, 0), cap) / 100

/** Expected crit multiplier over many casts. */
export function critMultiplier(stats: SimStats): number {
  const chance = Math.min(Math.max(stats.crit_chance ?? 0, 0), 100) / 100
  const bonus = (stats.crit_bonus ?? 0) / 100
  return 1 + chance * (CRIT_BASE_MULT - 1 + bonus)
}

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

/** Expected damage of ONE cast of the ability under the given stats:
 * per component, (base share + capped mod share) × target multiplier,
 * all times the expected crit multiplier and calibration factor. */
export function expectedCastDamage(
  ability: RotationAbility,
  stats: SimStats,
  calibration = 1,
  target: SimTarget = DEFAULT_TARGET,
): number {
  const base = abilityBaseAvg(ability, target)
  if (base <= 0) return 0
  const mod = Math.min(Math.max(stats.ability_mod ?? 0, 0), base * ABILITY_MOD_CAP_FRACTION)
  const scale = ((base + mod) / base) * critMultiplier(stats) * calibration
  let total = 0
  for (const c of ability.components) {
    const mult = componentTargetMultiplier(c, target)
    if (mult <= 0) continue
    const avg = (c.min_dmg + c.max_dmg) / 2
    total += (c.kind === 'dot' ? avg * dotTicks(c) : avg) * scale * mult
  }
  return total
}

/** Predicted average NON-crit hit for the ability's first UNCONDITIONAL
 * 'hit' component on the main target — what the user compares an in-game
 * observed hit against (calibration). */
export function predictedNonCritHit(ability: RotationAbility, stats: SimStats): number | null {
  const hit = ability.components.find(c => c.kind === 'hit' && !c.condition)
  if (!hit) return null
  const base = abilityBaseAvg(ability)
  if (base <= 0) return null
  const mod = Math.min(Math.max(stats.ability_mod ?? 0, 0), base * ABILITY_MOD_CAP_FRACTION)
  const hitAvg = (hit.min_dmg + hit.max_dmg) / 2
  // The ability mod is distributed across components by base share.
  return hitAvg + mod * (hitAvg / base)
}

/** Continuous auto-attack DPS from the weapon lines (0 when no weapon). */
export function autoAttackDps(stats: SimStats): number {
  const crit = critMultiplier(stats)
  const dpsMod = 1 + (stats.dps ?? 0) / 100
  const da = 1 + pct(stats.double_attack, DA_CAP)
  const haste = 1 + pct(stats.attack_speed, HASTE_CAP)
  let total = 0
  for (const [min, max, delay] of [
    [stats.primary_min, stats.primary_max, stats.primary_delay],
    [stats.secondary_min, stats.secondary_max, stats.secondary_delay],
  ] as const) {
    if (min == null || max == null || delay == null || delay <= 0) continue
    const swingAvg = (min + max) / 2
    total += (swingAvg * dpsMod * crit * da) / (delay / haste)
  }
  return total
}
