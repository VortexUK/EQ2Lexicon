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
  procs: [],
  effect_lines: [],
  has_unparsed_damage: false,
  source: 'spell',
  rank: null,
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
    expect(r.totalDamage).toBe(900)
    expect(r.dps).toBeCloseTo(15)
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
    expect(r.totalDamage).toBe(285)
    expect(r.idlePct).toBe(0)
  })

  it('ranks the per-ability breakdown by damage with pct of total', () => {
    const a = ability('Big', { cast: 1, recast: 10, components: [hit(100, 100)] })
    const b = ability('Filler', { cast: 1, recast: 0, components: [hit(10, 10)] })
    const r = simulate(config([a, b], 10))
    expect(r.perAbility[0].ability).toBe('Big')
    expect(r.perAbility[0].pct).toBeCloseTo((150 / 285) * 100)
    expect(byName(r, 'Filler').avgPerCast).toBeCloseTo(15)
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
    expect(e.damage).toBe(150)
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
    expect(e.damage).toBe(150)
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

  it('applies the flat ability mod, crit, and calibration per cast', () => {
    // base 100 → 150 per-app + mod 1000 = 1150; x1.3 full crit → 1495;
    // x2 calibration → 2990. One cast (recast > fight).
    const a = ability('Nuke', { cast: 1, recast: 100 })
    const r = simulate(
      config([a], 2, { calibration: { Nuke: 2 } }, { ability_mod: 1000, crit_chance: 100 }),
    )
    expect(byName(r, 'Nuke').damage).toBeCloseTo(2990)
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
    expect(e.damage).toBe(375)
    expect(e.clippedDotTicks).toBe(5) // only the fight-end clip
  })

  it('without hold the same rotation clips itself down', () => {
    // Free re-cast every 4s: casts at 0,4,8,12 — each re-cast clips the
    //   previous application after a single landed tick.
    //   +250 -200+200 -150+100 -50+0 = 150.
    const r = simulate(config([burn()], 13))
    const e = byName(r, 'Burn')
    expect(e.casts).toBe(4)
    expect(e.damage).toBe(225)
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
    expect(simulate(config([a], 2, { target: linked })).totalDamage).toBeCloseTo(450)
    expect(simulate(config([a], 2, { target: unlinked })).totalDamage).toBeCloseTo(150)
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
    expect(dummy.totalDamage).toBe(150)
    expect(undead.totalDamage).toBe(300)
  })

  it('aoe dots tick on every stacked mob', () => {
    // 1-cast dot, 2 ticks fit; x3 targets -> 75 x 2 x 3 = 450.
    const a = ability('Rain', {
      cast: 1,
      recast: 100,
      components: [hit(50, 50, { kind: 'dot', target_scope: 'aoe', interval_s: 2, duration_s: 4 })],
    })
    const r = simulate(config([a], 6, { target: { count: 3, encounter: false, activeConditions: [] } }))
    expect(r.totalDamage).toBe(450)
  })
})

describe('passive proc streams', () => {
  const passive = (base: string, trigger: string, chance: number, comps: DamageComponent[]): RotationAbility => ({
    ...ability(base, { components: [] }),
    beneficial: true,
    spell_type: 'pcinnates',
    source: 'aa',
    rank: 10,
    procs: [{ trigger, chance_pct: chance, name: base, per_minute: null, components: comps }],
  })

  it('ability-cast triggers scale with hostile casts', () => {
    // Nuke free-chains: 10 casts in 10s. 50% proc x 100x1.5 dmg → 5 procs, 750.
    const nuke = ability('Nuke', { cast: 1, recast: 0 })
    const bolt = passive('Bolt', 'ability_cast', 50, [hit(100, 100)])
    const r = simulate(config([nuke], 10, { passives: [bolt] }))
    const row = r.perAbility.find(e => e.isProc)
    expect(row?.casts).toBe(5)
    expect(row?.damage).toBe(750)
    expect(r.totalDamage).toBe(1500 + 750)
  })

  it('any-hit triggers count auto swings too', () => {
    // No rotation; 20s of auto at 2s delay = 10 swings; 100% proc x 50x1.5.
    const bolt = passive('Bolt', 'any_hit', 100, [hit(50, 50)])
    const r = simulate(
      config([], 20, { autoAttack: true, passives: [bolt] }, { primary_min: 50, primary_max: 100, primary_delay: 2 }),
    )
    expect(r.perAbility.find(e => e.isProc)?.damage).toBeCloseTo(750)
    expect(r.totalDamage).toBeCloseTo(750 + 750) // auto 37.5 dps x 20 + procs
  })

  it('multi-attack extras do NOT add proc triggers (log-validated); crit raises proc damage', () => {
    const bolt = passive('Bolt', 'melee_hit', 100, [hit(50, 50)])
    const r = simulate(
      config([], 20, { autoAttack: true, passives: [bolt] }, {
        primary_min: 50,
        primary_max: 100,
        primary_delay: 2,
        double_attack: 100,
        crit_chance: 100,
      }),
    )
    // 10 BASE swings only (DA extras never proc — wand session: 190
    // procs on 190 base swings of 304 auto hits); 10 x 50x1.5 x 1.3.
    expect(r.perAbility.find(e => e.isProc)?.damage).toBeCloseTo(975)
  })

  it('rate-limited procs use times-per-minute, not trigger events', () => {
    // Apply Poison shape: ~3/min over 120s = 6 procs x 336x1.5 avg = 3024.
    const poison = passive('Poison', 'melee_hit', 100, [hit(336, 336)])
    poison.procs[0].per_minute = 3
    const r = simulate(config([], 120, { autoAttack: false, passives: [poison] }))
    const row = r.perAbility.find(e => e.isProc)
    expect(row?.casts).toBe(6)
    expect(row?.damage).toBeCloseTo(3024)
  })

  it('single-target proc payloads deal +AM/3 (log-validated)', () => {
    // 10 casts, 100% proc: (100x1.5 + 900/3) x 10 = 4500.
    const nuke = ability('Nuke', { cast: 1, recast: 0 })
    const bolt = passive('Bolt', 'ability_cast', 100, [hit(100, 100)])
    const r = simulate(config([nuke], 10, { passives: [bolt] }, { ability_mod: 900 }))
    expect(r.perAbility.find(e => e.isProc)?.damage).toBeCloseTo(4500)
  })

  it('proc chance overrides correct stale census text', () => {
    // Bolt of Power reads 50% at rank 10 but fires on every attack.
    const nuke = ability('Nuke', { cast: 1, recast: 0 })
    const bolt = passive('Bolt', 'ability_cast', 50, [hit(100, 100)])
    const r = simulate(
      config([nuke], 10, { passives: [bolt], procChanceOverrides: { 'Bolt IV': 100 } }),
    )
    expect(r.perAbility.find(e => e.isProc)?.casts).toBe(10)
    expect(r.perAbility.find(e => e.isProc)?.damage).toBe(1500)
  })

  it('maintained toggles pulse continuously for the whole fight', () => {
    // Exorcise shape: aoe pulse every 6s, no duration, no AM share.
    const exorcise: RotationAbility = {
      ...ability('Exorcise', { components: [] }),
      beneficial: true,
      maintained: true,
      components: [
        hit(300, 300, { target_scope: 'aoe' }),
        { ...hit(300, 300, { target_scope: 'aoe' }), kind: 'dot', interval_s: 6, duration_s: null },
      ],
    }
    const r = simulate(config([], 60, { passives: [exorcise], stats: { ability_mod: 900 } }))
    // 60s / 6s = 10 pulses x 300x1.5 = 4500; the mod never applies.
    const row = r.perAbility.find(e => e.isProc)
    expect(row?.casts).toBe(10)
    expect(row?.damage).toBeCloseTo(4500)
  })

  it('buff-granted procs scale off the SUPPLIER stats (proc_stats)', () => {
    // PotM shape: the bard's chain, not the player's. Player has huge
    // stats; the supplier has none -> payload stays 100x1.5 (+ the
    // SUPPLIER's AM/3 = 0), even though the player's AM is 900.
    const nuke = ability('Nuke', { cast: 1, recast: 0 })
    const potm: RotationAbility = {
      ...passive('Precise Note', 'spell_cast', 100, [hit(100, 100)]),
      proc_stats: {},
    }
    const r = simulate(config([nuke], 10, { passives: [potm] }, { ability_mod: 900, potency: 100 }))
    expect(r.perAbility.find(e => e.isProc)?.damage).toBeCloseTo(10 * 150)
  })

  it('auto-attack appears as its own breakdown row', () => {
    const r = simulate(
      config([], 20, { autoAttack: true }, { primary_min: 50, primary_max: 100, primary_delay: 2 }),
    )
    const row = r.perAbility.find(e => e.ability === 'Auto-attack')
    expect(row?.casts).toBe(10)
    expect(row?.damage).toBeCloseTo(750)
    expect(row?.avgPerCast).toBeCloseTo(75)
  })

  it('when_damaged procs rate from the incoming-hits knob', () => {
    // Divine Light shape: 100% per incoming hit; 12/min over 60s = 12
    // procs x 100x1.5 = 1800. Knob absent -> zero contribution.
    const shield = passive('Divine Light', 'when_damaged', 100, [hit(100, 100)])
    const on = simulate(config([], 60, { passives: [shield], incomingHitsPerMinute: 12 }))
    expect(on.perAbility.find(e => e.isProc)?.casts).toBe(12)
    expect(on.perAbility.find(e => e.isProc)?.damage).toBeCloseTo(1800)
    const off = simulate(config([], 60, { passives: [shield] }))
    expect(off.perAbility.find(e => e.isProc)).toBeUndefined()
  })

  it('trigger-budget procs add N payload hits per cast (Slothful Spirit)', () => {
    // One cast, no components, proc budget 3 x payload 100x1.5 = 450.
    const sloth = ability('Slothful', { cast: 1, recast: 100, components: [] })
    sloth.procs = [
      { trigger: 'target_cast', chance_pct: 100, name: "Sloth's Habitat", per_minute: null, trigger_count: 3, components: [hit(100, 100)] },
    ]
    const r = simulate(config([sloth], 5))
    expect(byName(r, 'Slothful').damage).toBeCloseTo(450)
  })

  it('maintained streams honor their calibration factor', () => {
    const exorcise: RotationAbility = {
      ...passive('Exorcise', 'any_hit', 100, []),
      maintained: true,
      procs: [],
      components: [{ ...hit(300, 300, { target_scope: 'aoe' }), kind: 'dot', interval_s: 6, duration_s: null }],
    }
    const r = simulate(config([], 60, { passives: [exorcise], calibration: { Exorcise: 1.1 } }))
    // 10 pulses x 300x1.5 x 1.1 = 4950.
    expect(r.perAbility.find(e => e.isProc)?.damage).toBeCloseTo(4950)
  })

  it('spell-cast triggers ignore combat arts', () => {
    const art = ability('Slash', { cast: 1, recast: 0 })
    art.spell_type = 'arts'
    const bolt = passive('Bolt', 'spell_cast', 100, [hit(50, 50)])
    const r = simulate(config([art], 10, { passives: [bolt] }))
    expect(r.perAbility.find(e => e.isProc)).toBeUndefined()
  })
})

describe('own temp buffs', () => {
  it('casting a temp buff opens a window with its PARSED mods', () => {
    // Buff: +100 casting speed for 10s. Nuke cast 2s -> 1s inside the
    // window. Result exposes the window for the timeline shading.
    const buffAb: RotationAbility = {
      ...ability('Quicken', { cast: 1, recast: 100, components: [] }),
      beneficial: true,
      duration_s: 10,
      mods: { castSpeedPct: 100 },
    }
    const nuke = ability('Nuke', { cast: 2, recast: 0 })
    const r = simulate(config([buffAb, nuke], 12))
    const win = r.buffWindows.find(w => w.buffId === 'Quicken')
    expect(win).toBeDefined()
    expect(win?.mods.castSpeedPct).toBe(100)
    // 1s buff cast, then nukes at 1s casts inside the window: more casts
    // than the unbuffed 2s cadence would allow.
    expect(byName(r, 'Nuke').casts).toBeGreaterThan(5)
  })
})

describe('cast-applied proc buffs (Consumption)', () => {
  it('credits windowed proc payloads from the trigger-event rate', () => {
    // Buff: 10s window, cast 1, recast 100 → one cast at t=0, window 1-11.
    // Nuke: cast 1, recast 1 → busy 1, readyAt = castStart+2 → casts at
    // t=1,3,…,29 = 15 hostile casts over the 30s fight (no autos).
    // any_hit rate = 15/30 = 0.5/s × 10s window × 100% chance = 5 procs
    // × payload 100×(1+½) = 150 → 750 credited to the buff's row.
    const buff = ability('Consumption', { cast: 1, recast: 100, components: [] })
    buff.beneficial = true
    buff.duration_s = 10
    buff.procs = [
      { trigger: 'any_hit', chance_pct: 100, name: 'Consume', per_minute: null, components: [hit(100, 100)] },
    ]
    const nuke = ability('Nuke', { cast: 1, recast: 1 })
    const r = simulate(config([buff, nuke], 30))
    expect(byName(r, 'Nuke').casts).toBe(15)
    expect(byName(r, 'Consumption').damage).toBeCloseTo(750)
    // One cast, so the whole 750 reads as its per-cast value.
    expect(byName(r, 'Consumption').casts).toBe(1)
  })

  it('uses the per-minute rate over the window when the proc is rate-capped', () => {
    // Same shape but "Triggers about 6 times per minute" → 10s window
    // yields exactly 1 proc regardless of how often the nuke fires.
    const buff = ability('ProcBuff', { cast: 1, recast: 100, components: [] })
    buff.beneficial = true
    buff.duration_s = 10
    buff.procs = [
      { trigger: 'any_hit', chance_pct: 100, name: 'Payload', per_minute: 6, components: [hit(100, 100)] },
    ]
    const nuke = ability('Nuke', { cast: 1, recast: 1 })
    const r = simulate(config([buff, nuke], 30))
    expect(byName(r, 'ProcBuff').damage).toBeCloseTo(150)
  })
})

describe('firstCastAt (temp-buff timing slider)', () => {
  it('holds the FIRST cast until the chosen time; recasts follow normally', () => {
    // Buff (+100% dmg, 10s window) delayed to t=10; nuke free-chains at
    // 1s busy. Without delay the window covers t=1-11; delayed it covers
    // t=11-21 — nukes inside double from 150 to 300.
    const buff = ability('Temp', { cast: 1, recast: 100, components: [] })
    buff.beneficial = true
    buff.duration_s = 10
    const nuke = ability('Nuke', { cast: 1, recast: 0 })
    const cfg = (delay?: number) =>
      config([buff, nuke], 20, {
        selfBuffMods: { Temp: { dmgPct: 100 } },
        ...(delay != null ? { firstCastAt: { Temp: delay } } : {}),
      })
    const eager = simulate(cfg())
    const delayed = simulate(cfg(10))
    // Eager: window 1-11 covers 10 nuke casts -> 10x300 + 9x150 = 4350.
    // Delayed: window 11-21 covers 9 casts -> 10x150 + 9x300 = 4200.
    expect(byName(eager, 'Temp').casts).toBe(1)
    expect(byName(delayed, 'Temp').casts).toBe(1)
    expect(eager.totalDamage).toBeCloseTo(4350)
    expect(delayed.totalDamage).toBeCloseTo(4200)
    // The window genuinely moved: buffed bins sit late instead of early.
    expect(delayed.dpsBins[5]).toBeCloseTo(150)
    expect(delayed.dpsBins[15]).toBeCloseTo(300)
    expect(eager.dpsBins[5]).toBeCloseTo(300)
  })
})

describe('dpsBins', () => {
  it('sums to total damage; instants land at cast end', () => {
    // Nuke busy 2.5, recast cadence 10 → casts at 0,10,20; instant lands
    // at cast END (castTime 2) → bins 2/12/22 carry 150 each.
    const a = ability('Nuke', { cast: 2, recovery: 0.5, recast: 8 })
    const r = simulate(config([a], 30))
    expect(r.dpsBins).toHaveLength(30)
    expect(r.dpsBins.reduce((s, v) => s + v, 0)).toBeCloseTo(r.totalDamage)
    expect(r.dpsBins[2]).toBeCloseTo(150)
    expect(r.dpsBins[12]).toBeCloseTo(150)
    expect(r.dpsBins[0]).toBe(0)
  })

  it('dot ticks land in their tick bins', () => {
    // Cast ends at 1; 4 ticks every 2s → bins 3,5,7,9 at 75 each.
    const a = ability('Burn', { cast: 1, recast: 100, components: [dot(50, 2, 8)] })
    const r = simulate(config([a], 20))
    expect(r.dpsBins.reduce((s, v) => s + v, 0)).toBeCloseTo(r.totalDamage)
    expect(r.dpsBins[3]).toBeCloseTo(75)
    expect(r.dpsBins[9]).toBeCloseTo(75)
  })

  it('auto-attack spreads uniformly across its bins', () => {
    const r = simulate(
      config([], 20, { autoAttack: true, stats: { primary_min: 50, primary_max: 100, primary_delay: 3 } }),
    )
    expect(r.dpsBins.reduce((s, v) => s + v, 0)).toBeCloseTo(r.totalDamage)
    expect(r.dpsBins[5]).toBeCloseTo(r.totalDamage / 20)
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
    expect(r.totalDamage).toBeCloseTo(400)
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
