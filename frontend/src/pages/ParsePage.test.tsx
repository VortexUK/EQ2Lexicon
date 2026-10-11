/**
 * ParsePage Allies/Pets split tests.
 *
 * The split predicate post-Phase-6 reads c.is_player directly (no more
 * cls / multi-word fallback heuristic). Bucket-promoted combatants
 * (is_player=true but cls=null) render in the Allies section identical
 * to Census-resolved players.
 *
 * Also here: the title colour per ACT outcome (1 win / 2 partial / 3 loss)
 * and the per-combatant Threat tab's null-kind placeholder.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import ParsePage from './ParsePage'

interface MockCombatant {
  id: number
  name: string
  ally: boolean
  is_player: boolean
  level: number | null
  guild_name: string | null
  cls: string | null
  duration_s?: number
  damage?: number
  damage_perc?: number
  dps?: number
  encdps?: number
  dps_percentile?: number | null
  dps_best_overall?: boolean
  hps_percentile?: number | null
  hps_best_overall?: boolean
  healed?: number
  enchps?: number
  heals?: number
  crit_heals?: number
  cure_dispels?: number
  power_drain?: number
  power_replenish?: number
  heals_taken?: number
  damage_taken?: number
  threat_delta?: number
  deaths?: number
  kills?: number
  crit_hits?: number
  crit_dam_perc?: number
  top_attacks?: unknown[]
  top_heals?: unknown[]
  top_cures?: unknown[]
  top_threats?: unknown[]
  damage_types?: unknown[]
}

const _DEFAULTS = {
  duration_s: 60,
  damage: 100,
  damage_perc: 50,
  dps: 1.0,
  encdps: 1.0,
  dps_percentile: null,
  dps_best_overall: false,
  hps_percentile: null,
  hps_best_overall: false,
  healed: 0,
  enchps: 0,
  heals: 0,
  crit_heals: 0,
  cure_dispels: 0,
  power_drain: 0,
  power_replenish: 0,
  heals_taken: 0,
  damage_taken: 0,
  threat_delta: 0,
  deaths: 0,
  kills: 0,
  crit_hits: 0,
  crit_dam_perc: 0,
  top_attacks: [],
  top_heals: [],
  top_cures: [],
  top_threats: [],
  damage_types: [],
}

function combatant(o: Partial<MockCombatant> & Pick<MockCombatant, 'id' | 'name' | 'ally' | 'is_player'>): MockCombatant {
  return {
    ..._DEFAULTS,
    level: null,
    guild_name: null,
    cls: null,
    ...o,
  } as MockCombatant
}

function mockFetch(combatants: MockCombatant[], successLevel = 1) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/api/zones/')) return { ok: false, status: 404, json: async () => ({}) }
      if (url.includes('/api/characters/lookup')) return { ok: true, status: 200, json: async () => ({ results: {} }) }
      if (url.includes('/api/classes')) return { ok: true, status: 200, json: async () => [] }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: 1,
          act_encid: 'x',
          title: 'Test Boss',
          zone: 'Z',
          started_at: 100,
          ended_at: 200,
          duration_s: 100,
          total_damage: 1000,
          encdps: 100,
          kills: 1,
          deaths: 0,
          success_level: successLevel,
          hidden: false,
          uploaded_by: 'tester',
          uploader_discord_id: null,
          uploader_display_name: null,
          combatants,
        }),
      }
    }) as unknown as typeof fetch,
  )
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/parse/1']}>
      <Routes>
        <Route path="/parse/:id" element={<ParsePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { vi.restoreAllMocks() })


describe('ParsePage Allies/Pets split', () => {
  it('renders is_player=true combatants in Allies', async () => {
    mockFetch([
      combatant({ id: 1, name: 'Alpha', ally: true, is_player: true, cls: 'Wizard' }),
      combatant({ id: 2, name: 'a krait warrior', ally: true, is_player: false }),
    ])
    renderPage()
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('a krait warrior')).toBeInTheDocument()
    expect(screen.getByText('Allies')).toBeInTheDocument()
    expect(screen.getByText('Pets')).toBeInTheDocument()
  })

  it('renders bucket-promoted players (is_player=true, cls=null) in Allies', async () => {
    mockFetch([
      combatant({ id: 1, name: 'Bob', ally: true, is_player: true, cls: null }),
    ])
    renderPage()
    expect(await screen.findByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('Allies')).toBeInTheDocument()
    expect(screen.queryByText('Pets')).not.toBeInTheDocument()
  })

  it('renders enemies in Enemies', async () => {
    mockFetch([
      combatant({ id: 1, name: 'Alpha', ally: true, is_player: true, cls: 'Wizard' }),
      combatant({ id: 2, name: 'Venekor', ally: false, is_player: false }),
    ])
    renderPage()
    expect(await screen.findByText('Venekor')).toBeInTheDocument()
    expect(screen.getByText('Enemies')).toBeInTheDocument()
  })
})

// ACT's success level: 1 = win, 2 = partial / unclear, 3 = loss. The title
// once showed 2 in red and 3 in amber (the two were the wrong way round).
describe('ParsePage title colour by outcome', () => {
  async function titleColor(successLevel: number): Promise<string> {
    mockFetch([combatant({ id: 1, name: 'Alpha', ally: true, is_player: true })], successLevel)
    renderPage()
    const title = await screen.findByRole('heading', { level: 1, name: 'Test Boss' })
    return title.style.color
  }

  it('shows a win (1) in the success colour', async () => {
    expect(await titleColor(1)).toBe('var(--color-success)')
  })

  it('shows a partial outcome (2) in the warning colour', async () => {
    expect(await titleColor(2)).toBe('var(--color-warning)')
  })

  it('shows a loss (3) in the danger colour', async () => {
    expect(await titleColor(3)).toBe('var(--color-danger)')
  })

  it('keeps the gold heading when the outcome is unknown (0)', async () => {
    expect(await titleColor(0)).toBe('var(--gold)')
  })
})

// Threat rows from EQ2Parser up to v0.5.7 (swing type 16) carry no `resist`, so
// `kind` arrives null; the ACT plugin's rows say 'Increase' / 'Decrease'.
describe('ParsePage Threat tab', () => {
  it('lists threat rows and shows a dash for a null kind', async () => {
    mockFetch([
      combatant({
        id: 1, name: 'Tankee', ally: true, is_player: true,
        top_threats: [
          { ability_name: 'Taunt', value: 50000, procs: 4, max_proc: 900, kind: null },
          { ability_name: 'Undeniable Malice', value: 27240, procs: 10, max_proc: 5000, kind: 'Increase' },
        ],
      }),
    ])
    renderPage()
    const name = await screen.findByText('Tankee')
    // The name link stops propagation; click the row itself to expand it.
    fireEvent.click(name.closest('.cursor-pointer')!)

    expect(screen.getByRole('button', { name: 'Threat' })).toBeEnabled()
    // No damage, heal or cure rows, so the panel lands on the Threat tab.
    const kindOf = (ability: string) => screen.getByText(ability).parentElement!.lastElementChild!.textContent
    expect(kindOf('Taunt')).toBe('—')
    expect(kindOf('Undeniable Malice')).toBe('Increase')
  })

  it('keeps the Threat tab disabled when a combatant has no threat rows', async () => {
    mockFetch([combatant({ id: 1, name: 'Tankee', ally: true, is_player: true })])
    renderPage()
    const name = await screen.findByText('Tankee')
    fireEvent.click(name.closest('.cursor-pointer')!)
    expect(screen.getByRole('button', { name: 'Threat' })).toBeDisabled()
  })
})
