/**
 * Calibration tests — observed/predicted factors, global mean fallback,
 * and invalid-input handling.
 */
import { describe, expect, it } from 'vitest'

import { computeCalibration } from './calibration'
import type { DamageComponent, RotationAbility } from './types'

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

const ability = (base: string, components: DamageComponent[]): RotationAbility => ({
  name: `${base} IV`,
  base_name: base,
  crc: 1,
  tier_name: 'Master',
  level: 70,
  spell_type: 'spells',
  beneficial: false,
  cast_secs: 1,
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
})

const ABILITIES = {
  Big: ability('Big', [hit(900, 1100)]), // predicted tooltip MIN 1350 (900 x 1.5, no mod)
  Small: ability('Small', [hit(90, 110)]), // predicted min 135
  NoHit: ability('NoHit', []),
}

describe('computeCalibration', () => {
  it('factor = observed / predicted per ability', () => {
    const r = computeCalibration({ Big: 2025 }, ABILITIES, {})
    expect(r.observedFactors.Big).toBeCloseTo(1.5)
    expect(r.factors.Big).toBeCloseTo(1.5)
  })

  it('unobserved abilities fall back to the mean observed factor', () => {
    const r = computeCalibration({ Big: 2025, Small: 67.5 }, ABILITIES, {})
    // factors 1.5 and 0.5 -> mean 1.0 for the rest.
    expect(r.globalFactor).toBeCloseTo(1)
    expect(r.factors.NoHit).toBeCloseTo(1)
  })

  it('no observations → all factors 1', () => {
    const r = computeCalibration({}, ABILITIES, {})
    expect(r.globalFactor).toBe(1)
    expect(Object.values(r.factors).every(f => f === 1)).toBe(true)
  })

  it('ignores zero/negative observations and unknown abilities', () => {
    const r = computeCalibration({ Big: 0, Ghost: 500 }, ABILITIES, {})
    expect(r.observedFactors).toEqual({})
    expect(r.globalFactor).toBe(1)
  })

  it('accounts for the ability mod in the prediction', () => {
    // Big min 900x1.5 + mod 200 -> predicted 1550; observed 1550 -> factor 1.
    const r = computeCalibration({ Big: 1550 }, ABILITIES, { ability_mod: 200 })
    expect(r.observedFactors.Big).toBeCloseTo(1)
  })
})
