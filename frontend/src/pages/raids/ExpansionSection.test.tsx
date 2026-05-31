/**
 * ExpansionSection render-state + admin-interaction tests.
 *
 * Auth gate is exercised via the `authOverride` prop so we don't have to
 * mock `/api/auth/me`. Fetch is mocked via vi.stubGlobal to mirror the
 * DungeonsCard.test.tsx pattern.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

import { ExpansionSection } from './ExpansionSection'
import type { AuthState, User } from '../../hooks/useAuth'
import type { Zone } from './types'

// ── Auth fixtures ─────────────────────────────────────────────────────────────

const _USER_BASE: User = {
  id: 'u1',
  username: 'tester',
  global_name: 'Tester',
  avatar: null,
  is_admin: false,
  access_status: 'approved',
  static_roles: [],
}

const REGULAR: AuthState = { status: 'authenticated', user: { ..._USER_BASE } }
const ADMIN: AuthState = { status: 'authenticated', user: { ..._USER_BASE, is_admin: true } }

// ── Zone fixture helper ───────────────────────────────────────────────────────

function zone(name: string, overrides: Partial<Zone> = {}): Zone {
  return {
    name,
    expansion_short: 'RoK',
    expansion_name: 'Rise of Kunark',
    expansion_year: 2007,
    types: ['raid_x4'],
    aliases: [],
    wiki_url: null,
    is_contested: false,
    is_instance: true,
    is_openworld: false,
    bosses: [],
    ...overrides,
  }
}

// ── Fetch mock ────────────────────────────────────────────────────────────────

interface Category { name: string; position: number }

interface ReorderZonesBody {
  expansion: string
  zones: { name: string; category: string | null; position: number }[]
}

interface ReorderCategoriesBody {
  expansion: string
  categories: { name: string; position: number }[]
}

interface MockState {
  zones: Zone[]
  availableZones: Zone[]
  categories: Category[]
  mutations: { method: string; url: string; body?: unknown }[]
  failNextRemove?: boolean
  failNextCategoryCreate?: boolean
}

function installFetchMock(state: MockState) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = (init?.method ?? 'GET').toUpperCase()
      const parsedBody: unknown = init?.body
        ? (() => { try { return JSON.parse(String(init.body)) } catch { return undefined } })()
        : undefined

      if (method === 'GET' && url.includes('/api/raids/zones/available')) {
        return { ok: true, status: 200, json: async () => state.availableZones }
      }
      if (method === 'GET' && url.includes('/api/raids/categories')) {
        return { ok: true, status: 200, json: async () => state.categories }
      }
      if (method === 'GET' && url.includes('/api/raids/zones')) {
        return { ok: true, status: 200, json: async () => state.zones }
      }
      if (method === 'GET' && url.includes('/api/raid-progress')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ guild_name: null, character_name: null, killed_encounters: {} }),
        }
      }
      // Dungeons card fetches both `type=dungeon` and `type=` — return empty.
      if (method === 'GET' && url.includes('/api/zones')) {
        return { ok: true, status: 200, json: async () => ({ expansion: 'RoK', type: '', zones: [] }) }
      }
      if (method === 'GET' && url.includes('/api/auth/me')) {
        return { ok: false, status: 401, json: async () => ({}) }
      }

      if (method === 'PUT' && url.includes('/api/raids/zones/reorder')) {
        state.mutations.push({ method, url, body: parsedBody })
        const body = parsedBody as ReorderZonesBody
        // Apply ordering to state.zones so the next refetch returns the new order.
        const byName = new Map(state.zones.map(z => [z.name, z]))
        const updated: Zone[] = []
        for (const e of body.zones) {
          const z = byName.get(e.name)
          if (z) updated.push({ ...z, category: e.category, position: e.position })
        }
        state.zones = updated
        return { ok: true, status: 200, json: async () => ({ expansion: body.expansion, reordered: body.zones.length }) }
      }
      if (method === 'PUT' && url.includes('/api/raids/categories/reorder')) {
        state.mutations.push({ method, url, body: parsedBody })
        const body = parsedBody as ReorderCategoriesBody
        state.categories = body.categories.map(c => ({ name: c.name, position: c.position }))
        return { ok: true, status: 200, json: async () => ({ expansion: body.expansion, reordered: body.categories.length }) }
      }
      if (method === 'POST' && url.includes('/api/raids/zones/')) {
        const m = decodeURIComponent(url.match(/\/api\/raids\/zones\/(.+)$/)?.[1] ?? '')
        state.mutations.push({ method, url })
        const z = state.availableZones.find(x => x.name === m)
        if (z) {
          state.zones = [...state.zones, z]
          state.availableZones = state.availableZones.filter(x => x.name !== m)
        }
        return { ok: true, status: 200, json: async () => z ?? {} }
      }
      if (method === 'DELETE' && url.includes('/api/raids/zones/')) {
        if (state.failNextRemove) {
          state.failNextRemove = false
          return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) }
        }
        const m = decodeURIComponent(url.match(/\/api\/raids\/zones\/(.+)$/)?.[1] ?? '')
        state.mutations.push({ method, url })
        state.zones = state.zones.filter(z => z.name !== m)
        return { ok: true, status: 200, json: async () => ({ zone_name: m, removed: true }) }
      }
      if (method === 'DELETE' && url.includes('/api/raids/expansions/')) {
        state.mutations.push({ method, url })
        return { ok: true, status: 200, json: async () => ({ removed: true }) }
      }
      if (method === 'POST' && url.includes('/api/raids/categories')) {
        state.mutations.push({ method, url, body: parsedBody })
        if (state.failNextCategoryCreate) {
          state.failNextCategoryCreate = false
          return { ok: false, status: 409, json: async () => ({ detail: 'already exists' }) }
        }
        const body = parsedBody as { name: string }
        const newPos = state.categories.length
        state.categories = [...state.categories, { name: body.name, position: newPos }]
        return { ok: true, status: 200, json: async () => ({ expansion: 'RoK', name: body.name }) }
      }
      if (method === 'DELETE' && url.includes('/api/raids/categories')) {
        const nameMatch = url.match(/[?&]name=([^&]*)/)
        const catName = nameMatch ? decodeURIComponent(nameMatch[1]) : ''
        state.mutations.push({ method, url })
        state.categories = state.categories.filter(c => c.name !== catName)
        state.zones = state.zones.map(z => z.category === catName ? { ...z, category: null } : z)
        return { ok: true, status: 200, json: async () => ({ expansion: 'RoK', name: catName, removed: true }) }
      }

      return { ok: false, status: 404, json: async () => ({ detail: 'not mocked' }) }
    }) as unknown as typeof fetch,
  )
}

function renderSection(opts: {
  zones: Zone[]
  availableZones?: Zone[]
  categories?: Category[]
  auth: AuthState
  isOpen?: boolean
  onToggle?: () => void
  onExpansionRemoved?: () => void
}) {
  const state: MockState = {
    zones: opts.zones,
    availableZones: opts.availableZones ?? [],
    categories: opts.categories ?? [],
    mutations: [],
  }
  installFetchMock(state)
  const result = render(
    <MemoryRouter>
      <ExpansionSection
        expansion={{ short: 'RoK', name: 'Rise of Kunark' }}
        isOpen={opts.isOpen ?? true}
        onToggle={opts.onToggle ?? (() => {})}
        isCurrent={false}
        killedByZone={{}}
        hasGuild={false}
        onExpansionRemoved={opts.onExpansionRemoved ?? (() => {})}
        authOverride={opts.auth}
      />
    </MemoryRouter>,
  )
  return { ...result, state }
}

beforeEach(() => {
  vi.restoreAllMocks()
  // window.confirm always returns true so tests don't get stuck on prompts.
  vi.stubGlobal('confirm', vi.fn(() => true))
})

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('ExpansionSection rendering', () => {
  it('renders raid zones from the fetched response', async () => {
    renderSection({
      zones: [zone("Veeshan's Peak"), zone("Trakanon's Lair")],
      auth: REGULAR,
    })
    expect(await screen.findByText("Veeshan's Peak")).toBeInTheDocument()
    expect(await screen.findByText("Trakanon's Lair")).toBeInTheDocument()
  })

  it('shows empty state when zones list is empty', async () => {
    renderSection({ zones: [], auth: REGULAR })
    expect(await screen.findByText(/No raid zones added yet/i)).toBeInTheDocument()
  })
})

describe('ExpansionSection admin affordances', () => {
  it('does NOT show "Add raid zone" button for non-admin', async () => {
    renderSection({ zones: [zone("Veeshan's Peak")], auth: REGULAR })
    await screen.findByText("Veeshan's Peak")
    expect(screen.queryByRole('button', { name: /Add raid zone/i })).not.toBeInTheDocument()
  })

  it('shows "Add raid zone" button for admin', async () => {
    renderSection({ zones: [zone("Veeshan's Peak")], auth: ADMIN })
    await screen.findByText("Veeshan's Peak")
    expect(screen.getByRole('button', { name: /Add raid zone/i })).toBeInTheDocument()
  })

  it('admin trash on a zone card DELETEs it and removes from list', async () => {
    const { state } = renderSection({
      zones: [zone("Veeshan's Peak")],
      auth: ADMIN,
    })
    await screen.findByText("Veeshan's Peak")
    const trash = screen.getByRole('button', { name: /Remove Veeshan's Peak from \/raids/i })
    await userEvent.click(trash)
    await waitFor(() => {
      expect(state.mutations.some(m =>
        m.method === 'DELETE' && m.url.includes('/api/raids/zones/'),
      )).toBe(true)
    })
  })

  it('admin trash on expansion header calls onExpansionRemoved', async () => {
    const onRemoved = vi.fn()
    renderSection({
      zones: [],
      auth: ADMIN,
      onExpansionRemoved: onRemoved,
    })
    const trash = screen.getByRole('button', { name: /Remove RoK expansion from \/raids/i })
    await userEvent.click(trash)
    await waitFor(() => expect(onRemoved).toHaveBeenCalled())
  })

  it('"Add raid zone" opens picker and POSTs on selection', async () => {
    const { state } = renderSection({
      zones: [],
      availableZones: [zone("Veeshan's Peak"), zone("Trakanon's Lair")],
      auth: ADMIN,
    })
    await userEvent.click(screen.getByRole('button', { name: /Add raid zone/i }))
    // Picker modal renders with the available zones.
    const pickerOption = await screen.findByRole('button', { name: /Veeshan's Peak/i })
    await userEvent.click(pickerOption)
    await waitFor(() => {
      expect(state.mutations.some(m =>
        m.method === 'POST' && m.url.includes('/api/raids/zones/'),
      )).toBe(true)
    })
  })

  it('shows "+ Add category" button for admin', async () => {
    renderSection({ zones: [zone("Veeshan's Peak")], auth: ADMIN })
    await screen.findByText("Veeshan's Peak")
    expect(screen.getByRole('button', { name: /\+ Add category/i })).toBeInTheDocument()
  })

  it('does NOT show "+ Add category" button for non-admin', async () => {
    renderSection({ zones: [zone("Veeshan's Peak")], auth: REGULAR })
    await screen.findByText("Veeshan's Peak")
    expect(screen.queryByRole('button', { name: /\+ Add category/i })).not.toBeInTheDocument()
  })

  it('"+ Add category" prompts for name and POSTs to /api/raids/categories', async () => {
    vi.stubGlobal('prompt', vi.fn(() => 'Tier 1'))
    const { state } = renderSection({ zones: [zone("Veeshan's Peak")], auth: ADMIN })
    await screen.findByText("Veeshan's Peak")
    await userEvent.click(screen.getByRole('button', { name: /\+ Add category/i }))
    await waitFor(() => {
      expect(state.mutations.some(m =>
        m.method === 'POST' && m.url.includes('/api/raids/categories'),
      )).toBe(true)
    })
  })

  it('"+ Add category" does nothing when prompt is cancelled', async () => {
    vi.stubGlobal('prompt', vi.fn(() => null))
    const { state } = renderSection({ zones: [zone("Veeshan's Peak")], auth: ADMIN })
    await screen.findByText("Veeshan's Peak")
    await userEvent.click(screen.getByRole('button', { name: /\+ Add category/i }))
    // No mutations should be fired.
    expect(state.mutations.filter(m => m.method === 'POST' && m.url.includes('categories'))).toHaveLength(0)
  })
})

// ── Categories + lane layout ──────────────────────────────────────────────────

describe('ExpansionSection lanes', () => {
  it('renders named lanes in category order with no "Uncategorised" header label', async () => {
    renderSection({
      zones: [
        zone("Veeshan's Peak", { category: null, position: 0 }),
        zone('Sebilis',                   { category: 'Wing B', position: 0 }),
        zone("Trakanon's Lair", { category: 'Wing A', position: 0 }),
      ],
      categories: [
        { name: 'Wing A', position: 0 },
        { name: 'Wing B', position: 1 },
      ],
      auth: REGULAR,
    })
    // Named lane headers render — no "Uncategorised" label ever appears.
    expect(await screen.findByText('Wing A')).toBeInTheDocument()
    expect(screen.getByText('Wing B')).toBeInTheDocument()
    expect(screen.queryByText('Uncategorised')).not.toBeInTheDocument()
    // Spot-check: each zone in its lane.
    expect(await screen.findByText("Veeshan's Peak")).toBeInTheDocument()
    expect(await screen.findByText('Sebilis')).toBeInTheDocument()
    expect(await screen.findByText("Trakanon's Lair")).toBeInTheDocument()
  })

  it('sorts zones within a lane by position', async () => {
    renderSection({
      zones: [
        zone('Beta',  { category: null, position: 1 }),
        zone('Alpha', { category: null, position: 0 }),
        zone('Gamma', { category: null, position: 2 }),
      ],
      auth: REGULAR,
    })
    await screen.findByText('Alpha')
    // Find all zone-heading nodes and assert order.
    const items = screen.getAllByText(/Alpha|Beta|Gamma/)
    const names = items.map(n => n.textContent)
    // Alpha precedes Beta precedes Gamma.
    const ai = names.indexOf('Alpha')
    const bi = names.indexOf('Beta')
    const gi = names.indexOf('Gamma')
    expect(ai).toBeLessThan(bi)
    expect(bi).toBeLessThan(gi)
  })

  it('never shows "Uncategorised" label — empty NULL lane is invisible', async () => {
    renderSection({
      zones: [zone('Alpha', { category: 'Wing A', position: 0 })],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: REGULAR,
    })
    await screen.findByText('Alpha')
    expect(screen.queryByText('Uncategorised')).not.toBeInTheDocument()
  })

  it('empty Uncategorised lane is invisible even for admin', async () => {
    renderSection({
      zones: [zone('Alpha', { category: 'Wing A', position: 0 })],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: ADMIN,
    })
    await screen.findByText('Alpha')
    expect(screen.queryByText('Uncategorised')).not.toBeInTheDocument()
  })

  it('non-admin does NOT see drag handles', async () => {
    renderSection({
      zones: [
        zone('Alpha', { category: null, position: 0 }),
        zone('Beta',  { category: 'Wing A', position: 0 }),
      ],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: REGULAR,
    })
    await screen.findByText('Alpha')
    expect(screen.queryByRole('button', { name: /Drag Alpha to reorder/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Drag category Wing A to reorder/i })).not.toBeInTheDocument()
  })

  it('admin sees drag handles on zone cards and named-lane headers', async () => {
    renderSection({
      zones: [zone('Alpha', { category: 'Wing A', position: 0 })],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: ADMIN,
    })
    await screen.findByText('Alpha')
    expect(screen.getByRole('button', { name: /Drag Alpha to reorder/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Drag category Wing A to reorder/i })).toBeInTheDocument()
  })

  it('admin sees trash icon on named-lane header', async () => {
    renderSection({
      zones: [zone('Alpha', { category: 'Wing A', position: 0 })],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: ADMIN,
    })
    await screen.findByText('Alpha')
    expect(screen.getByRole('button', { name: /Delete category Wing A/i })).toBeInTheDocument()
  })

  it('non-admin does NOT see trash icon on named-lane header', async () => {
    renderSection({
      zones: [zone('Alpha', { category: 'Wing A', position: 0 })],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: REGULAR,
    })
    await screen.findByText('Alpha')
    expect(screen.queryByRole('button', { name: /Delete category Wing A/i })).not.toBeInTheDocument()
  })

  it('lane header trash confirms then DELETEs the category', async () => {
    const { state } = renderSection({
      zones: [zone('Alpha', { category: 'Wing A', position: 0 })],
      categories: [{ name: 'Wing A', position: 0 }],
      auth: ADMIN,
    })
    await screen.findByText('Alpha')
    await userEvent.click(screen.getByRole('button', { name: /Delete category Wing A/i }))
    await waitFor(() => {
      expect(state.mutations.some(m =>
        m.method === 'DELETE' && m.url.includes('/api/raids/categories'),
      )).toBe(true)
    })
  })
})
