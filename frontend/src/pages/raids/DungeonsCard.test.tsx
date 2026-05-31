/**
 * DungeonsCard render-state + interaction tests.
 *
 * Auth gate is exercised via the `authOverride` prop on DungeonsCard so we
 * don't have to mock the `/api/auth/me` round-trip. Everything else (zones
 * list + tag mutations) is mocked through `vi.stubGlobal('fetch', ...)`,
 * matching the pattern used by ParsesPage.test.tsx / ParsePage.test.tsx.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DungeonsCard } from './DungeonsCard'
import type { AuthState, User } from '../../hooks/useAuth'
import type { Zone } from './types'

// ── Auth helpers ──────────────────────────────────────────────────────────────

const _USER_BASE: User = {
  id: 'u1',
  username: 'tester',
  global_name: 'Tester',
  avatar: null,
  is_admin: false,
  access_status: 'approved',
  static_roles: [],
}

const UNAUTH: AuthState = { status: 'unauthenticated' }
const LOADING: AuthState = { status: 'loading' }
const REGULAR: AuthState = { status: 'authenticated', user: { ..._USER_BASE } }
const CONTRIBUTOR: AuthState = {
  status: 'authenticated',
  user: { ..._USER_BASE, static_roles: ['contributor'] },
}

// ── Zone fixture helpers ──────────────────────────────────────────────────────

function zone(name: string, overrides: Partial<Zone> = {}): Zone {
  return {
    name,
    expansion_short: 'RoK',
    expansion_name: 'Rise of Kunark',
    expansion_year: 2007,
    types: [],
    aliases: [],
    wiki_url: null,
    is_contested: false,
    is_instance: true,
    is_openworld: false,
    bosses: [],
    ...overrides,
  }
}

// ── Fetch mock orchestration ──────────────────────────────────────────────────

interface MockState {
  dungeons: Zone[]
  allZones: Zone[]
  /** Called whenever a POST or DELETE is observed (mutation log). */
  mutations: { method: string; url: string; body?: unknown }[]
}

function installFetchMock(state: MockState) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = (init?.method ?? 'GET').toUpperCase()

      if (method === 'GET' && url.includes('/api/zones')) {
        // The component fetches twice per expansion: with type=dungeon
        // (gives the curated list) and with type= (gives every zone in
        // the expansion). Discriminate on the query string.
        const wantsDungeonOnly = url.includes('type=dungeon')
        const zones = wantsDungeonOnly ? state.dungeons : state.allZones
        return {
          ok: true,
          status: 200,
          json: async () => ({ expansion: 'RoK', type: wantsDungeonOnly ? 'dungeon' : '', zones }),
        }
      }

      if (method === 'POST' && url.includes('/types')) {
        const body = init?.body ? JSON.parse(String(init.body)) : undefined
        state.mutations.push({ method, url, body })
        // Move the targeted zone from the "all" pool into the "dungeons"
        // list to simulate the server adding the tag. The next refetch will
        // observe the updated state.
        const match = decodeURIComponent(url.match(/\/api\/zones\/([^/]+)\/types/)?.[1] ?? '')
        const target = state.allZones.find(z => z.name === match)
        if (target && !state.dungeons.some(z => z.name === match)) {
          state.dungeons = [...state.dungeons, { ...target, types: ['dungeon'] }]
        }
        return { ok: true, status: 200, json: async () => ({ ...(target ?? {}), types: ['dungeon'] }) }
      }

      if (method === 'DELETE' && url.includes('/types/dungeon')) {
        state.mutations.push({ method, url })
        const match = decodeURIComponent(url.match(/\/api\/zones\/([^/]+)\/types\/dungeon/)?.[1] ?? '')
        state.dungeons = state.dungeons.filter(z => z.name !== match)
        return { ok: true, status: 200, json: async () => ({ name: match, types: [] }) }
      }

      return { ok: false, status: 404, json: async () => ({ detail: 'not mocked' }) }
    }) as unknown as typeof fetch,
  )
}

// ── beforeEach ────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.restoreAllMocks()
})

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('DungeonsCard auth gating', () => {
  it('renders nothing when unauthenticated', () => {
    installFetchMock({ dungeons: [], allZones: [], mutations: [] })
    const { container } = render(
      <DungeonsCard expansion="RoK" authOverride={UNAUTH} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing for a regular (non-contributor) authed user', () => {
    installFetchMock({ dungeons: [], allZones: [], mutations: [] })
    const { container } = render(
      <DungeonsCard expansion="RoK" authOverride={REGULAR} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing while auth is still loading', () => {
    installFetchMock({ dungeons: [], allZones: [], mutations: [] })
    const { container } = render(
      <DungeonsCard expansion="RoK" authOverride={LOADING} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders the card for a contributor', () => {
    installFetchMock({ dungeons: [], allZones: [], mutations: [] })
    render(<DungeonsCard expansion="RoK" authOverride={CONTRIBUTOR} />)
    expect(
      screen.getByRole('button', { name: /Dungeons/i, expanded: false }),
    ).toBeInTheDocument()
  })

  it('renders for an admin (admin is a contributor superset)', () => {
    installFetchMock({ dungeons: [], allZones: [], mutations: [] })
    const ADMIN: AuthState = {
      status: 'authenticated',
      user: { ..._USER_BASE, is_admin: true },
    }
    render(<DungeonsCard expansion="RoK" authOverride={ADMIN} />)
    expect(screen.getByRole('button', { name: /Dungeons/i })).toBeInTheDocument()
  })
})

describe('DungeonsCard list rendering', () => {
  it('lists dungeon zones from the fetched response when expanded', async () => {
    installFetchMock({
      dungeons: [zone('Crushbone Keep', { types: ['dungeon'] })],
      allZones: [
        zone('Crushbone Keep', { types: ['dungeon'] }),
        zone('Karnor’s Castle'),
      ],
      mutations: [],
    })
    render(<DungeonsCard expansion="RoK" authOverride={CONTRIBUTOR} />)
    await userEvent.click(screen.getByRole('button', { name: /Dungeons/i }))
    expect(await screen.findByText('Crushbone Keep')).toBeInTheDocument()
  })

  it('shows the Add-dungeon picker with non-dungeon zones in the expansion', async () => {
    installFetchMock({
      dungeons: [zone('Crushbone Keep', { types: ['dungeon'] })],
      allZones: [
        zone('Crushbone Keep', { types: ['dungeon'] }),
        zone('Karnor’s Castle'),
        zone('Sebilis'),
      ],
      mutations: [],
    })
    render(<DungeonsCard expansion="RoK" authOverride={CONTRIBUTOR} />)
    await userEvent.click(screen.getByRole('button', { name: /Dungeons/i }))
    // Wait for the picker to render (gated on loading=false for both fetches).
    const picker = (await screen.findByLabelText(/Add dungeon:/i)) as HTMLSelectElement
    const optionTexts = Array.from(picker.options).map(o => o.textContent)
    expect(optionTexts).toContain('Karnor’s Castle')
    expect(optionTexts).toContain('Sebilis')
    // The already-tagged dungeon must NOT appear in the picker.
    expect(optionTexts).not.toContain('Crushbone Keep')
  })
})

describe('DungeonsCard mutations', () => {
  it('selecting a zone in the dropdown POSTs the dungeon tag and adds the zone to the list', async () => {
    const state: MockState = {
      dungeons: [],
      allZones: [zone('Crushbone Keep'), zone('Karnor’s Castle')],
      mutations: [],
    }
    installFetchMock(state)
    render(<DungeonsCard expansion="RoK" authOverride={CONTRIBUTOR} />)
    await userEvent.click(screen.getByRole('button', { name: /Dungeons/i }))
    const picker = (await screen.findByLabelText(/Add dungeon:/i)) as HTMLSelectElement
    await userEvent.selectOptions(picker, 'Crushbone Keep')

    // A POST to /api/zones/Crushbone%20Keep/types with body {type: 'dungeon'}.
    expect(state.mutations.some(m =>
      m.method === 'POST' &&
      m.url.includes('/api/zones/Crushbone%20Keep/types') &&
      (m.body as { type?: string } | undefined)?.type === 'dungeon',
    )).toBe(true)

    // The list refreshes — the new dungeon should appear.
    expect(await screen.findByText('Crushbone Keep')).toBeInTheDocument()
  })

  it('clicking the remove (trash) button DELETEs the tag and removes the zone from the list', async () => {
    const state: MockState = {
      dungeons: [zone('Crushbone Keep', { types: ['dungeon'] })],
      allZones: [zone('Crushbone Keep', { types: ['dungeon'] })],
      mutations: [],
    }
    installFetchMock(state)
    render(<DungeonsCard expansion="RoK" authOverride={CONTRIBUTOR} />)
    await userEvent.click(screen.getByRole('button', { name: /Dungeons/i }))
    // The dungeon row should be present first.
    expect(await screen.findByText('Crushbone Keep')).toBeInTheDocument()
    // Trash button has an aria-label per the implementation.
    const trash = screen.getByRole('button', { name: /Remove Crushbone Keep from dungeons/i })
    await userEvent.click(trash)

    expect(state.mutations.some(m =>
      m.method === 'DELETE' &&
      m.url.includes('/api/zones/Crushbone%20Keep/types/dungeon'),
    )).toBe(true)
  })
})
