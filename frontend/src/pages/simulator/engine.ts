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

import { activeBuffIds, applyMods, buffUptimes, modsAt, windowEdges } from './buffs'
import {
  abilityDamageMultiplier,
  autoAttackDps,
  autoSwingRate,
  baseAutoSwingRate,
  componentFlatFraction,
  componentTargetMultiplier,
  abilityModShare,
  critMultiplier,
  damageCoefficient,
  dotTicks,
  effCastTime,
  effRecast,
  effRecovery,
  expectedCastDamage,
  fervorMultiplier,
  primaryComponent,
  procHitDamage,
  DEFAULT_TARGET,
} from './formulas'
import type {
  AbilityBreakdown,
  BuffWindow,
  RotationAbility,
  SimConfig,
  SimResult,
  SimStats,
  SimTarget,
  TimelineSegment,
} from './types'

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
  // Tooltip-validated model: per component application B̄×(coeff + ½),
  // ability mod ONCE on the primary component (as instant damage — for a
  // pure-DoT primary it lands with the first tick, approximated here as
  // instant), all × crit × fervor × doublecast (dealt-damage multipliers)
  // × calibration, each component scaled by its target multiplier.
  // Everything snapshots at cast. Inactive conditional components
  // contribute nothing and are excluded from the dot state list.
  const coeff = damageCoefficient(stats, ability.level)
  const dealt = abilityDamageMultiplier(stats) * calibration
  const primary = primaryComponent(ability)

  let instant = 0
  const dots: { perTick: number; ticks: number; interval: number; duration: number }[] = []
  for (const c of ability.components) {
    const mult = componentTargetMultiplier(c, target)
    if (mult <= 0) continue
    const per = coeff + componentFlatFraction(ability.level)
    const avg = (c.min_dmg + c.max_dmg) / 2
    if (c === primary) {
      instant += Math.max(stats.ability_mod ?? 0, 0) * abilityModShare(c) * dealt * mult
    }
    if (c.kind === 'dot') {
      const ticks = dotTicks(c)
      const interval = c.interval_s ?? 1
      dots.push({ perTick: avg * per * dealt * mult, ticks, interval, duration: ticks * interval })
    } else {
      instant += avg * per * dealt * mult
    }
  }
  // Trigger-budget procs carried BY the ability (Slothful Spirit grants
  // exactly N Sloth's Habitat hits per application) — credited instantly.
  for (const p of ability.procs ?? []) {
    if (p.trigger_count && p.trigger_count > 0) {
      instant += p.trigger_count * (p.chance_pct / 100) * procHitDamage(p.components, stats, ability.level) * dealt
    }
  }
  return { instant, dots }
}

export function simulate(config: SimConfig): SimResult {
  const { rotation, abilities, stats, fightDurationS, autoAttack } = config
  const target = config.target ?? DEFAULT_TARGET
  const calibration = config.calibration ?? {}
  const holdDots = new Set(config.dotRefreshHold ?? [])
  const selfBuffMods = config.selfBuffMods ?? {}
  // External windows are precomputed; casting a temp beneficial appends
  // its own window here (stats snapshot at cast reads this list).
  const windows: BuffWindow[] = [...(config.buffWindows ?? [])]

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
  // Hostile cast counts feed the passive-proc trigger rates.
  let artsCasts = 0
  let spellCasts = 0
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
    // Stats snapshot at cast start: sheet stats + whatever buff windows
    // are open right now. DoTs keep this snapshot (era-accurate).
    const mods = modsAt(windows, t)
    const eff = applyMods(stats, mods)
    const cal = (calibration[next] ?? 1) * (1 + mods.dmgPct / 100)
    const castTime = effCastTime(a.cast_secs, eff)
    const busy = Math.max(castTime + effRecovery(a.recovery_secs, eff), MIN_BUSY_S)
    readyAt[next] = Math.max(t + castTime + effRecast(a.recast_secs, eff), t + busy)

    // Casting one of the character's own temp buffs opens its window at
    // cast end for the ability's duration (mods from the curated map;
    // uncurated temps still show as windows with zero effect).
    if (a.beneficial && a.duration_s && a.duration_s > 0) {
      windows.push({
        buffId: next,
        start: t + castTime,
        end: Math.min(t + castTime + a.duration_s, fightDurationS),
        mods: selfBuffMods[next] ?? {},
      })
    }

    if (!a.beneficial) {
      if (a.spell_type === 'arts') artsCasts += 1
      else spellCasts += 1
    }

    const { instant, dots } = dotComponentsDamage(a, eff, target, cal)
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

    timeline.push({ t, dur: busy, ability: next, buffs: activeBuffIds(windows, t) })
    t += busy
  }

  const abilityDamage = Object.values(perAbility).reduce((s, e) => s + e.damage, 0)
  // Auto-attack integrates piecewise across buff-window edges so haste/
  // dps/crit windows raise the stream only while they're up. Swing count
  // is tracked alongside — each swing is a proc-able hit.
  let autoDamage = 0
  let autoSwings = 0
  if (autoAttack) {
    const edges = windowEdges(windows, fightDurationS)
    for (let i = 0; i + 1 < edges.length; i++) {
      const seg = edges[i + 1] - edges[i]
      const segStats = applyMods(stats, modsAt(windows, (edges[i] + edges[i + 1]) / 2))
      autoDamage += autoAttackDps(segStats) * seg
      autoSwings += baseAutoSwingRate(segStats) * seg
    }
  }

  // Passive proc streams (Bolt of Power etc.): expected procs = trigger
  // events × chance. Approximations (v1): base-stat crit, no ability mod,
  // single-target, an AoE/multi-hit cast counts as one trigger event.
  const hostileCasts = artsCasts + spellCasts
  const eventsFor = (trigger: string): number => {
    if (trigger === 'any_hit') return autoSwings + hostileCasts
    if (trigger === 'melee_hit') return autoSwings + artsCasts
    if (trigger === 'ability_cast') return hostileCasts
    if (trigger === 'spell_cast') return spellCasts
    // Damage-shield procs (Divine Light → Shock of Light) fire on hits
    // AGAINST the buff's target — rated by the user-set incoming knob.
    if (trigger === 'when_damaged') return (fightDurationS / 60) * (config.incomingHitsPerMinute ?? 0)
    return 0
  }
  const procRows: AbilityBreakdown[] = []
  // Proc payloads DEAL tooltip-chain damage + AM×⅓ (log-validated on
  // Bolt of Power + Blessed Armament: means within 1.1%, mins 0.3%),
  // then crit × fervor (crit/non-crit dealt ratio observed 1.29).
  // Maintained toggles (Exorcise) are continuous pulse streams instead:
  // one pulse per interval for the whole fight, aoe scope ⇒ no mod.
  for (const passive of config.passives ?? []) {
    // Buff-granted procs (PotM's Precise Note) hit with the SUPPLIER's
    // stats — the bard's chain/crit — not the receiving player's.
    const procStats = passive.proc_stats ? (passive.proc_stats as SimStats) : stats
    const procCritFervor = critMultiplier(procStats) * fervorMultiplier(procStats)
    let procDamage = 0
    let procCount = 0
    if (passive.maintained) {
      const coeff = damageCoefficient(procStats, passive.level)
      for (const c of passive.components ?? []) {
        if (c.kind !== 'dot' || !c.interval_s || c.interval_s <= 0) continue
        const mult = componentTargetMultiplier(c, target)
        if (mult <= 0) continue
        const pulses = fightDurationS / c.interval_s
        const avg = (c.min_dmg + c.max_dmg) / 2
        procCount += pulses
        procDamage += pulses * avg * (coeff + componentFlatFraction(passive.level)) * mult * procCritFervor
      }
    } else {
      const chanceOverride = config.procChanceOverrides?.[passive.name]
      for (const p of passive.procs ?? []) {
        const events =
          p.per_minute != null && p.per_minute > 0
            ? (fightDurationS / 60) * p.per_minute
            : eventsFor(p.trigger)
        const n = events * ((chanceOverride ?? p.chance_pct) / 100)
        procCount += n
        procDamage += n * procHitDamage(p.components, procStats, passive.level) * procCritFervor
      }
    }
    if (procCount <= 0) continue
    procRows.push({
      ability: passive.base_name,
      label: passive.rank != null ? `${passive.name} (rank ${passive.rank})` : passive.name,
      casts: Math.round(procCount),
      damage: procDamage,
      pct: 0,
      avgPerCast: procCount > 0 ? procDamage / procCount : 0,
      clippedDotTicks: 0,
      isProc: true,
    })
  }
  const procDamageTotal = procRows.reduce((s, e) => s + e.damage, 0)
  const total = abilityDamage + autoDamage + procDamageTotal

  // Auto-attack gets its own breakdown row (casts = base swings; multi
  // attack/flurry extras are inside the damage, not the count).
  if (autoDamage > 0) {
    procRows.push({
      ability: 'Auto-attack',
      label: config.autoAttackLabel ?? 'Auto-attack',
      casts: Math.round(autoSwings),
      damage: autoDamage,
      pct: 0,
      avgPerCast: autoSwings > 0 ? autoDamage / autoSwings : 0,
      clippedDotTicks: 0,
    })
  }

  const breakdown = [...Object.values(perAbility), ...procRows]
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
    buffUptimes: buffUptimes(windows, fightDurationS),
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
