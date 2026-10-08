/**
 * ItemSearchPage — stale-response race + "setSearchParams can throw" tests.
 * ItemSearchFilters is stubbed to two buttons that fire onSearch directly.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const h = vi.hoisted(() => ({
  throwOnSet: false,
  throwingSet: () => { throw new DOMException('History API quota exhausted', 'SecurityError') },
}))

vi.mock('react-router-dom', async importOriginal => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useSearchParams: () => {
      const [params, set] = actual.useSearchParams()
      return [params, h.throwOnSet ? h.throwingSet : set] as const
    },
  }
})

vi.mock('./items/ItemSearchFilters', () => ({
  default: ({ onSearch }: { onSearch: (q: unknown) => void }) => {
    const q = (text: string) => ({ q: text, tier: '', slot: '', itemType: '', cls: '', minLevel: '', maxLevel: '', stats: [] })
    return (
      <div>
        <button onClick={() => onSearch(q('alpha'))}>search-alpha</button>
        <button onClick={() => onSearch(q('beta'))}>search-beta</button>
      </div>
    )
  },
}))

import ItemSearchPage from './ItemSearchPage'

function payload(name: string, total: number) {
  return {
    results: [{
      id: total, name, tier: 'FABLED', slot: 'Head', item_type: null, level: 80,
      class_label: null, icon_id: null, stats: [], stat_values: {},
    }],
    total, page: 1, per_page: 25,
  }
}

type Deferred = { resolve: (v: unknown) => void }

beforeEach(() => {
  vi.restoreAllMocks()
  h.throwOnSet = false
})

function renderPage() {
  return render(<MemoryRouter><ItemSearchPage /></MemoryRouter>)
}

describe('ItemSearchPage stale responses', () => {
  it('drops an older response that resolves after a newer one', async () => {
    const pending: Record<string, Deferred> = {}
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const name = new URL(url, 'http://x').searchParams.get('name') ?? ''
      return new Promise(resolve => {
        pending[name] = { resolve: (body: unknown) => resolve({ ok: true, status: 200, json: async () => body }) }
      })
    }) as unknown as typeof fetch)

    renderPage()
    fireEvent.click(screen.getByText('search-alpha'))
    fireEvent.click(screen.getByText('search-beta'))

    // Newer (beta) lands first, then the slow older (alpha) one.
    await act(async () => { pending.beta.resolve(payload('BetaItem', 22)) })
    await act(async () => { pending.alpha.resolve(payload('AlphaItem', 11)) })

    expect(await screen.findByText('BetaItem')).toBeInTheDocument()
    expect(screen.queryByText('AlphaItem')).not.toBeInTheDocument()
    expect(screen.getByText(/22 items found/)).toBeInTheDocument()
  })
})

describe('ItemSearchPage URL mirror', () => {
  it('still shows results when setSearchParams throws a SecurityError', async () => {
    h.throwOnSet = true
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => payload('BetaItem', 22),
    })) as unknown as typeof fetch)

    renderPage()
    fireEvent.click(screen.getByText('search-beta'))
    expect(await screen.findByText('BetaItem')).toBeInTheDocument()
  })
})
