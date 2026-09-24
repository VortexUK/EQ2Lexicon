// Rotation simulation engine — discrete-event priority loop.
//
// Model (v1, single-target boss dummy):
//   * At each decision point, cast the FIRST ability in priority order
//     whose recast timer is up. Busy time = effective cast + recovery;
//     the recast timer starts when the cast completes (readyAt =
//     castStart + castTime + recast).
//   * DoTs are analytic snapshot-at-cast: total tick damage is credited
//     over the DoT's duration; re-casting before expiry clips the
//     remaining ticks of the previous application (tracked per ability);
//     ticks past the fight end are clipped too.
//   * Auto-attack is a continuous analytic stream (no swing events).
//   * When nothing is ready, time jumps to the next recast expiry (idle).
//
// Kept free of React so it unit-tests directly. All tuning constants
// live in formulas.ts.

import {
  autoAttackDps,
  componentTargetMultiplier,
  critMultiplier,
  dotTicks,
  effCastTime,
  effRecast,
  effRecovery,
  expectedCastDamage,
  ABILITY_MOD_CAP_FRACTION,
  DEFAULT_TARGET,
} from './formulas'
import type { AbilityBreakdown, RotationAbility, SimConfig, SimResult, SimStats, SimTarget, TimelineSegment } from './types'

/** Floor on per-cast busy time — a data anomaly (0 cast, 0 recovery,
 * 0 recast) must not spin the loop forever. */
export const MIN_BUSY_S = 0.1
const MAX_ITERATIONS = 100_000

interface DotState {
  /** When the previous application stops ticking (cast time + duration). */
  expiresAt: number
  ticksScheduled: number
  perTick: number
  interval: number
  startedAt: number
}

function dotComponentsDamage(
  ability: RotationAbility,
  stats: SimStats,
  target: SimTarget,
  calibration: number,
): { instant: number; dots: { perTick: number; ticks: number; interval: number; duration: number }[] } {
  // Distribute the capped ability mod across ACTIVE components by their
  // single-target base share (the mod applies per hit on each target),
  // then split instant vs dot for scheduling and scale each component by
  // its target multiplier. Crit + calibration apply to every component
  // (snapshot at cast). Inactive conditional components contribute
  // nothing and are excluded from the dot state list entirely.
  const crit = critMultiplier(stats)
  let base = 0
  for (const c of ability.components) {
    if (componentTargetMultiplier(c, target) <= 0) continue
    const avg = (c.min_dmg + c.max_dmg) / 2
    base += c.kind === 'dot' ? avg * dotTicks(c) : avg
  }
  if (base <= 0) return { instant: 0, dots: [] }
  const mod = Math.min(Math.max(stats.ability_mod ?? 0, 0), base * ABILITY_MOD_CAP_FRACTION)
  const scale = ((base + mod) / base) * crit * calibration

  let instant = 0
  const dots: { perTick: number; ticks: number; interval: number; duration: number }[] = []
  for (const c of ability.components) {
    const mult = componentTargetMultiplier(c, target)
    if (mult <= 0) continue
    const avg = (c.min_dmg + c.max_dmg) / 2
    if (c.kind === 'dot') {
      const ticks = dotTicks(c)
      const interval = c.interval_s ?? 1
      dots.push({ perTick: avg * scale * mult, ticks, interval, duration: ticks * interval })
    } else {
      instant += avg * scale * mult
    }
  }
  return { instant, dots }
}

export function simulate(config: SimConfig): SimResult {
  const { rotation, abilities, stats, fightDurationS, autoAttack } = config
  const target = config.target ?? DEFAULT_TARGET
  const calibration = config.calibration ?? {}
  const holdDots = new Set(config.dotRefreshHold ?? [])

  const order = rotation.filter(n => abilities[n])
  const readyAt: Record<string, number> = {}
  const dotStates: Record<string, DotState[]> = {}
  // For held abilities: the time the live application's LAST tick lands —
  // re-casting at or after this clips nothing.
  const dotGateAt: Record<string, number> = {}
  const perAbility: Record<string, AbilityBreakdown> = {}
  for (const n of order) {
    readyAt[n] = 0
    dotStates[n] = []
    dotGateAt[n] = 0
    perAbility[n] = { ability: n, casts: 0, damage: 0, pct: 0, avgPerCast: 0, clippedDotTicks: 0 }
  }
  const timeline: TimelineSegment[] = []

  let t = 0
  let idle = 0
  let iterations = 0
  while (t < fightDurationS - 1e-9 && order.length > 0 && iterations++ < MAX_ITERATIONS) {
    const next = order.find(
      n => readyAt[n] <= t + 1e-9 && (!holdDots.has(n) || dotGateAt[n] <= t + 1e-9),
    )
    if (next === undefined) {
      const wake = Math.min(
        ...order.map(n => Math.max(readyAt[n], holdDots.has(n) ? dotGateAt[n] : 0)),
      )
      const jump = Math.min(wake, fightDurationS) - t
      if (jump <= 0) break
      timeline.push({ t, dur: jump, ability: '' })
      idle += jump
      t += jump
      continue
    }

    const a = abilities[next]
    const cal = calibration[next] ?? 1
    const castTime = effCastTime(a.cast_secs, stats)
    const busy = Math.max(castTime + effRecovery(a.recovery_secs, stats), MIN_BUSY_S)
    readyAt[next] = Math.max(t + castTime + effRecast(a.recast_secs, stats), t + busy)

    const { instant, dots } = dotComponentsDamage(a, stats, target, cal)
    const entry = perAbility[next]
    entry.casts += 1
    entry.damage += instant

    let lastTickAt = 0
    for (let di = 0; di < dots.length; di++) {
      const d = dots[di]
      // Clip the previous application's remaining ticks (early re-cast).
      // States pair with dot components by index (stable per ability).
      const prev = dotStates[next][di]
      if (prev && prev.expiresAt > t) {
        const elapsed = t - prev.startedAt
        const tickedSoFar = Math.min(prev.ticksScheduled, Math.floor(elapsed / prev.interval))
        const clipped = prev.ticksScheduled - tickedSoFar
        entry.damage -= clipped * prev.perTick
        entry.clippedDotTicks += clipped
      }
      // Credit the full application now; clip fight-end overrun below.
      const start = t + castTime
      const ticksInFight = Math.min(d.ticks, Math.max(0, Math.floor((fightDurationS - start) / d.interval)))
      const clippedAtEnd = d.ticks - ticksInFight
      entry.damage += ticksInFight * d.perTick
      entry.clippedDotTicks += clippedAtEnd
      dotStates[next][di] = {
        expiresAt: start + d.duration,
        ticksScheduled: ticksInFight,
        perTick: d.perTick,
        interval: d.interval,
        startedAt: start,
      }
      lastTickAt = Math.max(lastTickAt, start + ticksInFight * d.interval)
    }
    if (dots.length > 0) dotGateAt[next] = lastTickAt

    timeline.push({ t, dur: busy, ability: next })
    t += busy
  }

  const abilityDamage = Object.values(perAbility).reduce((s, e) => s + e.damage, 0)
  const autoDamage = autoAttack ? autoAttackDps(stats) * fightDurationS : 0
  const total = abilityDamage + autoDamage

  const breakdown = Object.values(perAbility)
    .map(e => ({
      ...e,
      pct: total > 0 ? (100 * e.damage) / total : 0,
      avgPerCast: e.casts > 0 ? e.damage / e.casts : 0,
    }))
    .sort((x, y) => y.damage - x.damage)

  return {
    totalDamage: total,
    dps: fightDurationS > 0 ? total / fightDurationS : 0,
    perAbility: breakdown,
    autoAttackDamage: autoDamage,
    timeline,
    idlePct: fightDurationS > 0 ? (100 * idle) / fightDurationS : 0,
  }
}

/** Convenience: the expected one-cast damage table shown in the UI. */
export function castDamageTable(
  abilities: RotationAbility[],
  stats: SimStats,
  calibration: Record<string, number> = {},
  target: SimTarget = DEFAULT_TARGET,
): Record<string, number> {
  const out: Record<string, number> = {}
  for (const a of abilities) {
    out[a.base_name] = expectedCastDamage(a, stats, calibration[a.base_name] ?? 1, target)
  }
  return out
}
