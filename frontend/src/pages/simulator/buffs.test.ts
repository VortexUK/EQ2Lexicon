/**
 * Buff-window math tests — staggered provider windows, uptime union,
 * non-stacking same-buff overlap, stat merging, and the engine's
 * snapshot/piecewise semantics.
 */
import { describe, expect, it } from 'vitest'

import { activeBuffIds, applyMods, buffUptimes, buildExternalWindows, modsAt, windowEdges } from './buffs'
import type { ExternalBuffDef } from './buffs'
import { simulate } from './engine'
import type { BuffWindow, DamageComponent, RotationAbility, SimConfig } from './types'

const DEFS: Record<string, ExternalBuffDef> = {
  jcap: { duration_s: 30, recast_s: 90, mods: { castSpeedPct: 50 } },
  burst: { duration_s: 12, recast_s: 60, mods: { dmgPct: 100 } },
}

const win = (buffId: string, start: number, end: number, mods = {}): BuffWindow => ({ buffId, start, end, mods })

describe('buildExternalWindows + buffUptimes', () => {
  it('one provider covers duration/recast of the fight', () => {
    // 30s up every 90s over 180s → 60/180 = 33.3%
    const w = buildExternalWindows([{ buffId: 'jcap', providers: 1 }], DEFS, 180)
    expect(buffUptimes(w, 180).jcap).toBeCloseTo(100 / 3)
  })

  it('staggered providers stack coverage up to 100%', () => {
    // 2 providers: 60/180 each, offset 45s apart → 66.7%; 3 → 100%.
    const two = buildExternalWindows([{ buffId: 'jcap', providers: 2 }], DEFS, 180)
    expect(buffUptimes(two, 180).jcap).toBeCloseTo(200 / 3)
    const three = buildExternalWindows([{ buffId: 'jcap', providers: 3 }], DEFS, 180)
    expect(buffUptimes(three, 180).jcap).toBeCloseTo(100)
  })

  it('user timing overrides beat the def', () => {
    // duration bumped to 90 = permanent coverage even with 1 provider.
    const w = buildExternalWindows([{ buffId: 'jcap', providers: 1, duration_s: 90 }], DEFS, 180)
    expect(buffUptimes(w, 180).jcap).toBeCloseTo(100)
  })

  it('overlapping same-buff windows merge, not double-count', () => {
    const w = [win('x', 0, 60), win('x', 30, 90)]
    expect(buffUptimes(w, 120).x).toBeCloseTo(75)
  })

  it('zero providers or unknown buff yields nothing', () => {
    expect(buildExternalWindows([{ buffId: 'jcap', providers: 0 }], DEFS, 180)).toHaveLength(0)
    expect(buildExternalWindows([{ buffId: 'nope', providers: 2 }], DEFS, 180)).toHaveLength(0)
  })
})

describe('modsAt + applyMods', () => {
  it('sums different buffs but never stacks the same buff', () => {
    const w = [
      win('a', 0, 30, { castSpeedPct: 20 }),
      win('a', 10, 40, { castSpeedPct: 20 }), // second provider, same buff
      win('b', 0, 30, { castSpeedPct: 5 }),
    ]
    expect(modsAt(w, 15).castSpeedPct).toBe(25)
    expect(modsAt(w, 35).castSpeedPct).toBe(20) // only a's second window
    expect(modsAt(w, 50).castSpeedPct).toBe(0)
  })

  it('applyMods adds onto the sheet stats', () => {
    const eff = applyMods({ crit_chance: 40, attack_speed: 10 }, { critChancePct: 20, hastePct: 15 })
    expect(eff.crit_chance).toBe(60)
    expect(eff.attack_speed).toBe(25)
  })

  it('activeBuffIds dedupes and windowEdges clips to the fight', () => {
    const w = [win('a', 0, 30), win('a', 10, 40), win('b', 200, 300)]
    expect(activeBuffIds(w, 15)).toEqual(['a'])
    expect(windowEdges(w, 100)).toEqual([0, 10, 30, 40, 100])
  })
})

// ── Engine integration ───────────────────────────────────────────────────────

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

const ability = (base: string, over: Partial<RotationAbility> = {}): RotationAbility => ({
  name: `${base} IV`,
  base_name: base,
  crc: 1,
  tier_name: 'Master',
  level: 70,
  spell_type: 'spells',
  beneficial: false,
  cast_secs: 1,
  recast_secs: 8,
  recovery_secs: 0,
  target_type: 'other',
  icon_id: null,
  icon_backdrop: null,
  duration_s: null,
  power_cost: null,
  components: [hit(100, 100)],
  procs: [],
  effect_lines: [],
  has_unparsed_damage: false,
  source: 'spell',
  rank: null,
  ...over,
})

const config = (abilities: RotationAbility[], fightDurationS: number, over: Partial<SimConfig> = {}): SimConfig => ({
  rotation: abilities.map(a => a.base_name),
  abilities: Object.fromEntries(abilities.map(a => [a.base_name, a])),
  stats: {},
  fightDurationS,
  autoAttack: false,
  ...over,
})

describe('engine buff integration', () => {
  it('damage windows buff casts inside them only (snapshot at cast)', () => {
    // Nuke casts at t=0 (inside +100% dmg window ending at 5) and t=9.
    const a = ability('Nuke')
    const r = simulate(config([a], 12, { buffWindows: [win('burst', 0, 5, { dmgPct: 100 })] }))
    // cast@0 -> 300, cast@9 -> 150.
    expect(r.totalDamage).toBe(450)
    expect(r.timeline[0].buffs).toEqual(['burst'])
    expect(r.buffUptimes.burst).toBeCloseTo((5 / 12) * 100)
  })

  it('a DoT snapshots the window at cast and keeps it after expiry', () => {
    // Dot cast at t=0 inside the window; ticks land after it closes but
    // keep the doubled snapshot: 75 x 4 ticks x 2 = 600.
    const a = ability('Burn', {
      recast_secs: 100,
      components: [hit(50, 50, { kind: 'dot', interval_s: 2, duration_s: 8 })],
    })
    const r = simulate(config([a], 20, { buffWindows: [win('burst', 0, 2, { dmgPct: 100 })] }))
    expect(r.totalDamage).toBe(600)
  })

  it('cast-speed windows shorten casts only while up', () => {
    // cast 2s, recast 0 → free-chains. +100% cast speed for first 10s of
    // a 20s fight → 10 one-second casts + 5 two-second casts.
    const a = ability('Chain', { cast_secs: 2, recast_secs: 0 })
    const r = simulate(config([a], 20, { buffWindows: [win('jcap', 0, 10, { castSpeedPct: 100 })] }))
    expect(r.perAbility[0].casts).toBe(15)
  })

  it('auto-attack integrates piecewise across window edges', () => {
    // Base auto 25 dps; +100% dps mod for half the 20s fight:
    // 25x10 + 50x10 = 750.
    const r = simulate(
      config([], 20, {
        autoAttack: true,
        stats: { primary_min: 50, primary_max: 100, primary_delay: 3 },
        buffWindows: [win('burst', 0, 10, { dpsModPct: 100 })],
      }),
    )
    expect(r.autoAttackDamage).toBeCloseTo(750)
  })

  it('casting an own temp buff opens a window that buffs later casts', () => {
    // Priority: Temp (beneficial, 1s cast, 10s duration, +100% dmg via
    // selfBuffMods) then Nuke. Temp@0 → window [1,11); nukes at 1..:
    // each nuke inside doubles.
    const temp = ability('Temp', { beneficial: true, duration_s: 10, components: [], recast_secs: 100 })
    const nuke = ability('Nuke', { recast_secs: 0 })
    const r = simulate(
      config([temp, nuke], 6, { selfBuffMods: { Temp: { dmgPct: 100 } } }),
    )
    // Nukes at t=1..5 (5 casts) all inside the window -> 5 x 300.
    expect(r.totalDamage).toBe(1500)
    expect(r.buffUptimes.Temp).toBeCloseTo((5 / 6) * 100)
  })

  it('uncurated temp buffs still appear as zero-effect windows', () => {
    const temp = ability('Mystery', { beneficial: true, duration_s: 10, components: [], recast_secs: 100 })
    const nuke = ability('Nuke', { recast_secs: 0 })
    const r = simulate(config([temp, nuke], 6))
    expect(r.buffUptimes.Mystery).toBeGreaterThan(0)
    // 5 nukes, unbuffed.
    expect(r.totalDamage).toBe(750)
  })
})
