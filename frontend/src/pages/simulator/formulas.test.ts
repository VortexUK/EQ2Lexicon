/**
 * Simulator formula tests — hand-computed expectations for the stat math
 * the engine builds on: crit multiplier, speed-modified timings, DoT
 * ticks, ability base damage, capped ability mod, and auto-attack DPS.
 */
import { describe, expect, it } from 'vitest'

import {
  ABILITY_MOD_CAP_FRACTION,
  DEFAULT_TARGET,
  FALLBACK_DOT_DURATION_S,
  abilityBaseAvg,
  autoAttackDps,
  componentTargetMultiplier,
  critMultiplier,
  dotTicks,
  effCastTime,
  effRecast,
  effRecovery,
  expectedCastDamage,
  predictedNonCritHit,
} from './formulas'
import type { DamageComponent, RotationAbility, SimStats, SimTarget } from './types'

const hit = (min: number, max: number, over: Partial<DamageComponent> = {}): DamageComponent => ({
  kind: 'hit',
  min_dmg: min,
  max_dmg: max,
  school: 'heat',
  target_scope: 'single',
  condition: null,
  interval_s: null,
  duration_s: null,
  duration_estimated: false,
  suspect_low_value: false,
  ...over,
})

const dot = (min: number, max: number, interval: number, duration: number | null): DamageComponent =>
  hit(min, max, { kind: 'dot', interval_s: interval, duration_s: duration })

const ability = (components: DamageComponent[], over: Partial<RotationAbility> = {}): RotationAbility => ({
  name: 'Test Nuke IV',
  base_name: 'Test Nuke',
  crc: 1,
  tier_name: 'Master',
  level: 70,
  spell_type: 'spells',
  beneficial: false,
  cast_secs: 2,
  recast_secs: 8,
  recovery_secs: 0.5,
  target_type: 'other',
  icon_id: null,
  icon_backdrop: null,
  duration_s: null,
  power_cost: null,
  components,
  effect_lines: [],
  has_unparsed_damage: false,
  ...over,
})

describe('critMultiplier', () => {
  it('is 1 with no crit chance', () => {
    expect(critMultiplier({})).toBe(1)
    expect(critMultiplier({ crit_chance: 0, crit_bonus: 50 })).toBe(1)
  })

  it('blends the 1.3x base crit by chance', () => {
    // 50% chance, no bonus: 1 + 0.5 * 0.3 = 1.15
    expect(critMultiplier({ crit_chance: 50 })).toBeCloseTo(1.15)
  })

  it('adds crit bonus on top of the base multiplier', () => {
    // 100% chance, 20% bonus: 1 + 1.0 * (0.3 + 0.2) = 1.5
    expect(critMultiplier({ crit_chance: 100, crit_bonus: 20 })).toBeCloseTo(1.5)
  })

  it('clamps chance to 100 and floors negatives at 0', () => {
    expect(critMultiplier({ crit_chance: 250 })).toBeCloseTo(1.3)
    expect(critMultiplier({ crit_chance: -10 })).toBe(1)
  })
})

describe('speed-modified timings', () => {
  const stats: SimStats = { casting_speed: 50, reuse_speed: 25, recovery_speed: 100 }

  it('divides by 1 + stat/100', () => {
    expect(effCastTime(3, stats)).toBeCloseTo(2) // 3 / 1.5
    expect(effRecast(10, stats)).toBeCloseTo(8) // 10 / 1.25
    expect(effRecovery(0.5, stats)).toBeCloseTo(0.25) // 0.5 / 2
  })

  it('caps each speed at 100', () => {
    expect(effCastTime(3, { casting_speed: 400 })).toBeCloseTo(1.5)
  })

  it('is identity with no stats', () => {
    expect(effCastTime(2, {})).toBe(2)
    expect(effRecast(8, {})).toBe(8)
  })
})

describe('dotTicks', () => {
  it('floors duration / interval', () => {
    expect(dotTicks(dot(10, 10, 2, 10))).toBe(5)
    expect(dotTicks(dot(10, 10, 3, 10))).toBe(3)
  })

  it('falls back to the default duration when unknown', () => {
    expect(dotTicks(dot(10, 10, 2, null))).toBe(FALLBACK_DOT_DURATION_S / 2)
  })

  it('treats a missing interval as 1s ticks', () => {
    expect(dotTicks(hit(10, 10, { kind: 'dot', duration_s: 6 }))).toBe(6)
  })

  it('returns 0 for a non-positive interval', () => {
    expect(dotTicks(dot(10, 10, 0, 10))).toBe(0)
  })
})

describe('abilityBaseAvg', () => {
  it('sums hit averages and dot ticks', () => {
    // hit avg 400 + dot avg 100 * 5 ticks = 900
    const a = ability([hit(300, 500), dot(100, 100, 2, 10)])
    expect(abilityBaseAvg(a)).toBe(900)
  })

  it('is 0 with no components', () => {
    expect(abilityBaseAvg(ability([]))).toBe(0)
  })
})

describe('expectedCastDamage', () => {
  it('adds the full ability mod when under the cap', () => {
    // base 1000, mod 200 (< 500 cap), no crit: 1200
    const a = ability([hit(800, 1200)])
    expect(expectedCastDamage(a, { ability_mod: 200 })).toBe(1200)
  })

  it('caps the ability mod at 50% of base', () => {
    // base 1000, mod 5000 → capped at 500: 1500
    const a = ability([hit(800, 1200)])
    expect(expectedCastDamage(a, { ability_mod: 5000 })).toBe(1000 * (1 + ABILITY_MOD_CAP_FRACTION))
  })

  it('multiplies crit and calibration on top', () => {
    // base 1000, no mod, crit mult 1.3, calibration 1.1 → 1430
    const a = ability([hit(800, 1200)])
    expect(expectedCastDamage(a, { crit_chance: 100 }, 1.1)).toBeCloseTo(1430)
  })

  it('is 0 for a zero-damage ability (no NaN)', () => {
    expect(expectedCastDamage(ability([]), { ability_mod: 500 })).toBe(0)
  })
})

describe('componentTargetMultiplier', () => {
  const t = (over: Partial<SimTarget> = {}): SimTarget => ({ ...DEFAULT_TARGET, ...over })

  it('single scope always hits once', () => {
    expect(componentTargetMultiplier(hit(1, 1), t({ count: 5, encounter: true }))).toBe(1)
  })

  it('aoe scope hits every stacked mob', () => {
    expect(componentTargetMultiplier(hit(1, 1, { target_scope: 'aoe' }), t({ count: 4 }))).toBe(4)
  })

  it('encounter scope hits all mobs only when linked', () => {
    const enc = hit(1, 1, { target_scope: 'encounter' })
    expect(componentTargetMultiplier(enc, t({ count: 4, encounter: true }))).toBe(4)
    expect(componentTargetMultiplier(enc, t({ count: 4, encounter: false }))).toBe(1)
  })

  it('conditional components deal nothing unless toggled on', () => {
    const cond = hit(1, 1, { condition: 'If target is undead' })
    expect(componentTargetMultiplier(cond, t())).toBe(0)
    expect(componentTargetMultiplier(cond, t({ activeConditions: ['If target is undead'] }))).toBe(1)
  })

  it('floors a fractional count at 1', () => {
    expect(componentTargetMultiplier(hit(1, 1, { target_scope: 'aoe' }), t({ count: 0 }))).toBe(1)
  })
})

describe('target-aware damage', () => {
  it('excludes inactive conditional components from base and damage', () => {
    // Divine Strike shape: 400 unconditional + 400 conditional.
    const a = ability([hit(300, 500), hit(300, 500, { condition: 'If target is undead' })])
    expect(abilityBaseAvg(a)).toBe(400)
    expect(expectedCastDamage(a, {})).toBe(400)
    const undead: SimTarget = { ...DEFAULT_TARGET, activeConditions: ['If target is undead'] }
    expect(abilityBaseAvg(a, undead)).toBe(800)
    expect(expectedCastDamage(a, {}, 1, undead)).toBe(800)
  })

  it('caps the mod on single-target base, then scales by target count', () => {
    // AoE hit avg 100, mod 1000 capped at 50 (single-target base), then
    // x3 targets: (100 + 50) * 3 = 450.
    const a = ability([hit(100, 100, { target_scope: 'aoe' })])
    const three: SimTarget = { ...DEFAULT_TARGET, count: 3 }
    expect(expectedCastDamage(a, { ability_mod: 1000 }, 1, three)).toBe(450)
  })

  it('unlinked encounter components hit only the main target', () => {
    const a = ability([hit(100, 100, { target_scope: 'encounter' })])
    const unlinked: SimTarget = { count: 3, encounter: false, activeConditions: [] }
    const linked: SimTarget = { count: 3, encounter: true, activeConditions: [] }
    expect(expectedCastDamage(a, {}, 1, unlinked)).toBe(100)
    expect(expectedCastDamage(a, {}, 1, linked)).toBe(300)
  })
})

describe('predictedNonCritHit', () => {
  it('distributes the capped mod by base share', () => {
    // hit avg 400, dot total 500 (100 avg x 5 ticks), base 900.
    // mod 90 → hit share 90 * 400/900 = 40 → 440.
    const a = ability([hit(300, 500), dot(100, 100, 2, 10)])
    expect(predictedNonCritHit(a, { ability_mod: 90 })).toBeCloseTo(440)
  })

  it('returns null when the ability has no hit component', () => {
    expect(predictedNonCritHit(ability([dot(100, 100, 2, 10)]), {})).toBeNull()
    expect(predictedNonCritHit(ability([]), {})).toBeNull()
  })

  it('ignores crit — it predicts a NON-crit hit', () => {
    const a = ability([hit(300, 500)])
    expect(predictedNonCritHit(a, { crit_chance: 100, crit_bonus: 100 })).toBe(400)
  })

  it('skips conditional hit components — the observed hit is on a plain dummy', () => {
    const a = ability([hit(300, 500, { condition: 'If target is undead' }), hit(100, 200)])
    expect(predictedNonCritHit(a, {})).toBe(150)
  })
})

describe('autoAttackDps', () => {
  it('is 0 with no weapon lines', () => {
    expect(autoAttackDps({})).toBe(0)
    expect(autoAttackDps({ primary_min: 50, primary_max: 100 })).toBe(0) // no delay
  })

  it('computes swing avg over delay for a single weapon', () => {
    // avg 75 / 3s = 25 dps, no modifiers
    expect(autoAttackDps({ primary_min: 50, primary_max: 100, primary_delay: 3 })).toBe(25)
  })

  it('applies dps mod, crit, double attack, and haste', () => {
    // avg 75 * 1.5 (dps) * 1.15 (50% crit) * 1.5 (50% DA) / (3 / 1.25 haste)
    const stats: SimStats = {
      primary_min: 50,
      primary_max: 100,
      primary_delay: 3,
      dps: 50,
      crit_chance: 50,
      double_attack: 50,
      attack_speed: 25,
    }
    expect(autoAttackDps(stats)).toBeCloseTo((75 * 1.5 * 1.15 * 1.5) / (3 / 1.25))
  })

  it('sums primary and secondary weapons', () => {
    const stats: SimStats = {
      primary_min: 50,
      primary_max: 100,
      primary_delay: 3,
      secondary_min: 20,
      secondary_max: 40,
      secondary_delay: 2,
    }
    expect(autoAttackDps(stats)).toBe(25 + 15)
  })
})
