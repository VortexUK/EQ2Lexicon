/**
 * Simulator engine tests — hand-computed discrete-event scenarios:
 * single-ability cadence, priority gap-filling, DoT clipping (fight end
 * + early recast), stat speedups, calibration, auto-attack, and the
 * anti-hang busy floor.
 */
import { describe, expect, it } from 'vitest'

import { MIN_BUSY_S, simulate } from './engine'
import type { DamageComponent, RotationAbility, SimConfig, SimStats, SimTarget } from './types'

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

const dot = (perTickAvg: number, interval: number, duration: number): DamageComponent =>
  hit(perTickAvg, perTickAvg, { kind: 'dot', interval_s: interval, duration_s: duration })

interface AbilitySpec {
  cast?: number
  recast?: number
  recovery?: number
  components?: DamageComponent[]
}

const ability = (base: string, spec: AbilitySpec = {}): RotationAbility => ({
  name: `${base} IV`,
  base_name: base,
  crc: 1,
  tier_name: 'Master',
  level: 70,
  spell_type: 'spells',
  beneficial: false,
  cast_secs: spec.cast ?? 1,
  recast_secs: spec.recast ?? 8,
  recovery_secs: spec.recovery ?? 0,
  target_type: 'other',
  icon_id: null,
  icon_backdrop: null,
  duration_s: null,
  power_cost: null,
  components: spec.components ?? [hit(100, 100)],
  effect_lines: [],
  has_unparsed_damage: false,
})

const config = (
  abilities: RotationAbility[],
  fightDurationS: number,
  over: Partial<SimConfig> = {},
  stats: SimStats = {},
): SimConfig => ({
  rotation: abilities.map(a => a.base_name),
  abilities: Object.fromEntries(abilities.map(a => [a.base_name, a])),
  stats,
  fightDurationS,
  autoAttack: false,
  ...over,
})

const byName = (r: ReturnType<typeof simulate>, name: string) => {
  const e = r.perAbility.find(x => x.ability === name)
  if (!e) throw new Error(`no breakdown for ${name}`)
  return e
}

describe('single-ability cadence', () => {
  it('casts on the recast cadence and idles between', () => {
    // cast 2 + recovery 0.5 → busy 2.5; readyAt = cast start + 2 + 8 = 10.
    // 60s fight → casts at 0,10,20,30,40,50 = 6 casts of 100.
    const a = ability('Nuke', { cast: 2, recovery: 0.5, recast: 8 })
    const r = simulate(config([a], 60))
    const e = byName(r, 'Nuke')
    expect(e.casts).toBe(6)
    expect(r.totalDamage).toBe(600)
    expect(r.dps).toBeCloseTo(10)
    // Idle 7.5s after each 2.5s busy window = 45s of 60.
    expect(r.idlePct).toBeCloseTo(75)
    expect(r.timeline.filter(s => s.ability === '')).toHaveLength(6)
    expect(r.timeline.filter(s => s.ability === 'Nuke')).toHaveLength(6)
  })
})

describe('priority order', () => {
  it('fills recast gaps with lower-priority abilities', () => {
    // A (100 dmg, recast 10) fires once at t=0; B (10 dmg, no recast)
    // fills every remaining 1s slot of the 10s fight.
    const a = ability('Big', { cast: 1, recast: 10, components: [hit(100, 100)] })
    const b = ability('Filler', { cast: 1, recast: 0, components: [hit(10, 10)] })
    const r = simulate(config([a, b], 10))
    expect(byName(r, 'Big').casts).toBe(1)
    expect(byName(r, 'Filler').casts).toBe(9)
    expect(r.totalDamage).toBe(190)
    expect(r.idlePct).toBe(0)
  })

  it('ranks the per-ability breakdown by damage with pct of total', () => {
    const a = ability('Big', { cast: 1, recast: 10, components: [hit(100, 100)] })
    const b = ability('Filler', { cast: 1, recast: 0, components: [hit(10, 10)] })
    const r = simulate(config([a, b], 10))
    expect(r.perAbility[0].ability).toBe('Big')
    expect(r.perAbility[0].pct).toBeCloseTo((100 / 190) * 100)
    expect(byName(r, 'Filler').avgPerCast).toBeCloseTo(10)
  })
})

describe('DoT clipping', () => {
  it('clips ticks that fall past the fight end', () => {
    // Cast finishes at t=1; 5 ticks x 2s would run to t=11 but the fight
    // ends at 6 → floor((6-1)/2) = 2 ticks land, 3 clipped.
    const a = ability('Burn', { cast: 1, recast: 100, components: [dot(50, 2, 10)] })
    const r = simulate(config([a], 6))
    const e = byName(r, 'Burn')
    expect(e.casts).toBe(1)
    expect(e.damage).toBe(100)
    expect(e.clippedDotTicks).toBe(3)
  })

  it('clips the previous application on early re-cast', () => {
    // recast 3 → casts at t=0,4,8 in a 9s fight; dot 5x2s (10s duration)
    // never runs out naturally, so each re-cast clips the remainder.
    //  cast@0: start 1, 4 ticks fit (fight-end clips 1)      → +200
    //  cast@4: prev ticked floor(3/2)=1 of 4 → clip 3        → -150
    //          start 5, 2 ticks fit (fight-end clips 3)      → +100
    //  cast@8: prev ticked floor(3/2)=1 of 2 → clip 1        →  -50
    //          start 9, 0 ticks fit (fight-end clips 5)      →   +0
    const a = ability('Burn', { cast: 1, recast: 3, components: [dot(50, 2, 10)] })
    const r = simulate(config([a], 9))
    const e = byName(r, 'Burn')
    expect(e.casts).toBe(3)
    expect(e.damage).toBe(100)
    expect(e.clippedDotTicks).toBe(3 + 1 + 3 + 1 + 5)
  })
})

describe('stats', () => {
  it('speed stats tighten the cadence', () => {
    // Unbuffed readyAt cadence 10s → 3 casts in 30s; with 100 casting +
    // 100 reuse the cadence is 1 + 4 = 5s → 6 casts.
    const a = ability('Nuke', { cast: 2, recovery: 0.5, recast: 8 })
    const slow = simulate(config([a], 30))
    const fast = simulate(config([a], 30, {}, { casting_speed: 100, reuse_speed: 100, recovery_speed: 100 }))
    expect(byName(slow, 'Nuke').casts).toBe(3)
    expect(byName(fast, 'Nuke').casts).toBe(6)
  })

  it('applies capped ability mod, crit, and calibration per cast', () => {
    // base 100, mod 1000 capped at 50 → 150; x1.3 full crit → 195;
    // x2 calibration → 390. One cast (recast > fight).
    const a = ability('Nuke', { cast: 1, recast: 100 })
    const r = simulate(
      config([a], 2, { calibration: { Nuke: 2 } }, { ability_mod: 1000, crit_chance: 100 }),
    )
    expect(byName(r, 'Nuke').damage).toBeCloseTo(390)
  })
})

describe('DoT refresh hold', () => {
  // Burn: cast 1, recast 3, dot 50 x 5 ticks every 2s (10s duration).
  const burn = () => ability('Burn', { cast: 1, recast: 3, components: [dot(50, 2, 10)] })

  it('held DoTs wait for the last tick — no clipped ticks mid-fight', () => {
    // cast@0: starts ticking at 1, 5 ticks land 3,5,7,9,11 → +250,
    //   gate = 11 (recast up at 4 but held).
    // cast@11: nothing left to clip; new app starts 12, 0 ticks fit the
    //   13s fight (5 clipped at fight end). 2 casts, 250 damage.
    const r = simulate(config([burn()], 13, { dotRefreshHold: ['Burn'] }))
    const e = byName(r, 'Burn')
    expect(e.casts).toBe(2)
    expect(e.damage).toBe(250)
    expect(e.clippedDotTicks).toBe(5) // only the fight-end clip
  })

  it('without hold the same rotation clips itself down', () => {
    // Free re-cast every 4s: casts at 0,4,8,12 — each re-cast clips the
    //   previous application after a single landed tick.
    //   +250 -200+200 -150+100 -50+0 = 150.
    const r = simulate(config([burn()], 13))
    const e = byName(r, 'Burn')
    expect(e.casts).toBe(4)
    expect(e.damage).toBe(150)
    expect(e.clippedDotTicks).toBe(17) // 4+3+1 re-cast clips + 0+1+3+5 fight-end
  })

  it('a held DoT frees the gap for lower-priority fillers', () => {
    const filler = ability('Filler', { cast: 1, recast: 0, components: [hit(10, 10)] })
    const r = simulate(config([burn(), filler], 13, { dotRefreshHold: ['Burn'] }))
    expect(byName(r, 'Burn').casts).toBe(2)
    // Every second not spent casting Burn goes to the filler.
    expect(byName(r, 'Filler').casts).toBe(11)
    expect(r.idlePct).toBe(0)
  })

  it('holding an ability with no DoT changes nothing', () => {
    const a = ability('Nuke', { cast: 1, recast: 3 })
    const held = simulate(config([a], 12, { dotRefreshHold: ['Nuke'] }))
    const free = simulate(config([a], 12))
    expect(held.totalDamage).toBe(free.totalDamage)
    expect(byName(held, 'Nuke').casts).toBe(byName(free, 'Nuke').casts)
  })
})

describe('target model', () => {
  it('encounter components scale with a linked encounter', () => {
    const a = ability('Blast', { cast: 1, recast: 100, components: [hit(100, 100, { target_scope: 'encounter' })] })
    const linked: SimTarget = { count: 3, encounter: true, activeConditions: [] }
    const unlinked: SimTarget = { count: 3, encounter: false, activeConditions: [] }
    expect(simulate(config([a], 2, { target: linked })).totalDamage).toBe(300)
    expect(simulate(config([a], 2, { target: unlinked })).totalDamage).toBe(100)
  })

  it('conditional damage only lands when its condition is toggled on', () => {
    // Divine Strike shape: unconditional 100 + conditional 100.
    const a = ability('Strike', {
      cast: 1,
      recast: 100,
      components: [hit(100, 100), hit(100, 100, { condition: 'If target is undead' })],
    })
    const dummy = simulate(config([a], 2))
    const undead = simulate(
      config([a], 2, { target: { count: 1, encounter: false, activeConditions: ['If target is undead'] } }),
    )
    expect(dummy.totalDamage).toBe(100)
    expect(undead.totalDamage).toBe(200)
  })

  it('aoe dots tick on every stacked mob', () => {
    // 1-cast dot, 2 ticks fit; x3 targets → 50 x 2 x 3 = 300.
    const a = ability('Rain', {
      cast: 1,
      recast: 100,
      components: [hit(50, 50, { kind: 'dot', target_scope: 'aoe', interval_s: 2, duration_s: 4 })],
    })
    const r = simulate(config([a], 6, { target: { count: 3, encounter: false, activeConditions: [] } }))
    expect(r.totalDamage).toBe(300)
  })
})

describe('auto-attack', () => {
  it('adds a continuous stream over the fight', () => {
    // 75 avg / 3s delay = 25 dps x 10s = 250, plus one 100 nuke.
    const a = ability('Nuke', { cast: 1, recast: 100 })
    const r = simulate(
      config([a], 10, { autoAttack: true }, { primary_min: 50, primary_max: 100, primary_delay: 3 }),
    )
    expect(r.autoAttackDamage).toBeCloseTo(250)
    expect(r.totalDamage).toBeCloseTo(350)
  })
})

describe('robustness', () => {
  it('a zero-damage ability produces zeros, never NaN', () => {
    const a = ability('Debuff', { cast: 1, recast: 4, components: [] })
    const r = simulate(config([a], 10))
    const e = byName(r, 'Debuff')
    expect(e.casts).toBeGreaterThan(0)
    expect(e.damage).toBe(0)
    expect(e.pct).toBe(0)
    expect(e.avgPerCast).toBe(0)
    expect(r.dps).toBe(0)
    expect(Number.isNaN(r.idlePct)).toBe(false)
  })

  it('an all-zero-timing ability terminates via the busy floor', () => {
    const a = ability('Broken', { cast: 0, recast: 0, recovery: 0 })
    const r = simulate(config([a], 1))
    expect(byName(r, 'Broken').casts).toBe(Math.round(1 / MIN_BUSY_S))
  })

  it('an empty rotation returns a zeroed result', () => {
    const r = simulate(config([], 10))
    expect(r.totalDamage).toBe(0)
    expect(r.perAbility).toHaveLength(0)
    expect(r.dps).toBe(0)
  })

  it('rotation names missing from the ability map are skipped', () => {
    const a = ability('Real')
    const r = simulate(config([a], 10, { rotation: ['Ghost', 'Real'] }))
    expect(r.perAbility.map(e => e.ability)).toEqual(['Real'])
  })
})
