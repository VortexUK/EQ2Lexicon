// Calibration — user-entered in-game datapoints refine the theorycraft.
// Pure, React-free.
//
// The user enters the MINIMUM of the ability's in-game tooltip range;
// factor = observed / predicted absorbs every source of model error at
// once (era-drifted data, stat-scaling rules we don't model, unscaled
// tooltip text). Abilities without an observation fall back to the mean
// factor of the observed ones (systematic error is usually shared), and
// 1 when nothing is observed.

import { predictedTooltipMin } from './formulas'
import type { RotationAbility, SimStats } from './types'

/** base_name → in-game tooltip minimum (user-entered). */
export type ObservedHits = Record<string, number>

export interface CalibrationResult {
  /** base_name → damage multiplier the engine applies (all abilities). */
  factors: Record<string, number>
  /** base_name → the per-ability factor, only where observed. */
  observedFactors: Record<string, number>
  /** Mean of the observed factors — the fallback for the rest. */
  globalFactor: number
}

export function computeCalibration(
  observed: ObservedHits,
  abilities: Record<string, RotationAbility>,
  stats: SimStats,
): CalibrationResult {
  const observedFactors: Record<string, number> = {}
  for (const [name, value] of Object.entries(observed)) {
    const a = abilities[name]
    if (!a || !(value > 0)) continue
    const predicted = predictedTooltipMin(a, stats)
    if (predicted == null || predicted <= 0) continue
    observedFactors[name] = value / predicted
  }
  const values = Object.values(observedFactors)
  const globalFactor = values.length > 0 ? values.reduce((s, v) => s + v, 0) / values.length : 1
  const factors: Record<string, number> = {}
  for (const name of Object.keys(abilities)) {
    factors[name] = observedFactors[name] ?? globalFactor
  }
  return { factors, observedFactors, globalFactor }
}
