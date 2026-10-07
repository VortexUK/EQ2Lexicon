// Rotation simulation engine — discrete-event priority loop: cast the first
// ready ability in priority order (recast starts when the cast completes);
// DoTs credit at cast and clip on early re-cast or fight end. React-free;
// tuning constants live in formulas.ts.

import { activeBuffIds, applyMods, buffUptimes, modsAt, windowEdges } from './buffs'
import {
  abilityDamageMultiplierFor,
  abilityDmgMod,
  abilityDotDmgMod,
  autoAttackDps,
  baseAutoSwingRate,
  componentFlatFraction,
  componentTargetMultiplier,
  abilityModShare,
  critMultiplier,
  damageCoefficient,
  dotTicks,
  effCastTimeFor,
  effRecastFor,
  effRecovery,
  expectedCastDamage,
  fervorMultiplier,
  flatDamageMod,
  isPerHp,
  perHpDamage,
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
  // flat mod (ability mod + school-matched gear flat) ONCE on the
  // primary component (as instant damage — for a pure-DoT primary it
  // lands with the first tick, approximated here as instant), all ×
  // crit × fervor × doublecast (dealt-damage multipliers) × calibration,
  // each component scaled by its target multiplier. The ability's own
  // Enhance % multiplies the BASE-CHAIN parts only — never the flat mod
  // or proc payloads.
  // Everything snapshots at cast. Inactive conditional components
  // contribute nothing and are excluded from the dot state list.
  const coeff = damageCoefficient(stats, ability.level)
  const dealt = abilityDamageMultiplierFor(stats, ability) * calibration
  const dmgMod = abilityDmgMod(ability)
  const dotMod = abilityDotDmgMod(ability)
  const primary = primaryComponent(ability)

  let instant = 0
  const dots: { perTick: number; ticks: number; interval: number; duration: number }[] = []
  for (const c of ability.components) {
    const mult = componentTargetMultiplier(c, target)
    if (mult <= 0) continue
    const half = c.no_flat_mod ? 0 : componentFlatFraction(ability.level)
    const per = (coeff + half) * dmgMod
    // Per-HP components (Lifeburn) are FLAT: rate × fraction × max
    // health, outside the chain and Enhance.
    const appAvg = isPerHp(c) ? perHpDamage(c, stats) : ((c.min_dmg + c.max_dmg) / 2) * per
    if (c === primary && !c.no_flat_mod) {
      instant += flatDamageMod(stats, c.school) * abilityModShare(c) * dealt * mult
    }
    if (c.kind === 'dot') {
      const ticks = dotTicks(c)
      const interval = c.interval_s ?? 1
      dots.push({ perTick: appAvg * dotMod * dealt * mult, ticks, interval, duration: ticks * interval })
    } else {
      instant += appAvg * dealt * mult
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
  // Seconds of open window per cast-applied proc buff (Consumption) —
  // its proc damage is credited after the loop from the trigger-event
  // totals, which aren't known until every cast is placed.
  const castProcWindowSec: Record<string, number> = {}
  const dotStates: Record<string, DotState[]> = {}
  // For held abilities: the time the live application's LAST tick lands —
  // re-casting at or after this clips nothing.
  const dotGateAt: Record<string, number> = {}
  const perAbility: Record<string, AbilityBreakdown> = {}
  const firstCastAt = config.firstCastAt ?? {}
  for (const n of order) {
    // The temp-buff timing slider: hold the FIRST cast until its chosen
    // time; recasts follow the normal cooldown cadence.
    readyAt[n] = Math.max(0, Math.min(firstCastAt[n] ?? 0, fightDurationS))
    dotStates[n] = []
    dotGateAt[n] = 0
    perAbility[n] = { ability: n, casts: 0, damage: 0, pct: 0, avgPerCast: 0, clippedDotTicks: 0 }
  }
  const timeline: TimelineSegment[] = []

  // Exact per-second damage bins — the DPS-over-time chart's data. Every
  // credit/clip below mirrors into the bins, so they sum to totalDamage.
  const binCount = Math.max(1, Math.ceil(fightDurationS))
  const dpsBins: number[] = new Array(binCount).fill(0)
  const addAt = (at: number, amount: number) => {
    if (amount === 0) return
    dpsBins[Math.min(Math.max(Math.floor(at), 0), binCount - 1)] += amount
  }
  const spreadUniform = (from: number, to: number, amount: number) => {
    if (amount === 0 || to <= from) return
    const perSec = amount / (to - from)
    for (let b = Math.floor(from); b < to && b < binCount; b++) {
      const overlap = Math.min(b + 1, to) - Math.max(b, from)
      if (overlap > 0) dpsBins[b] += perSec * overlap
    }
  }

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
    const cal = (calibration[next] ?? 1) * (1 + (mods.dmgPct ?? 0) / 100)
    const castTime = effCastTimeFor(a, eff)
    const busy = Math.max(castTime + effRecovery(a.recovery_secs, eff), MIN_BUSY_S)
    readyAt[next] = Math.max(t + castTime + effRecastFor(a, eff), t + busy)

    // Casting one of the character's own temp buffs opens its window at
    // cast end for the ability's duration (mods from the curated map;
    // uncurated temps still show as windows with zero effect).
    if (a.beneficial && a.duration_s && a.duration_s > 0) {
      const wEnd = Math.min(t + castTime + a.duration_s, fightDurationS)
      windows.push({
        buffId: next,
        start: t + castTime,
        end: wEnd,
        mods: selfBuffMods[next] ?? a.mods ?? {},
      })
      castProcWindowSec[next] = (castProcWindowSec[next] ?? 0) + Math.max(0, wEnd - (t + castTime))
    }

    if (!a.beneficial) {
      if (a.spell_type === 'arts') artsCasts += 1
      else spellCasts += 1
    }

    const { instant, dots } = dotComponentsDamage(a, eff, target, cal)
    const entry = perAbility[next]
    entry.casts += 1
    entry.damage += instant
    addAt(t + castTime, instant)

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
        for (let k = tickedSoFar + 1; k <= prev.ticksScheduled; k++) {
          addAt(prev.startedAt + k * prev.interval, -prev.perTick)
        }
      }
      // Credit the full application now; clip fight-end overrun below.
      const start = t + castTime
      const ticksInFight = Math.min(d.ticks, Math.max(0, Math.floor((fightDurationS - start) / d.interval)))
      const clippedAtEnd = d.ticks - ticksInFight
      entry.damage += ticksInFight * d.perTick
      entry.clippedDotTicks += clippedAtEnd
      for (let k = 1; k <= ticksInFight; k++) addAt(start + k * d.interval, d.perTick)
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
      const segDamage = autoAttackDps(segStats) * seg
      autoDamage += segDamage
      autoSwings += baseAutoSwingRate(segStats) * seg
      spreadUniform(edges[i], edges[i + 1], segDamage)
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

  // Cast-applied proc buffs (Consumption: "will cast Consume on any
  // combat or spell hit", 15s window): while a window is open every
  // qualifying event fires the payload — expected procs = event rate ×
  // window seconds × chance (rate-capped procs use their per-minute).
  // Payloads follow the validated proc rule (tooltip chain + AM/3),
  // × crit × fervor. Trigger-budget procs are already credited per cast.
  for (const n of order) {
    const a = abilities[n]
    const windowSec = castProcWindowSec[n] ?? 0
    if (windowSec <= 0 || fightDurationS <= 0) continue
    for (const p of a.procs ?? []) {
      if (p.trigger_count && p.trigger_count > 0) continue
      const count =
        p.per_minute != null && p.per_minute > 0
          ? (windowSec / 60) * p.per_minute
          : (eventsFor(p.trigger) / fightDurationS) * windowSec * (p.chance_pct / 100)
      if (count <= 0) continue
      const procDmg =
        count *
        procHitDamage(p.components, stats, a.level) *
        critMultiplier(stats) *
        fervorMultiplier(stats) *
        (calibration[n] ?? 1)
      perAbility[n].damage += procDmg
      // Spread across the buff's actual open windows (bins stay exact).
      const spans = windows.filter(w => w.buffId === n)
      const total = spans.reduce((sum, w) => sum + Math.max(0, Math.min(w.end, fightDurationS) - w.start), 0)
      for (const w of spans) {
        const span = Math.max(0, Math.min(w.end, fightDurationS) - w.start)
        if (span > 0 && total > 0) spreadUniform(w.start, Math.min(w.end, fightDurationS), procDmg * (span / total))
      }
    }
  }

  const abilityDamage = Object.values(perAbility).reduce((s, e) => s + e.damage, 0)
  const procRows: AbilityBreakdown[] = []
  // Proc payloads DEAL tooltip-chain damage + AM×⅓, then crit × fervor.
  // Maintained toggles (Exorcise) are continuous pulse streams instead:
  // one pulse per interval for the whole fight, aoe scope ⇒ no mod.
  for (const passive of config.passives ?? []) {
    const streamCal = calibration[passive.base_name] ?? 1
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
        procDamage += pulses * avg * (coeff + componentFlatFraction(passive.level)) * mult * procCritFervor * streamCal
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
        procDamage += n * procHitDamage(p.components, procStats, passive.level) * procCritFervor * streamCal
      }
    }
    if (procCount <= 0) continue
    spreadUniform(0, fightDurationS, procDamage)
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
    buffWindows: windows,
    dpsBins,
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
