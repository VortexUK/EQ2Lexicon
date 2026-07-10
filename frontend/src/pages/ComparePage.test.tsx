import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import ComparePage from './ComparePage'
import type { Character } from './characterSheet'

// ── react-router-dom partial mock: real router, swappable useSearchParams ────
// Default passthrough; the throws-test flips `throwOnSet` to simulate the
// Firefox History-API throttle (per CLAUDE.md this test is mandatory).
// The guarded setter identity is memoised on the underlying setter — a fresh
// identity per render would re-fire ComparePage's mirror effect (which deps on
// setSearchParams) every render and loop forever.
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

const stats = (over: Record<string, number | null> = {}) => ({
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
  ...over,
})

const mkChar = (name: string, cls: string, over: Partial<Character> = {}): Character => ({
  id: name,
  name,
  level: 70,
  cls,
  race: 'Human',
  gender: 'Male',
  deity: null,
  aa_count: 100,
  world: 'Varsoon',
  ts_class: null,
  ts_level: null,
  guild_name: 'Exordium',
  ilvl: 50,
  stats: stats(),
  equipment: [],
  stale: false,
  ...over,
})

interface StubOpts {
  chars?: Record<string, Character>
  favorites?: unknown[]
  searchResults?: { name: string; cls: string | null; level: number | null; guild_name: string | null }[]
  aas?: Record<string, unknown>
}

function stubFetch(opts: StubOpts = {}) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(url)
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
    if (url.includes('/api/classes')) return ok([])
    if (url.includes('/api/favorites')) return ok({ favorites: opts.favorites ?? [] })
    if (url.includes('/api/characters/search')) return ok({ results: opts.searchResults ?? [], total: 0, source: 'local' })
    if (url.includes('/api/aa/config')) return ok({ xpac: 'x', aa_cap: 100, tradeskill_aa_cap: 0, unlocked_tree_types: [] })
    const aaMatch = url.match(/\/api\/character\/([^/]+)\/aas/)
    if (aaMatch) {
      const name = decodeURIComponent(aaMatch[1])
      return ok(opts.aas?.[name] ?? { character_name: name, total_spent: 0, trees: [], profiles: [] })
    }
    if (url.match(/\/api\/aa\/tree\//)) return ok({ tree_id: 1, tree_name: 'T', tree_type: 'class', nodes: [] })
    const charMatch = url.match(/\/api\/character\/([^/]+)$/)
    if (charMatch) {
      const name = decodeURIComponent(charMatch[1])
      const char = opts.chars?.[name]
      return char ? ok(char) : { ok: false, status: 404, json: async () => ({ detail: 'not found' }) }
    }
    return ok({})
  }) as unknown as typeof fetch)
  return calls
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <ComparePage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  throwOnSet = false
})

// NOTE: characterCache + aaCache are module-level and persist across tests in
// this file — every test uses unique character names for isolation.

describe('ComparePage', () => {
  it('initialises both sides from ?a=&b= and renders both columns', async () => {
    stubFetch({ chars: { Aldun: mkChar('Aldun', 'Templar'), Brenlo: mkChar('Brenlo', 'Templar') } })
    renderAt('/compare?a=Aldun&b=Brenlo')
    // Names appear in the chip + Δ legend + stats header — any presence is fine.
    expect((await screen.findAllByText('Aldun')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('Brenlo')).length).toBeGreaterThan(0)
    // Both ok → overview strip + tabs appear
    expect(await screen.findByText('Stats')).toBeInTheDocument()
    expect(screen.getByText('Gear')).toBeInTheDocument()
    expect(screen.getByText('AAs')).toBeInTheDocument()
  })

  it('drops b on a case-insensitive self-compare deep link', async () => {
    stubFetch({ chars: { Cedric: mkChar('Cedric', 'Templar') } })
    renderAt('/compare?a=Cedric&b=cedric')
    expect(await screen.findByText('Cedric')).toBeInTheDocument()
    // Side B stays a picker; the second-character prompt shows
    expect(await screen.findByText(/Pick a second character/)).toBeInTheDocument()
  })

  it('offers favourites first in the picker and disables the other side\'s pick', async () => {
    stubFetch({
      chars: { Davor: mkChar('Davor', 'Templar') },
      favorites: [
        { character_name: 'Davor', world: 'Varsoon', created_at: 1, level: 70, cls: 'Templar', ts_class: null, ts_level: null, guild_name: null },
        { character_name: 'Elwin', world: 'Varsoon', created_at: 2, level: 60, cls: 'Fury', ts_class: null, ts_level: null, guild_name: null },
      ],
    })
    renderAt('/compare?a=Davor')
    expect((await screen.findAllByText('Davor')).length).toBeGreaterThan(0)
    // Side B's picker lists favourites (Favourites section header present)
    expect(await screen.findByText('Favourites')).toBeInTheDocument()
    const elwin = await screen.findByRole('button', { name: /Elwin/ })
    expect(elwin).toBeEnabled()
    // Davor is already side A → its favourite row is disabled
    const davorRows = screen.getAllByRole('button', { name: /Davor/ })
    expect(davorRows.some(b => (b as HTMLButtonElement).disabled)).toBe(true)
  })

  it('selecting a favourite fills side B', async () => {
    stubFetch({
      chars: { Fenrik: mkChar('Fenrik', 'Templar'), Gilda: mkChar('Gilda', 'Fury') },
      favorites: [
        { character_name: 'Gilda', world: 'Varsoon', created_at: 1, level: 60, cls: 'Fury', ts_class: null, ts_level: null, guild_name: null },
      ],
    })
    renderAt('/compare?a=Fenrik')
    fireEvent.click(await screen.findByRole('button', { name: /Gilda/ }))
    expect(await screen.findByText('Stats')).toBeInTheDocument() // both ok now
  })

  it('AA tab: different subclass shows the gate note and fires NO /aas fetch', async () => {
    const calls = stubFetch({
      chars: { Haldir: mkChar('Haldir', 'Templar'), Iskra: mkChar('Iskra', 'Inquisitor') },
    })
    renderAt('/compare?a=Haldir&b=Iskra&tab=aas')
    expect(await screen.findByText(/only available when both characters are the same subclass/)).toBeInTheDocument()
    expect(calls.filter(u => u.includes('/aas'))).toHaveLength(0)
  })

  it('AA tab: same subclass loads AA data and shows tree summaries', async () => {
    stubFetch({
      chars: { Jorun: mkChar('Jorun', 'Templar'), Kaeli: mkChar('Kaeli', 'Templar') },
      aas: {
        Jorun: { character_name: 'Jorun', total_spent: 10, trees: [{ tree_id: 1, tree_type: 'class', tree_name: 'Cleric', spent: { '10': 5 }, total_spent: 10 }], profiles: [] },
        Kaeli: { character_name: 'Kaeli', total_spent: 4, trees: [{ tree_id: 1, tree_type: 'class', tree_name: 'Cleric', spent: { '10': 2 }, total_spent: 4 }], profiles: [] },
      },
    })
    renderAt('/compare?a=Jorun&b=Kaeli&tab=aas')
    expect(await screen.findByText('Cleric')).toBeInTheDocument()
    expect(await screen.findByText(/1 node differs?|1 nodes? differ/)).toBeInTheDocument()
  })

  it('swap button exchanges the two sides', async () => {
    stubFetch({ chars: { Lorin: mkChar('Lorin', 'Templar'), Mira: mkChar('Mira', 'Fury') } })
    renderAt('/compare?a=Lorin&b=Mira')
    await screen.findByText('Stats')
    // Scope to the legend pill itself (unique rounded-full span), not ancestors.
    const legend = () =>
      screen.getByText(
        (_, el) => el?.tagName === 'SPAN' && (el?.className?.includes('rounded-full') ?? false) && (el?.textContent?.includes('Δ =') ?? false),
      )
    expect(legend().textContent).toContain('Mira − Lorin')
    fireEvent.click(screen.getByRole('button', { name: /swap/i }))
    await waitFor(() => expect(legend().textContent).toContain('Lorin − Mira'))
  })

  it('survives setSearchParams throwing (History API throttle) — UI still responds', async () => {
    throwOnSet = true
    stubFetch({ chars: { Nerys: mkChar('Nerys', 'Templar'), Osric: mkChar('Osric', 'Templar') } })
    renderAt('/compare?a=Nerys&b=Osric')
    await screen.findByText('Stats')
    // Tab switching mutates React state + mirrors to the URL; the mirror throws
    // but state must win — the Gear tab renders anyway.
    fireEvent.click(screen.getByText('Gear'))
    expect(await screen.findByText(/slots differ/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('AAs'))
    expect(await screen.findByText(/Adventure AA:/)).toBeInTheDocument()
  })
})
