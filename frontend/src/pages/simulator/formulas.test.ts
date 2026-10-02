/**
 * Simulator formula tests — hand-computed expectations for the stat math
 * the engine builds on: crit multiplier, speed-modified timings, DoT
 * ticks, ability base damage, capped ability mod, and auto-attack DPS.
 */
import { describe, expect, it } from 'vitest'

import {
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
  expectedCritSwing,
  extraSwingFactor,
  predictedTooltipMin,
  primaryStatBonus,
  speedStatEffective,
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
  procs: [],
  effect_lines: [],
  has_unparsed_damage: false,
  source: 'spell',
  rank: null,
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
  it('per application = B̄ × (coeff + ½), plus the FULL mod on the primary hit', () => {
    // base avg 1000 → 1500, + mod 200 = 1700 (no stats → coeff 1).
    const a = ability([hit(800, 1200)])
    expect(expectedCastDamage(a, { ability_mod: 200 })).toBe(1700)
  })

  it('the ability mod is uncapped', () => {
    // base avg 1000 → 1500 + 5000 = 6500.
    const a = ability([hit(800, 1200)])
    expect(expectedCastDamage(a, { ability_mod: 5000 })).toBe(6500)
  })

  it('multiplies crit and calibration on top', () => {
    // 1000 × 1.5 = 1500, × 1.3 crit × 1.1 calibration → 2145
    const a = ability([hit(800, 1200)])
    expect(expectedCastDamage(a, { crit_chance: 100 }, 1.1)).toBeCloseTo(2145)
  })

  it('is 0 for a zero-damage ability (no NaN)', () => {
    expect(expectedCastDamage(ability([]), { ability_mod: 500 })).toBe(0)
  })
})

describe('primaryStatBonus', () => {
  it('plateaus at 0.65 between the level cap and 1200', () => {
    // Spell L=72 → cap 1100; S=1150 sits in the plateau band.
    expect(primaryStatBonus(1150, 72)).toBe(0.65)
  })

  it('follows the log curve at 1200+', () => {
    // 0.28·log2(1493) − 2.2 ≈ 0.7523
    expect(primaryStatBonus(1493, 72)).toBeCloseTo(0.7523, 4)
    // exactly at the threshold: 0.28·log2(1200) − 2.2 ≈ 0.6638
    expect(primaryStatBonus(1200, 72)).toBeCloseTo(0.6638, 3)
  })

  it('ramps below the cap and is 0 with no stat', () => {
    // L=72 → cap 1100; S=550 → half the plateau.
    expect(primaryStatBonus(550, 72)).toBeCloseTo(0.325)
    expect(primaryStatBonus(0, 72)).toBe(0)
    expect(primaryStatBonus(null, 72)).toBe(0)
  })
})

describe('potency / fervor / doublecast (TLE modern engine)', () => {
  it('potency multiplies base; mod added flat after', () => {
    // B=100, P=100 → coeff 2, per-app (2+½)×100 = 250; + mod 1000 = 1250.
    const a = ability([hit(100, 100)])
    expect(expectedCastDamage(a, { potency: 100, ability_mod: 1000 })).toBe(1250)
  })

  it('fervor and doublecast multiply the final (dealt) damage', () => {
    const a = ability([hit(100, 100)])
    expect(expectedCastDamage(a, { fervor: 10, ability_doublecast: 50 })).toBeCloseTo(150 * 1.1 * 1.5)
  })

  it('base-damage-bonus % is additive with the primary-stat bonus', () => {
    // coeff = (1 + 0 + 0.35) × 1 = 1.35 → per-app 1.85 × 100 = 185.
    const a = ability([hit(100, 100)])
    expect(expectedCastDamage(a, { base_damage_bonus_pct: 35 })).toBeCloseTo(185)
  })

  it('predictedTooltipMin excludes crit/fervor/doublecast', () => {
    const a = ability([hit(100, 100)])
    const v = predictedTooltipMin(a, {
      potency: 100,
      ability_mod: 1000,
      fervor: 10,
      crit_chance: 100,
      ability_doublecast: 100,
    })
    expect(v).toBeCloseTo(1250) // (2+½)×100 + 1000, fervor excluded
  })

  it('reproduces the blind-validated Divine Strike VII tooltip', () => {
    // In-game tooltip read 5,494 - 6,191. Model min: WIS 1493 -> +75.23%,
    // +35% gear/AA, x1.95 potency -> coeff 4.0995; B_min 754 x 4.5995
    // + AM 1983 = 5,451 - within 1% of the observed minimum.
    const a = ability([hit(754, 922)], { level: 78 })
    const stats: SimStats = {
      primary_stat: 1493,
      potency: 95,
      base_damage_bonus_pct: 35,
      ability_mod: 1983,
    }
    const v = predictedTooltipMin(a, stats)
    expect(v).not.toBeNull()
    expect(Math.abs((v as number) - 5494)).toBeLessThan(55)
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
  it('excludes inactive conditional components from damage', () => {
    // Divine Strike shape: 400 unconditional + 400 conditional rider.
    // Dummy: 400×1.5 = 600; undead: + rider 600 → 1200 (no mod → rider
    // and primary scale identically here).
    const a = ability([hit(300, 500), hit(300, 500, { condition: 'If target is undead' })])
    expect(abilityBaseAvg(a)).toBe(400)
    expect(expectedCastDamage(a, {})).toBe(600)
    const undead: SimTarget = { ...DEFAULT_TARGET, activeConditions: ['If target is undead'] }
    expect(abilityBaseAvg(a, undead)).toBe(800)
    expect(expectedCastDamage(a, {}, 1, undead)).toBe(1200)
  })

  it('ability-mod share by scope: aoe none, encounter third, single full', () => {
    // Blind-validated: Exorcise (blue AoE) tooltip carries NO mod;
    // Wrath of the Ancients (encounter) carries AM/3; single carries AM.
    const three: SimTarget = { ...DEFAULT_TARGET, count: 3, encounter: true }
    const aoe = ability([hit(100, 100, { target_scope: 'aoe' })])
    expect(expectedCastDamage(aoe, { ability_mod: 1000 }, 1, three)).toBe(450) // 100x1.5x3, no mod
    const enc = ability([hit(100, 100, { target_scope: 'encounter' })])
    expect(expectedCastDamage(enc, { ability_mod: 1000 }, 1, three)).toBeCloseTo(1450, 6) // (150+1000/3)x3
    const single = ability([hit(100, 100)])
    expect(expectedCastDamage(single, { ability_mod: 1000 }, 1, three)).toBe(1150) // 150+1000
  })

  it('unlinked encounter components hit only the main target', () => {
    const a = ability([hit(100, 100, { target_scope: 'encounter' })])
    const unlinked: SimTarget = { count: 3, encounter: false, activeConditions: [] }
    const linked: SimTarget = { count: 3, encounter: true, activeConditions: [] }
    expect(expectedCastDamage(a, {}, 1, unlinked)).toBeCloseTo(150, 6)
    expect(expectedCastDamage(a, {}, 1, linked)).toBeCloseTo(450, 6)
  })

  it('T6-and-below spells (level < 61) carry no +half flat fraction', () => {
    // Velium Winds/Wrath of the Ancients fit: tooltip spreads sit on the
    // BARE chain for level 59/60 spells; 61+ keeps the half.
    const low = { ...ability([hit(100, 100)]), level: 59 }
    expect(expectedCastDamage(low, {})).toBe(100) // coeff 1.0, no half
    const high = { ...ability([hit(100, 100)]), level: 61 }
    expect(expectedCastDamage(high, {})).toBe(150)
  })
})

describe('predictedTooltipMin', () => {
  it('the primary hit gets the FULL mod (ticks and riders get none)', () => {
    // hit MIN 300 x 1.5 = 450, + mod 90 = 540 (the dot is irrelevant).
    const a = ability([hit(300, 500), dot(100, 100, 2, 10)])
    expect(predictedTooltipMin(a, { ability_mod: 90 })).toBeCloseTo(540)
  })

  it('returns null when the ability has no unconditional hit component', () => {
    expect(predictedTooltipMin(ability([dot(100, 100, 2, 10)]), {})).toBeNull()
    expect(predictedTooltipMin(ability([]), {})).toBeNull()
  })

  it('ignores crit — tooltips are non-crit', () => {
    const a = ability([hit(300, 500)])
    expect(predictedTooltipMin(a, { crit_chance: 100, crit_bonus: 100 })).toBe(450)
  })

  it('skips conditional hit components — the tooltip main line is unconditional', () => {
    const a = ability([hit(300, 500, { condition: 'If target is undead' }), hit(100, 200)])
    expect(predictedTooltipMin(a, {})).toBe(150)
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

  it('applies dps mod, crit (with max+1 floor), multi attack, and haste', () => {
    // Non-crit avg 75; crit swing = E[max(1.3·roll, 101)] on uniform
    // [50,100]: floor share (t=101/1.3≈77.69) → ((77.69−50)·101 +
    // 1.3·(100²−77.69²)/2)/50 ≈ 107.47. 50% crit → E ≈ 91.234.
    // × 1.5 dps × 1.5 MA / (3 / 1.25 haste) ≈ 85.53.
    const stats: SimStats = {
      primary_min: 50,
      primary_max: 100,
      primary_delay: 3,
      dps: 50,
      crit_chance: 50,
      double_attack: 50,
      attack_speed: 25,
    }
    expect(autoAttackDps(stats)).toBeCloseTo(85.532, 2)
  })

  it('haste and dps stats follow the diminishing-returns curve', () => {
    expect(speedStatEffective(50)).toBe(50)
    expect(speedStatEffective(100)).toBe(100)
    expect(speedStatEffective(150)).toBeCloseTo(112.5)
    expect(speedStatEffective(200)).toBe(125)
    expect(speedStatEffective(300)).toBe(125) // hard cap
  })

  it('multi attack is linear past 100 and flurry adds extra hits', () => {
    // MA 237 → +2.37 swings; flurry 10 → +0.1 (ONE hit per proc —
    // log-fitted: 178-swing session, extras 21.9% vs DA 15.7 + flurry 6.9).
    expect(extraSwingFactor({ double_attack: 237 })).toBeCloseTo(3.37)
    expect(extraSwingFactor({ flurry: 10 })).toBeCloseTo(1.1)
    expect(extraSwingFactor({ double_attack: 700 })).toBeCloseTo(7)
  })

  it('crit floor: most crits sit at max+1 (validated on live logs)', () => {
    // Wand session numbers: uniform [547, 2463], crit ×1.3 → floor 2464.
    const v = expectedCritSwing(547, 2463, 1.3)
    expect(v).toBeGreaterThan(2464)
    expect(v).toBeLessThan(1.3 * 2463)
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
