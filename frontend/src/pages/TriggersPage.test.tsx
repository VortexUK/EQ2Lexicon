/**
 * TriggersPage tests — categories section (contributor gating + create flow)
 * and the raid-encounter listing grouped from the pack.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

import TriggersPage from './TriggersPage'
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
const CONTRIBUTOR: AuthState = {
  status: 'authenticated',
  user: { ..._USER_BASE, static_roles: ['contributor'] },
}

interface MockState {
  categories: { name: string; position: number; trigger_count: number; spell_timer_count: number }[]
  pack: {
    version: string
    generated_at: number
    zones: {
      zone: string
      expansion: string
      encounters: { mob: string; position: number; triggers: unknown[]; spell_timers: unknown[] }[]
    }[]
  }
  mutations: { method: string; url: string; body: unknown }[]
}

function installFetchMock(state: MockState) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = (init?.method ?? 'GET').toUpperCase()

      if (method === 'GET' && url.includes('/api/act/categories')) {
        return { ok: true, status: 200, json: async () => state.categories }
      }
      if (method === 'GET' && url.includes('/api/act/pack')) {
        return { ok: true, status: 200, json: async () => state.pack }
      }
      if (method === 'GET' && url.includes('/api/raids/expansions')) {
        return { ok: true, status: 200, json: async () => [{ short: 'KoS', name: 'Kingdom of Sky', year: 2006 }] }
      }
      // Nested <ActTriggers> fetches (on expand) + its useAuth call.
      if (method === 'GET' && (url.includes('/triggers') || url.includes('/spell-timers'))) {
        return { ok: true, status: 200, json: async () => [] }
      }
      if (method === 'GET' && url.includes('/api/auth/me')) {
        return { ok: true, status: 200, json: async () => _USER_BASE }
      }
      if (method === 'POST' && url.includes('/api/act/categories')) {
        const body = init?.body ? JSON.parse(init.body as string) : null
        state.mutations.push({ method, url, body })
        state.categories = [
          ...state.categories,
          { name: (body as { name: string }).name, position: 0, trigger_count: 0, spell_timer_count: 0 },
        ]
        return { ok: true, status: 201, json: async () => ({ name: (body as { name: string }).name, position: 0 }) }
      }
      throw new Error(`Unmocked fetch: ${method} ${url}`)
    }),
  )
}

const PACK: MockState['pack'] = {
  version: '1.2.3',
  generated_at: 1_700_000_000,
  zones: [
    {
      zone: "The Laboratory of Lord Vyemm",
      expansion: 'KoS',
      encounters: [
        { mob: 'Lord Vyemm', position: 0, triggers: [{}, {}], spell_timers: [{}] },
      ],
    },
    // The synthetic categories zone must NOT appear in the raid section.
    {
      zone: 'General',
      expansion: 'General',
      encounters: [{ mob: 'Death Saves', position: 0, triggers: [{}], spell_timers: [] }],
    },
  ],
}

function renderPage(auth: AuthState) {
  return render(
    <MemoryRouter>
      <TriggersPage authOverride={auth} />
    </MemoryRouter>,
  )
}

describe('TriggersPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('imports without throwing and renders categories + raid encounters', async () => {
    installFetchMock({
      categories: [{ name: 'Death Saves', position: 0, trigger_count: 3, spell_timer_count: 1 }],
      pack: PACK,
      mutations: [],
    })
    renderPage(REGULAR)

    expect(await screen.findByText('Death Saves')).toBeTruthy()
    expect(await screen.findByText('Lord Vyemm')).toBeTruthy()
    expect(screen.getByText('Kingdom of Sky')).toBeTruthy()
    expect(screen.getByText("The Laboratory of Lord Vyemm")).toBeTruthy()
    // The General zone is the categories section, not a raid zone card.
    expect(screen.queryByText('General')).toBeNull()
    expect(screen.getByText('3 triggers')).toBeTruthy()
  })

  it('hides category management from non-contributors', async () => {
    installFetchMock({ categories: [], pack: PACK, mutations: [] })
    renderPage(REGULAR)
    await screen.findByText('Lord Vyemm')
    expect(screen.queryByRole('button', { name: 'New category' })).toBeNull()
  })

  it('lets a contributor create a category', async () => {
    const state: MockState = { categories: [], pack: PACK, mutations: [] }
    installFetchMock(state)
    renderPage(CONTRIBUTOR)

    await userEvent.click(await screen.findByRole('button', { name: 'New category' }))
    await userEvent.type(screen.getByPlaceholderText(/Category name/), 'Cures')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(state.mutations).toHaveLength(1))
    expect(state.mutations[0]).toMatchObject({ method: 'POST', body: { name: 'Cures' } })
    // Refetched list shows the new category.
    expect(await screen.findByText('Cures')).toBeTruthy()
  })
})
