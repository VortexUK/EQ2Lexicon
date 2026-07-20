import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import CharacterPage from './CharacterPage'
import type { Character, EquipmentSlot, GearSet } from './characterSheet'

// ── Hook mocks: no EventSource / server bootstrap in jsdom ───────────────────

vi.mock('../hooks/useCensusStream', () => ({
  useCensusStream: () => ({ subscribe: () => () => {} }),
}))
vi.mock('../hooks/useServer', () => ({
  useServer: () => ({ maxLevel: 70 }),
}))

// ── react-router-dom partial mock (ComparePage.test.tsx pattern) ─────────────
// The throws-test flips `throwOnSet` to simulate the Firefox History-API
// throttle — per CLAUDE.md, actively-clicked URL-mirrored state (the ?set=
// gear-set pills) must survive setSearchParams throwing.
let throwOnSet = false
vi.mock('react-router-dom', async importOriginal => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  const { useCallback } = await import('react')
  return {
    ...actual,
    useSearchParams: (...args: Parameters<typeof actual.useSearchParams>) => {
      const [params, setParams] = actual.useSearchParams(...args)
      const guarded = useCallback(
        ((...setArgs: Parameters<typeof setParams>) => {
          if (throwOnSet) throw new DOMException('history quota exhausted', 'SecurityError')
          return setParams(...setArgs)
        }) as typeof setParams,
        [setParams],
      )
      return [params, guarded] as const
    },
  }
})

// ── Fixtures ─────────────────────────────────────────────────────────────────

const stats = () => ({
  health_max: 10000, health_regen: null, power_max: 5000, power_regen: null,
  run_speed: null, status_points: null,
  str_eff: 100, sta_eff: 100, agi_eff: 100, wis_eff: 100, int_eff: 100,
  armor: 5000, avoidance: null, block_chance: null, parry: null,
  mit_physical: null, mit_elemental: null, mit_noxious: null, mit_arcane: null,
  potency: 50, crit_chance: null, crit_bonus: null, fervor: null, dps: null,
  double_attack: null, ability_doublecast: null, attack_speed: null,
  strikethrough: null, accuracy: null, ability_mod: null,
  weapon_damage_bonus: null, flurry: null, lethality: null, toughness: null,
  reuse_speed: null, casting_speed: null, recovery_speed: null,
  primary_min: null, primary_max: null, primary_delay: null,
  secondary_min: null, secondary_max: null, secondary_delay: null,
  ranged_min: null, ranged_max: null, ranged_delay: null,
})

const slot = (slotName: string, itemName: string, itemId: string | null = null): EquipmentSlot => ({
  slot: slotName,
  name: itemName,
  item_id: itemId, // null → no tooltip prefetch in jsdom
  icon_id: null,
  tier: 'FABLED',
  adorn_slots: [],
})

const mkChar = (name: string, equipment?: EquipmentSlot[]): Character => ({
  id: name,
  name,
  level: 70,
  cls: 'Templar',
  race: 'Human',
  gender: 'Male',
  deity: null,
  aa_count: 100,
  world: 'Varsoon',
  ts_class: null,
  ts_level: null,
  guild_name: null,
  ilvl: 50,
  stats: stats(),
  equipment: equipment ?? [slot('Head', 'Current Helm')],
  stale: false,
})

const TANK_SET: GearSet = {
  name: 'Tank',
  ilvl: 62,
  stat_deltas: { potency: -5.5, block_chance: 12.3, health_max: 800 },
  equipment: [slot('Head', 'Tanky Helm')],
}
const DPS_SET: GearSet = { name: 'DPS', ilvl: 58, stat_deltas: {}, equipment: [slot('Head', 'Deeps Helm')] }

function stubFetch(opts: { char: Character; sets?: GearSet[]; items?: Record<string, unknown> }) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (url.includes('/gear-sets')) {
      return ok({ character_name: opts.char.name, sets: opts.sets ?? [] })
    }
    const itemMatch = url.match(/\/api\/item\/(\d+)/)
    if (itemMatch) {
      const item = opts.items?.[itemMatch[1]]
      return item ? ok(item) : { ok: false, status: 404, json: async () => ({}) }
    }
    if (url.includes('/favorite')) return ok({ count: 0, favorited_by_me: false })
    if (url.includes('/api/claim/me')) return ok({ approved: [], pending: [] })
    if (url.includes('/api/config')) return ok({})
    if (url.includes('/api/classes')) return ok([])
    if (url.match(/\/api\/character\/[^/]+$/)) return ok(opts.char)
    return ok({})
  }) as unknown as typeof fetch)
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/character/:name" element={<CharacterPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  throwOnSet = false
})

// NOTE: characterCache is module-level and persists across tests in this file
// — every test uses a unique character name for isolation.

describe('CharacterPage gear-set pills', () => {
  it('renders a pill per saved set plus Current, showing current gear by default', async () => {
    stubFetch({ char: mkChar('Pillsa'), sets: [DPS_SET, TANK_SET] })
    renderAt('/character/Pillsa')
    expect(await screen.findByRole('button', { name: 'Current' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'DPS' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tank' })).toBeInTheDocument()
    expect(screen.getByText('Current Helm')).toBeInTheDocument()
    expect(screen.queryByText('Tanky Helm')).not.toBeInTheDocument()
  })

  it('clicking a set pill swaps the paperdoll to that set and notes the approximation', async () => {
    stubFetch({ char: mkChar('Pillsb'), sets: [TANK_SET] })
    renderAt('/character/Pillsb')
    fireEvent.click(await screen.findByRole('button', { name: 'Tank' }))
    expect(await screen.findByText('Tanky Helm')).toBeInTheDocument()
    expect(screen.queryByText('Current Helm')).not.toBeInTheDocument()
    expect(screen.getByText(/stats are approximate/)).toBeInTheDocument()
    // Back to Current
    fireEvent.click(screen.getByRole('button', { name: 'Current' }))
    expect(await screen.findByText('Current Helm')).toBeInTheDocument()
  })

  it('a selected set approximates stats: base + delta with a signed chip', async () => {
    stubFetch({ char: mkChar('Pillsf'), sets: [TANK_SET] })
    renderAt('/character/Pillsf')
    // Live gear: Potency 50 (fixture), no Block Chance row (null), banner Health 10,000.
    fireEvent.click(await screen.findByRole('button', { name: 'Tank' }))
    // potency 50 − 5.5 → 44.5 with a −5.5 chip
    expect(await screen.findByText('44.5')).toBeInTheDocument()
    expect(screen.getByText('−5.5')).toBeInTheDocument()
    // block_chance had no live value → the delta alone renders the row
    expect(screen.getByText('12.3%')).toBeInTheDocument()
    // health_max is banner-only → surfaces in the "Health & Power" delta group
    expect(screen.getByText('Health & Power')).toBeInTheDocument()
    expect(screen.getByText('10,800')).toBeInTheDocument()
    // Back to Current → approximation gone
    fireEvent.click(screen.getByRole('button', { name: 'Current' }))
    expect(await screen.findByText('50.0')).toBeInTheDocument()
    expect(screen.queryByText('Health & Power')).not.toBeInTheDocument()
  })

  it('renders no pills for a character without saved sets', async () => {
    stubFetch({ char: mkChar('Pillsc'), sets: [] })
    renderAt('/character/Pillsc')
    expect(await screen.findByText('Current Helm')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Current' })).not.toBeInTheDocument()
  })

  it('applies a ?set= deep link once the set list arrives', async () => {
    stubFetch({ char: mkChar('Pillsd'), sets: [DPS_SET, TANK_SET] })
    renderAt('/character/Pillsd?set=Tank')
    expect(await screen.findByText('Tanky Helm')).toBeInTheDocument()
    const tankPill = screen.getByRole('button', { name: 'Tank' })
    expect(tankPill).toHaveAttribute('aria-pressed', 'true')
  })

  it('survives setSearchParams throwing (History API throttle) — pills still swap gear', async () => {
    throwOnSet = true
    stubFetch({ char: mkChar('Pillse'), sets: [TANK_SET] })
    renderAt('/character/Pillse')
    fireEvent.click(await screen.findByRole('button', { name: 'Tank' }))
    await waitFor(() => expect(screen.getByText('Tanky Helm')).toBeInTheDocument())
  })
})

// NOTE: the tooltip item cache is also module-level — unique item ids per test.

const itemDetail = (over: Record<string, unknown>) => ({
  quality: 'FABLED', stats: [], effects: [], adornment_slots: [], flags: [],
  extra_info: [], mitigation: null, set_name: null, set_bonuses: [],
  ...over,
})

describe('CharacterPage set bonuses', () => {
  it('lists worn sets with piece counts and marks active tiers', async () => {
    const bonuses = [
      { required_items: 2, effect: '+120 Stamina', lines: [] },
      { required_items: 5, effect: '+5.0 Potency', lines: ['Applies Enhance: Void Bane.'] },
    ]
    stubFetch({
      char: mkChar('Setsy', [
        slot('Head', 'Vault Helm', '9101'),
        slot('Chest', 'Vault Cuirass', '9102'),
        slot('Legs', 'Plain Legs', '9103'),
      ]),
      items: {
        9101: itemDetail({ id: '9101', name: 'Vault Helm', set_name: 'Vault Raiment', set_bonuses: bonuses }),
        9102: itemDetail({ id: '9102', name: 'Vault Cuirass', set_name: 'Vault Raiment', set_bonuses: bonuses }),
        9103: itemDetail({ id: '9103', name: 'Plain Legs' }),
      },
    })
    renderAt('/character/Setsy')
    expect(await screen.findByText('Set Bonuses')).toBeInTheDocument()
    expect(screen.getByText('Vault Raiment')).toBeInTheDocument()
    expect(screen.getByText(/— 2 equipped/)).toBeInTheDocument()
    // 2-piece tier active (success-coloured requirement), 5-piece inactive
    expect(screen.getByText('(2)').className).toContain('text-success')
    expect(screen.getByText('(5)').className).not.toContain('text-success')
    expect(screen.getByText(/\+120 Stamina/)).toBeInTheDocument()
    expect(screen.getByText(/\+5\.0 Potency/)).toBeInTheDocument()
    expect(screen.getByText('Applies Enhance: Void Bane.')).toBeInTheDocument()
  })

  it('renders nothing when no worn item belongs to a set', async () => {
    stubFetch({
      char: mkChar('Setless2', [slot('Head', 'Plain Helm', '9201')]),
      items: { 9201: itemDetail({ id: '9201', name: 'Plain Helm' }) },
    })
    renderAt('/character/Setless2')
    expect(await screen.findByText('Plain Helm')).toBeInTheDocument()
    expect(screen.queryByText('Set Bonuses')).not.toBeInTheDocument()
  })

  it('recomputes for the displayed saved set', async () => {
    const bonuses = [{ required_items: 2, effect: '+10 Block Chance', lines: [] }]
    stubFetch({
      char: mkChar('Setswap', [slot('Head', 'Plain Helm', '9301')]),
      sets: [{
        name: 'Tank',
        ilvl: 60,
        stat_deltas: {},
        equipment: [slot('Head', 'Bulwark Helm', '9302'), slot('Chest', 'Bulwark Chest', '9303')],
      }],
      items: {
        9301: itemDetail({ id: '9301', name: 'Plain Helm' }),
        9302: itemDetail({ id: '9302', name: 'Bulwark Helm', set_name: 'Bulwark of Stone', set_bonuses: bonuses }),
        9303: itemDetail({ id: '9303', name: 'Bulwark Chest', set_name: 'Bulwark of Stone', set_bonuses: bonuses }),
      },
    })
    renderAt('/character/Setswap')
    expect(await screen.findByText('Plain Helm')).toBeInTheDocument()
    // Wait for the pill row (gear-sets fetch resolved) BEFORE asserting the
    // section's absence — otherwise the assertion passes vacuously.
    const tankPill = await screen.findByRole('button', { name: 'Tank' })
    expect(screen.queryByText('Set Bonuses')).not.toBeInTheDocument()
    // Switch to the Tank set → its two Bulwark pieces activate the 2-piece bonus
    fireEvent.click(tankPill)
    expect(await screen.findByText('Set Bonuses')).toBeInTheDocument()
    expect(screen.getByText('Bulwark of Stone')).toBeInTheDocument()
    expect(screen.getByText('(2)').className).toContain('text-success')
    // Back to current gear → section disappears again
    fireEvent.click(screen.getByRole('button', { name: 'Current' }))
    await waitFor(() => expect(screen.queryByText('Set Bonuses')).not.toBeInTheDocument())
  })
})
