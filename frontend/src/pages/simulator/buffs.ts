// Buff-window math — pure, React-free (aaplanner engine convention).
//
// External buffs (Jester's Cap from the group's troubs, etc.) are timed
// windows: N providers rotating one buff cast it staggered (provider i
// starts at i·recast/N and re-casts every recast), so coverage
// uptime = min(1, N·duration/recast) emerges from the window union.
// Windows sharing a buffId never stack — overlap merges into coverage.

import type { BuffMods, BuffWindow, ExternalBuffConfig, SimStats } from './types'

export interface ExternalBuffDef {
  duration_s: number
  recast_s: number
  mods: BuffMods
}

/** Deterministic staggered windows for the configured external buffs,
 * clipped to the fight. Providers of one buff are evenly offset. */
export function buildExternalWindows(
  configs: ExternalBuffConfig[],
  defs: Record<string, ExternalBuffDef>,
  fightDurationS: number,
): BuffWindow[] {
  const out: BuffWindow[] = []
  for (const cfg of configs) {
    const def = defs[cfg.buffId]
    if (!def || cfg.providers <= 0) continue
    const duration = cfg.duration_s ?? def.duration_s
    const recast = cfg.recast_s ?? def.recast_s
    if (duration <= 0 || recast <= 0) continue
    const n = Math.floor(cfg.providers)
    for (let i = 0; i < n; i++) {
      for (let start = (i * recast) / n; start < fightDurationS; start += recast) {
        out.push({
          buffId: cfg.buffId,
          start,
          end: Math.min(start + duration, fightDurationS),
          mods: def.mods,
        })
      }
    }
  }
  return out
}

/** Buff ids active at time t (deduped — same-buff overlaps count once). */
export function activeBuffIds(windows: BuffWindow[], t: number): string[] {
  const seen = new Set<string>()
  for (const w of windows) {
    if (w.start <= t + 1e-9 && t < w.end - 1e-9) seen.add(w.buffId)
  }
  return [...seen]
}

/** Combined mods at time t. Same-buff overlapping windows apply ONCE
 * (first window found wins — providers cast the same rank). */
export function modsAt(windows: BuffWindow[], t: number): BuffMods {
  const seen = new Set<string>()
  const total: Required<BuffMods> = {
    castSpeedPct: 0,
    reuseSpeedPct: 0,
    recoverySpeedPct: 0,
    dmgPct: 0,
    critChancePct: 0,
    critBonusPct: 0,
    hastePct: 0,
    dpsModPct: 0,
    doubleAttackPct: 0,
    abilityModFlat: 0,
    potencyPct: 0,
    fervorPct: 0,
    strFlat: 0,
    agiFlat: 0,
    wisFlat: 0,
    intFlat: 0,
  }
  for (const w of windows) {
    if (seen.has(w.buffId) || w.start > t + 1e-9 || t >= w.end - 1e-9) continue
    seen.add(w.buffId)
    total.castSpeedPct += w.mods.castSpeedPct ?? 0
    total.reuseSpeedPct += w.mods.reuseSpeedPct ?? 0
    total.recoverySpeedPct += w.mods.recoverySpeedPct ?? 0
    total.dmgPct += w.mods.dmgPct ?? 0
    total.critChancePct += w.mods.critChancePct ?? 0
    total.critBonusPct += w.mods.critBonusPct ?? 0
    total.hastePct += w.mods.hastePct ?? 0
    total.dpsModPct += w.mods.dpsModPct ?? 0
    total.doubleAttackPct += w.mods.doubleAttackPct ?? 0
    total.abilityModFlat += w.mods.abilityModFlat ?? 0
    total.potencyPct += w.mods.potencyPct ?? 0
    total.fervorPct += w.mods.fervorPct ?? 0
    total.strFlat += w.mods.strFlat ?? 0
    total.agiFlat += w.mods.agiFlat ?? 0
    total.wisFlat += w.mods.wisFlat ?? 0
    total.intFlat += w.mods.intFlat ?? 0
  }
  return total
}

/** Sheet stats + additive buff mods (dmgPct is NOT here — it multiplies
 * damage and is applied by the engine at cast time). */
/** The flat attribute add that actually matters: the one hitting the
 * character's primary attribute (AGI on a templar does nothing). */
export function primaryAttrFlat(base: SimStats, mods: BuffMods): number {
  switch (base.primary_attr) {
    case 'str': return mods.strFlat ?? 0
    case 'agi': return mods.agiFlat ?? 0
    case 'wis': return mods.wisFlat ?? 0
    case 'int': return mods.intFlat ?? 0
    default: return 0
  }
}

export function applyMods(base: SimStats, mods: BuffMods): SimStats {
  return {
    ...base,
    primary_stat: (base.primary_stat ?? 0) + primaryAttrFlat(base, mods),
    casting_speed: (base.casting_speed ?? 0) + (mods.castSpeedPct ?? 0),
    reuse_speed: (base.reuse_speed ?? 0) + (mods.reuseSpeedPct ?? 0),
    recovery_speed: (base.recovery_speed ?? 0) + (mods.recoverySpeedPct ?? 0),
    crit_chance: (base.crit_chance ?? 0) + (mods.critChancePct ?? 0),
    crit_bonus: (base.crit_bonus ?? 0) + (mods.critBonusPct ?? 0),
    attack_speed: (base.attack_speed ?? 0) + (mods.hastePct ?? 0),
    dps: (base.dps ?? 0) + (mods.dpsModPct ?? 0),
    double_attack: (base.double_attack ?? 0) + (mods.doubleAttackPct ?? 0),
    ability_mod: (base.ability_mod ?? 0) + (mods.abilityModFlat ?? 0),
    potency: (base.potency ?? 0) + (mods.potencyPct ?? 0),
    fervor: (base.fervor ?? 0) + (mods.fervorPct ?? 0),
  }
}

/** buffId → % of the fight covered by ≥1 window of that buff. */
export function buffUptimes(windows: BuffWindow[], fightDurationS: number): Record<string, number> {
  if (fightDurationS <= 0) return {}
  const byBuff = new Map<string, BuffWindow[]>()
  for (const w of windows) {
    const list = byBuff.get(w.buffId) ?? []
    list.push(w)
    byBuff.set(w.buffId, list)
  }
  const out: Record<string, number> = {}
  for (const [id, list] of byBuff) {
    const sorted = [...list].sort((a, b) => a.start - b.start)
    let covered = 0
    let curStart = -1
    let curEnd = -1
    for (const w of sorted) {
      const s = Math.max(0, w.start)
      const e = Math.min(fightDurationS, w.end)
      if (e <= s) continue
      if (s > curEnd) {
        if (curEnd > curStart) covered += curEnd - curStart
        curStart = s
        curEnd = e
      } else {
        curEnd = Math.max(curEnd, e)
      }
    }
    if (curEnd > curStart) covered += curEnd - curStart
    out[id] = (100 * covered) / fightDurationS
  }
  return out
}

/** Time points where any window opens or closes inside [0, fight] —
 * the piecewise-integration edges for the auto-attack stream. */
export function windowEdges(windows: BuffWindow[], fightDurationS: number): number[] {
  const pts = new Set<number>([0, fightDurationS])
  for (const w of windows) {
    if (w.start > 0 && w.start < fightDurationS) pts.add(w.start)
    if (w.end > 0 && w.end < fightDurationS) pts.add(w.end)
  }
  return [...pts].sort((a, b) => a - b)
}
