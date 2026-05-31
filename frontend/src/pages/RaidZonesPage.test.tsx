/**
 * RaidZonesPage tests — exercise the page-level admin curation flow
 * (expansion list, "Add expansion" picker, empty state).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

import RaidZonesPage from './RaidZonesPage'
import type { AuthState, User } from '../hooks/useAuth'

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

interface MockState {
  expansions: { short: string; name: string | null; year: number | null }[]
  available: { short: string; name: string | null; year: number | null }[]
  zonesByExpansion: Record<string, unknown[]>
  mutations: { method: string; url: string }[]
}

function installFetchMock(state: MockState) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = (init?.method ?? 'GET').toUpperCase()

      if (method === 'GET' && url.includes('/api/raids/expansions/available')) {
        return { ok: true, status: 200, json: async () => state.available }
      }
      if (method === 'GET' && url.includes('/api/raids/expansions')) {
        return { ok: true, status: 200, json: async () => state.expansions }
      }
      if (method === 'GET' && url.includes('/api/raids/zones')) {
        // Match ?expansion=RoK so we route to the right list.
        const m = url.match(/expansion=([^&]+)/)
        const exp = m ? decodeURIComponent(m[1]) : ''
        return { ok: true, status: 200, json: async () => state.zonesByExpansion[exp] ?? [] }
      }
      if (method === 'GET' && url.includes('/api/raid-progress')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ guild_name: null, character_name: null, killed_encounters: {} }),
        }
      }
      if (method === 'GET' && url.includes('/api/server')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            world: 'Varsoon', displayName: 'Varsoon', maxLevel: 80, currentXpac: 'RoK', launchDt: null, servers: [],
          }),
        }
      }
      if (method === 'GET' && url.includes('/api/zones')) {
        // Dungeons card supporting fetches.
        return { ok: true, status: 200, json: async () => ({ expansion: '', type: '', zones: [] }) }
      }
      if (method === 'GET' && url.includes('/api/auth/me')) {
        return { ok: false, status: 401, json: async () => ({}) }
      }

      if (method === 'POST' && url.includes('/api/raids/expansions/')) {
        const m = decodeURIComponent(url.match(/\/api\/raids\/expansions\/(.+)$/)?.[1] ?? '')
        state.mutations.push({ method, url })
        if (!state.expansions.some(e => e.short === m)) {
          const added = state.available.find(e => e.short === m) ?? { short: m, name: m, year: null }
          state.expansions = [...state.expansions, added]
          state.available = state.available.filter(e => e.short !== m)
        }
        return { ok: true, status: 200, json: async () => ({ expansion_short: m }) }
      }

      return { ok: false, status: 404, json: async () => ({ detail: 'not mocked' }) }
    }) as unknown as typeof fetch,
  )
}

function renderPage(state: MockState, auth: AuthState) {
  installFetchMock(state)
  return render(
    <MemoryRouter>
      <RaidZonesPage authOverride={auth} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('confirm', vi.fn(() => true))
})

describe('RaidZonesPage rendering', () => {
  it('renders expansions from the fetched response', async () => {
    renderPage(
      {
        expansions: [{ short: 'RoK', name: 'Rise of Kunark', year: 2007 }],
        available: [],
        zonesByExpansion: { RoK: [] },
        mutations: [],
      },
      REGULAR,
    )
    expect(await screen.findByText(/Rise of Kunark \(RoK\)/i)).toBeInTheDocument()
  })

  it('shows admin empty state when nothing is curated', async () => {
    renderPage(
      { expansions: [], available: [], zonesByExpansion: {}, mutations: [] },
      ADMIN,
    )
    expect(
      await screen.findByText(/No expansions have been added to \/raids yet/i),
    ).toBeInTheDocument()
    // Admin sees the page-level "Add expansion" button alongside the empty state.
    expect(screen.getByRole('button', { name: /Add expansion/i })).toBeInTheDocument()
  })
})

describe('RaidZonesPage admin affordances', () => {
  it('does NOT show "Add expansion" button for non-admin', async () => {
    renderPage(
      { expansions: [], available: [], zonesByExpansion: {}, mutations: [] },
      REGULAR,
    )
    await screen.findByText(/No expansions have been added/i)
    expect(screen.queryByRole('button', { name: /Add expansion/i })).not.toBeInTheDocument()
  })

  it('shows "Add expansion" button for admin', async () => {
    renderPage(
      { expansions: [], available: [], zonesByExpansion: {}, mutations: [] },
      ADMIN,
    )
    expect(await screen.findByRole('button', { name: /Add expansion/i })).toBeInTheDocument()
  })

  it('"Add expansion" modal is populated from /raids/expansions/available', async () => {
    renderPage(
      {
        expansions: [],
        available: [{ short: 'TSO', name: 'The Shadow Odyssey', year: 2008 }],
        zonesByExpansion: {},
        mutations: [],
      },
      ADMIN,
    )
    await userEvent.click(await screen.findByRole('button', { name: /Add expansion/i }))
    expect(await screen.findByText(/The Shadow Odyssey/i)).toBeInTheDocument()
  })

  it('clicking an expansion in the modal POSTs and refetches', async () => {
    const state: MockState = {
      expansions: [],
      available: [{ short: 'TSO', name: 'The Shadow Odyssey', year: 2008 }],
      zonesByExpansion: { TSO: [] },
      mutations: [],
    }
    renderPage(state, ADMIN)
    await userEvent.click(await screen.findByRole('button', { name: /Add expansion/i }))
    const opt = await screen.findByRole('button', { name: /The Shadow Odyssey/i })
    await userEvent.click(opt)
    await waitFor(() => {
      expect(state.mutations.some(m =>
        m.method === 'POST' && m.url.includes('/api/raids/expansions/TSO'),
      )).toBe(true)
    })
    // After refetch, the new expansion section is on the page.
    await waitFor(() => {
      expect(screen.getByText(/The Shadow Odyssey \(TSO\)/i)).toBeInTheDocument()
    })
  })
})
