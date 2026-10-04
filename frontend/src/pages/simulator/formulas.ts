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

/** Every component application carries a flat +½·B̄ — a CONSTANT
 * (half the component's census midpoint) added to BOTH tooltip ends,
 * so ranges widen by the BARE chain, not chain+½. Pinned by
 * Menludiir's Divine Smite VII: widths back-solve the bare chain and
 * the min end then reproduces his ability mod to ±0.03%. The AVERAGE
 * is unchanged versus the old per-end reading (B̄×(chain+½)), so the
 * engine's expected-damage math is identical — only displayed ranges
 * and tooltip-min predictions split the two. */
export const COMPONENT_BASE_FLAT_FRACTION = 0.5
/** LOW-LEVEL spells (T6 and below) carry NO +½: Velium Winds (59) and
 * Wrath of the Ancients (60) tooltip spreads sit on the BARE chain
 * (−4.5%) and nowhere near chain+½ (−18%), while level 67/70+ spells on
 * the same character (Plague, Rabies two-loadout ratio 0.860) demand
 * the ½. Spells at/above this level keep the ½. */
export const COMPONENT_FLAT_LEVEL_MIN = 61
/** Attack-proc payloads DEAL tooltip + AM×⅓ (log-fitted on Bolt of
 * Power and Blessed Armament dealt damage: means ≤1.1%, mins ≤0.3%).
 * Applied to single-target proc payloads only — maintained pulse
 * streams (aoe scope) verifiably carry none. */
export const PROC_AM_SHARE = 1 / 3
/** EQ2 membership Perks grant +20% beneficial spell duration — every
 * temp-buff window (own, group and raid) runs 1.2× its census length
 * (Badbang's Accelerated examine: 39.6s vs the 33.0s census value).
 * Toggleable in the Fight panel (default ON) since not every account
 * has perks active. Hostile effect durations (dots) are unaffected. */
export const PERK_BENEFICIAL_DURATION_MULT = 1.2
/** Crit multiplier = 1.3 + crit bonus (modern engine). */
export const CRIT_BASE_MULT = 1.3
/** USER-VERIFIED on Wuoshi (RoK TLE): Fervor applies, Crit Bonus does
 * NOT — the stat exists on sheets/buffs but has no effect. Flip this
 * when a later era enables it. */
export const CRIT_BONUS_ENABLED = false
export const effectiveCritBonus = (stats: SimStats) =>
  CRIT_BONUS_ENABLED ? (stats.crit_bonus ?? 0) : 0
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
/** Extra hits per flurry proc. LOG-FITTED to 1: a 10.7-minute pure-auto
 * session (178 base swings) showed 21.9% extra-hit lines vs the sheet's
 * DA 15.7 + flurry 6.9 — dead on MA + flurry×1 (22.6%) and 2.6σ below
 * flurry×2 (29.5%). */
export const FLURRY_EXTRA_HITS = 1
/** DoTs with no known duration assume this many seconds (flagged "est."). */
export const FALLBACK_DOT_DURATION_S = 12

const pct = (v: number | null | undefined, cap: number) => Math.min(Math.max(v ?? 0, 0), cap) / 100

/** Expected crit multiplier over many casts. */
export function critMultiplier(stats: SimStats): number {
  const chance = Math.min(Math.max(stats.crit_chance ?? 0, 0), 100) / 100
  const bonus = effectiveCritBonus(stats) / 100
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

/** Ability-aware dealt multiplier: per-ability Enhance crit bonus
 * ("Improves the Crit Bonus by 5%") raises THAT ability's expected crit. */
export const abilityDamageMultiplierFor = (stats: SimStats, a: RotationAbility) => {
  const extra = Math.max(a.crit_bonus_pct ?? 0, 0)
  return extra > 0
    ? critMultiplier({ ...stats, crit_bonus: (stats.crit_bonus ?? 0) + extra }) *
        fervorMultiplier(stats) *
        doublecastMultiplier(stats)
    : abilityDamageMultiplier(stats)
}

/** "Increases overtime damage by N%" — multiplies DOT components only. */
export const abilityDotDmgMod = (a: RotationAbility) => 1 + Math.max(a.dot_dmg_mod_pct ?? 0, 0) / 100

/** Effective cast time under casting speed. */
export const effCastTime = (castSecs: number, stats: SimStats) =>
  castSecs / (1 + pct(stats.casting_speed, CAST_SPEED_CAP))

/** Effective recast under reuse speed. */
export const effRecast = (recastSecs: number, stats: SimStats) =>
  recastSecs / (1 + pct(stats.reuse_speed, REUSE_SPEED_CAP))

/** Effective recovery under recovery speed. */
export const effRecovery = (recoverySecs: number, stats: SimStats) =>
  recoverySecs / (1 + pct(stats.recovery_speed, RECOVERY_SPEED_CAP))

/** Ability-aware timings: global speed PLUS any scoped worn-item cut
 * matching the ability's polarity ("Reduces reuse time of hostile
 * spells by 1 percent" speeds hostile recasts only; the beneficial
 * variant only the temp-buff casts). Treated as speed-equivalent
 * (divisor-additive) like the set bonuses.
 *
 * HARD FLOOR (user-verified): cast and reuse never drop below HALF the
 * ORIGINAL base — AA second-cuts count toward the same cap, so a 5s
 * cast with a −1s AA at +100% cast speed still lands on 2.5s. */
export const effCastTimeFor = (a: RotationAbility, stats: SimStats) =>
  Math.max(
    (a.orig_cast_secs ?? a.cast_secs) / 2,
    a.cast_secs /
      (1 + pct((stats.casting_speed ?? 0) + ((a.beneficial ? stats.beneficial_cast_pct : stats.hostile_cast_pct) ?? 0), CAST_SPEED_CAP)),
  )

export const effRecastFor = (a: RotationAbility, stats: SimStats) =>
  Math.max(
    (a.orig_recast_secs ?? a.recast_secs) / 2,
    a.recast_secs /
      (1 + pct((stats.reuse_speed ?? 0) + ((a.beneficial ? stats.beneficial_reuse_pct : stats.hostile_reuse_pct) ?? 0), REUSE_SPEED_CAP)),
  )

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
/** Encounter AEs carry AM×⅓ (Wrath of the Ancients' two ends give a
 * constant flat ≈ AM×0.36; AM/2 misses by 10% — and ⅓ matches the proc
 * share). Blue self-AoEs carry none (Exorcise, two loadouts). */
export const AM_SHARE_ENCOUNTER = 1 / 3
export const AM_SHARE_AOE = 0.0

export function abilityModShare(comp: DamageComponent | null): number {
  if (!comp) return 0
  if (comp.target_scope === 'encounter') return AM_SHARE_ENCOUNTER
  if (comp.target_scope === 'aoe') return AM_SHARE_AOE
  return 1
}

/** Flat fraction by SPELL LEVEL: ½ at 61+, none for T6-and-below
 * (see COMPONENT_FLAT_LEVEL_MIN). Scope-independent. */
export function componentFlatFraction(spellLevel: number): number {
  if (spellLevel > 0 && spellLevel < COMPONENT_FLAT_LEVEL_MIN) return 0
  return COMPONENT_BASE_FLAT_FRACTION
}

/** What one PROC payload hit deals (before crit/fervor): payload avg ×
 * (coefficient + flat) + AM×⅓ for single-target payloads. Log-validated
 * on Bolt of Power and Blessed Armament; the AM×⅓ confirmed again by
 * Rabies II across a gear swap. */
export function procHitDamage(components: DamageComponent[], stats: SimStats, level: number): number {
  if (!components.length) return 0
  const per = damageCoefficient(stats, level) + componentFlatFraction(level)
  const singleTarget = components.every(c => !c.target_scope || c.target_scope === 'single')
  const amShare = singleTarget ? PROC_AM_SHARE : 0
  return componentsAvg(components) * per + flatDamageMod(stats, components[0]?.school) * amShare
}

/** Lifeburn's per-HP components: per application = rate × fraction ×
 * caster max health — FLAT, outside the coefficient chain and Enhance
 * (the in-game 9/HP is static across gear; the ~25%-of-pool burn per
 * application is user-observed, pending a log). The in-game cap "based
 * off the target's maximum health" is not modeled. */
export const isPerHp = (c: DamageComponent) => (c.per_hp_rate ?? 0) > 0
export const perHpDamage = (c: DamageComponent, stats: SimStats) =>
  Math.max(c.per_hp_rate ?? 0, 0) * Math.max(c.hp_fraction ?? 0, 0) * Math.max(stats.max_health ?? 0, 0)

/** The flat add on a primary-hit application: ability mod plus any
 * school-matched "damage done by spells" gear flat (Spooky Bone Hoop —
 * +30 disease reaches Soulrot/Lifeburn, nothing on a cold spell, and a
 * character with no spells of the school gets nothing at all).
 * Validated: Lifeburn's hit − tick = sheet AM 627 + 30 exactly at both
 * tooltip ends. On procs it's assumed to share the AM ⅓ (unvalidated). */
export const flatDamageMod = (stats: SimStats, school: string | null | undefined) =>
  Math.max(stats.ability_mod ?? 0, 0) +
  Math.max(stats.school_damage_flat?.[(school ?? '').toLowerCase()] ?? 0, 0)

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
  // Enhance multiplies the BASE-CHAIN part only; the flat ability-mod/
  // school-flat part and proc payloads sit OUTSIDE it (Soulrot VII +
  // Lifeburn cross-validated via the Spooky Bone Hoop's +30 disease).
  let chainPart = 0
  let flatPart = 0
  for (const c of ability.components) {
    const mult = componentTargetMultiplier(c, target)
    if (mult <= 0) continue
    const ticks = c.kind === 'dot' ? dotTicks(c) : 1
    if (isPerHp(c)) {
      flatPart += perHpDamage(c, stats) * ticks * mult
      continue
    }
    const avg = (c.min_dmg + c.max_dmg) / 2
    const dotMod = c.kind === 'dot' ? abilityDotDmgMod(ability) : 1
    const half = c.no_flat_mod ? 0 : componentFlatFraction(ability.level)
    chainPart += avg * (coeff + half) * ticks * mult * dotMod
    if (c === primary && !c.no_flat_mod) flatPart += flatDamageMod(stats, c.school) * abilityModShare(c) * mult
  }
  // Trigger-budget procs carried BY the ability (Slothful Spirit grants
  // exactly N Sloth's Habitat hits per application) — a separate spell's
  // damage, so the carrying ability's Enhance doesn't scale it.
  for (const p of ability.procs ?? []) {
    if (p.trigger_count && p.trigger_count > 0) {
      flatPart += p.trigger_count * (p.chance_pct / 100) * procHitDamage(p.components, stats, ability.level)
    }
  }
  return (chainPart * abilityDmgMod(ability) + flatPart) * abilityDamageMultiplierFor(stats, ability) * calibration
}

/** Per-ability Enhance multiplier — applies to the BASE-CHAIN part only,
 * never the flat ability-mod/school-flat part: with the hoop's +30
 * disease flat known, Soulrot VII's hit back-solves to chain×1.05 +
 * (627 + 30) exactly; whole-tooltip ×1.05 was the coincidence
 * 627×1.05 ≈ 657. */
export const abilityDmgMod = (a: RotationAbility) => 1 + Math.max(a.dmg_mod_pct ?? 0, 0) / 100


/** Predicted MINIMUM of the primary hit's TOOLTIP range — directly
 * comparable to the low end the user reads in game (tooltips exclude
 * crit, fervor and doublecast): B_min × (coefficient + ½) + ability mod.
 * The max derives from it via the base ratio, so min carries all the
 * calibration signal. */
export function predictedTooltipMin(ability: RotationAbility, stats: SimStats): number | null {
  const primary = primaryComponent(ability)
  if (!primary || primary.kind !== 'hit') return null
  if (primary.min_dmg <= 0) return null
  // The ½ flat is a CONSTANT ½×B̄ (midpoint), not ½×B_min — tooltip
  // widths scale by the bare chain (Divine Smite VII width-validated).
  // Auto-scaled class ranks (Wrath) are the BARE chain: no ½, no mod.
  const mid = (primary.min_dmg + primary.max_dmg) / 2
  const half = primary.no_flat_mod ? 0 : componentFlatFraction(ability.level)
  const flat = primary.no_flat_mod ? 0 : flatDamageMod(stats, primary.school) * abilityModShare(primary)
  return (
    (primary.min_dmg * damageCoefficient(stats, ability.level) + half * mid) * abilityDmgMod(ability) + flat
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
  const critMult = CRIT_BASE_MULT + effectiveCritBonus(stats) / 100
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
