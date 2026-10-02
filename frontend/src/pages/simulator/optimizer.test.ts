/**
 * Optimizer tests — seeded, deterministic: finds a known-best order,
 * respects the sim cap and time budget, always returns a permutation.
 */
import { describe, expect, it } from 'vitest'

import { createOptimizer, mulberry32, optimize, OPTIMIZER_BATCH } from './optimizer'
import type { DamageComponent, RotationAbility, SimConfig } from './types'

const hit = (min: number, max: number): DamageComponent => ({
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
})

const ability = (base: string, cast: number, recast: number, dmg: number): RotationAbility => ({
  name: base,
  base_name: base,
  crc: 1,
  tier_name: 'Master',
  level: 70,
  spell_type: 'spells',
  beneficial: false,
  cast_secs: cast,
  recast_secs: recast,
  recovery_secs: 0,
  target_type: 'other',
  icon_id: null,
  icon_backdrop: null,
  duration_s: null,
  power_cost: null,
  components: [hit(dmg, dmg)],
  procs: [],
  effect_lines: [],
  has_unparsed_damage: false,
  source: 'spell',
  rank: null,
})

/** Big nuke must outrank the free-chaining filler: with Filler first it
 * free-casts forever and Big never fires. */
const config = (rotation: string[]): SimConfig => {
  const abilities = {
    Big: ability('Big', 1, 4, 1000),
    Filler: ability('Filler', 1, 0, 10),
  }
  return { rotation, abilities, stats: {}, fightDurationS: 60, autoAttack: false }
}

describe('optimizer', () => {
  it('finds the known-best order from a pessimal start', () => {
    const r = optimize(config(['Filler', 'Big']), { seed: 7 })
    expect(r.order[0]).toBe('Big')
    expect(r.improved).toBe(true)
    expect(r.dps).toBeGreaterThan(r.baselineDps * 5)
  })

  it('reports no improvement when the order is already optimal', () => {
    const r = optimize(config(['Big', 'Filler']), { seed: 7 })
    expect(r.improved).toBe(false)
    expect(r.dps).toBe(r.baselineDps)
  })

  it('always returns a permutation of the input rotation', () => {
    const abilities: Record<string, RotationAbility> = {}
    const names = ['A', 'B', 'C', 'D', 'E']
    for (const [i, n] of names.entries()) abilities[n] = ability(n, 1, i * 3, 50 * (i + 1))
    const cfg: SimConfig = { rotation: names, abilities, stats: {}, fightDurationS: 30, autoAttack: false }
    const r = optimize(cfg, { seed: 3, maxSims: 300 })
    expect([...r.order].sort()).toEqual([...names].sort())
  })

  it('respects the sim cap', () => {
    const r = optimize(config(['Filler', 'Big']), { seed: 1, maxSims: 120 })
    expect(r.simsRun).toBeLessThanOrEqual(120)
  })

  it('respects the time budget via the injected clock', () => {
    // Clock jumps 10ms per read → the while loop re-checks after each
    // batch; the budget cuts the run long before the sim cap.
    let t = 0
    const r = optimize(config(['Filler', 'Big']), {
      seed: 1,
      budgetMs: 25,
      now: () => (t += 10),
    })
    expect(r.simsRun).toBeLessThanOrEqual(3 * OPTIMIZER_BATCH + 1)
  })

  it('is deterministic under a fixed seed', () => {
    const a = optimize(config(['Filler', 'Big']), { seed: 42, maxSims: 200 })
    const b = optimize(config(['Filler', 'Big']), { seed: 42, maxSims: 200 })
    expect(a).toEqual(b)
  })

  it('createOptimizer.step honors batch size and done()', () => {
    const o = createOptimizer(config(['Filler', 'Big']), { seed: 5, maxSims: 60 })
    o.step(10)
    expect(o.result().simsRun).toBeLessThanOrEqual(11) // +1 baseline sim
    while (!o.done()) o.step(25)
    expect(o.result().simsRun).toBeLessThanOrEqual(60)
    expect(o.done()).toBe(true)
  })

  it('mulberry32 is deterministic and in [0,1)', () => {
    const r1 = mulberry32(9)
    const r2 = mulberry32(9)
    for (let i = 0; i < 100; i++) {
      const v = r1()
      expect(v).toBe(r2())
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThan(1)
    }
  })

  it('a single-ability rotation is a no-op', () => {
    const r = optimize(config(['Big']).rotation.length ? { ...config(['Big']), rotation: ['Big'] } : config(['Big']), {
      seed: 1,
    })
    expect(r.order).toEqual(['Big'])
    expect(r.improved).toBe(false)
  })
})
