import { describe, it, expect } from 'vitest'
import { STAT_GROUPS, WEAPON_SLOTS, buildSlotMap, tierStyle, fmtStat, LEFT_SLOTS, RIGHT_SLOTS, CONSUMABLE_SLOTS } from './characterSheet'
import type { CharacterStats, EquipmentSlot } from './characterSheet'

/** Every key of CharacterStats, maintained by hand as the test contract —
 * a new stats field must be routed somewhere deliberate or the test fails. */
const ALL_STAT_KEYS: (keyof CharacterStats)[] = [
  'health_max', 'health_regen', 'power_max', 'power_regen', 'run_speed', 'status_points',
  'str_eff', 'sta_eff', 'agi_eff', 'wis_eff', 'int_eff',
  'armor', 'avoidance', 'block_chance', 'parry',
  'mit_physical', 'mit_elemental', 'mit_noxious', 'mit_arcane',
  'potency', 'crit_chance', 'crit_bonus', 'fervor', 'dps',
  'double_attack', 'ability_doublecast', 'attack_speed', 'strikethrough',
  'accuracy', 'ability_mod', 'weapon_damage_bonus', 'flurry', 'lethality', 'toughness',
  'reuse_speed', 'casting_speed', 'recovery_speed',
  'primary_min', 'primary_max', 'primary_delay',
  'secondary_min', 'secondary_max', 'secondary_delay',
  'ranged_min', 'ranged_max', 'ranged_delay',
]

// Shown in the GeneralBanner, not the stat-group panel — intentionally
// excluded from STAT_GROUPS.
const BANNER_KEYS = new Set(['health_max', 'health_regen', 'power_max', 'power_regen', 'run_speed', 'status_points'])

describe('STAT_GROUPS', () => {
  it('routes every stats key exactly once (groups ∪ weapon ∪ banner)', () => {
    const groupKeys = STAT_GROUPS.flatMap(g => g.rows.map(r => r.key))
    const weaponKeys = WEAPON_SLOTS.flatMap(w => [w.min, w.max, w.delay])
    const all = [...groupKeys, ...weaponKeys]
    // no duplicates
    expect(new Set(all).size).toBe(all.length)
    // nothing missing
    for (const key of ALL_STAT_KEYS) {
      if (BANNER_KEYS.has(key)) continue
      expect(all, `stats key '${key}' is not routed to any display group`).toContain(key)
    }
    // nothing extra (typo'd key would be caught by the type, but ordering sanity)
    expect(all.length).toBe(ALL_STAT_KEYS.length - BANNER_KEYS.size)
  })

  it('group titles are the canonical four', () => {
    expect(STAT_GROUPS.map(g => g.title)).toEqual(['Attributes', 'Defense', 'Combat', 'Casting'])
  })
})

describe('buildSlotMap', () => {
  const slot = (display: string, name = 'Item'): EquipmentSlot =>
    ({ slot: display, name, item_id: '1', icon_id: null, tier: null, adorn_slots: [] })

  it('maps multi-slots positionally (rings, ears, wrists, charms)', () => {
    const map = buildSlotMap([
      slot('Finger', 'Ring A'), slot('Finger', 'Ring B'),
      slot('Ear', 'Ear A'), slot('Ear', 'Ear B'),
      slot('Charm', 'Charm A'), slot('Charm', 'Charm B'),
      slot('Head', 'Helm'),
    ])
    expect(map.get('left_ring')?.name).toBe('Ring A')
    expect(map.get('right_ring')?.name).toBe('Ring B')
    expect(map.get('ears')?.name).toBe('Ear A')
    expect(map.get('ears2')?.name).toBe('Ear B')
    expect(map.get('activate1')?.name).toBe('Charm A')
    expect(map.get('activate2')?.name).toBe('Charm B')
    expect(map.get('head')?.name).toBe('Helm')
  })

  it('covers every canonical slot key used by the paperdoll lists', () => {
    // Sanity: the slot keys the layouts reference are producible by buildSlotMap
    const producible = new Set([
      'activate1', 'activate2', 'left_ring', 'right_ring', 'ears', 'ears2',
      'left_wrist', 'right_wrist',
      'primary', 'secondary', 'ranged', 'head', 'chest', 'shoulders', 'forearms',
      'hands', 'legs', 'feet', 'waist', 'neck', 'cloak', 'food', 'drink',
    ])
    for (const [, key] of [...LEFT_SLOTS, ...RIGHT_SLOTS, ...CONSUMABLE_SLOTS]) {
      expect(producible, `paperdoll references unproducible slot key '${key}'`).toContain(key)
    }
  })
})

describe('tierStyle / fmtStat', () => {
  it('resolves compound tiers to the last recognised word', () => {
    expect(tierStyle('MASTERCRAFTED FABLED').color).toBe('var(--rarity-fabled)')
    expect(tierStyle('fabled').color).toBe('var(--rarity-fabled)')
    expect(tierStyle(null).color).toBe('var(--text)')
    expect(tierStyle('UNKNOWN THING').color).toBe('var(--text)')
  })

  it('formats stats per Fmt', () => {
    expect(fmtStat(12345, 'int')).toBe((12345).toLocaleString())
    expect(fmtStat(12.34, 'pct1')).toBe('12.3%')
    expect(fmtStat(12.34, 'dec1')).toBe('12.3')
    expect(fmtStat(12.6, 'pct')).toBe('13%')
  })
})
