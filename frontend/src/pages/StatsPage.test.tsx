/**
 * StatsPage render tests — totals cards, class-distribution bars, named
 * leaderboards with character links, class averages table.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import StatsPage from './StatsPage'

vi.mock('../hooks/useServer', () => ({
  useServer: () => ({ displayName: 'Wuoshi' }),
}))

// useClasses caches [] module-wide after its first fetch, so stub the hook
// directly — the page only needs `classes` (explorer dropdown) + `colourFor`.
vi.mock('../useClasses', () => ({
  useClasses: () => ({
    classes: [{ name: 'Templar' }, { name: 'Necromancer' }],
    byName: new Map(),
    colourFor: () => 'var(--text)',
    iconUrlFor: () => null,
  }),
}))

const STATS = {
  world: 'Wuoshi',
  ts: 1784972773,
  fetched_at: 1784980000,
  population: 21343,
  totals: {
    kills: 146061046,
    deaths: 637644,
    'quests.complete': 1358803,
    'collections.complete': 78657,
    items_crafted: 40225247,
    rare_harvests: 891495,
  },
  records: { kills: 465696, max_melee_hit: 268591 },
  averages: { kills: 6843.5 },
  global_averages: { kills: 9000 },
  classes: [
    { classid: 33, name: 'Necromancer', count: 2305, avg: { kills: 13216, kills_deaths_ratio: 300 }, global_avg: { kills_deaths_ratio: 250 } },
    { classid: 13, name: 'Templar', count: 900, avg: { kills: 6000, kills_deaths_ratio: 120 }, global_avg: { kills_deaths_ratio: 110 } },
  ],
  leaders: {
    kills: [
      { name: 'Kaipai', cls: 'Necromancer', level: 70, value: 465696 },
      { name: 'Touxin', cls: 'Fury', level: 70, value: 459917 },
    ],
    max_melee_hit: [{ name: 'Dema', cls: 'Guardian', level: 70, value: 268591 }],
  },
}

function stubFetch(body: unknown = STATS, ok = true) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
    ok: url.includes('/api/stats/server') ? ok : true,
    status: ok ? 200 : 503,
    json: async () => (url.includes('/api/classes') ? [] : body),
  })) as unknown as typeof fetch)
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('StatsPage', () => {
  it('renders population, totals, class bars, leaderboards and the averages table', async () => {
    stubFetch()
    render(<MemoryRouter><StatsPage /></MemoryRouter>)

    expect(await screen.findByText(/21,343 characters known to Census/)).toBeInTheDocument()
    // Totals card
    expect(screen.getByText('146,061,046')).toBeInTheDocument()
    expect(screen.getByText('NPCs slain')).toBeInTheDocument()
    // Class distribution (name appears in the bar row AND the table)
    expect(screen.getAllByText('Necromancer').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/2,305 · 10.8%/)).toBeInTheDocument()
    // Leaderboards: names link to character pages
    const kaipai = screen.getByRole('link', { name: 'Kaipai' })
    expect(kaipai).toHaveAttribute('href', '/character/Kaipai')
    expect(screen.getByText('Most Kills')).toBeInTheDocument()
    expect(screen.getByText('Biggest Melee Hit')).toBeInTheDocument()
    // Boards with no data don't render
    expect(screen.queryByText('Rare Harvests')).not.toBeInTheDocument()
    // Averages table shows the global comparison column
    expect(screen.getByText('Global avg K/D')).toBeInTheDocument()
  })

  it('surfaces the 503 message when stats are unavailable', async () => {
    stubFetch({ detail: 'Server statistics unavailable (Census unreachable). Try again shortly.' }, false)
    render(<MemoryRouter><StatsPage /></MemoryRouter>)
    expect(await screen.findByText(/Census unreachable/)).toBeInTheDocument()
  })

  it('explorer tab fetches whitelisted stats and refetches on class change', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push(url)
      if (url.includes('/api/stats/explore')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            stat: 'ability_mod',
            cls: url.includes('cls=') ? 'Templar' : null,
            entries: url.includes('cls=')
              ? [{ name: 'Menludiir', cls: 'Templar', level: 70, value: 1868 }]
              : [
                  { name: 'Menludiir', cls: 'Templar', level: 70, value: 1868 },
                  { name: 'Sihtric', cls: 'Wizard', level: 70, value: 1700 },
                ],
          }),
        }
      }
      return { ok: true, status: 200, json: async () => STATS }
    }) as unknown as typeof fetch)

    render(<MemoryRouter><StatsPage /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Explorer' }))

    // Default stat (Ability Mod, all classes) loads and links to characters.
    expect(await screen.findByRole('link', { name: 'Menludiir' })).toHaveAttribute('href', '/character/Menludiir')
    expect(screen.getByText('Sihtric')).toBeInTheDocument()
    expect(calls.some(u => u.includes('/api/stats/explore?stat=ability_mod') && !u.includes('cls='))).toBe(true)

    // Narrowing to a class refetches with cls= and re-renders.
    fireEvent.change(screen.getByLabelText('Class'), { target: { value: 'Templar' } })
    await vi.waitFor(() => {
      if (!calls.some(u => u.includes('stat=ability_mod') && u.includes('cls=Templar'))) throw new Error('not yet')
    })
    // The table hides while the refetch is in flight — wait for loading to
    // clear so we're asserting against the narrowed result set.
    await vi.waitFor(() => {
      if (screen.queryByText('Asking Census…')) throw new Error('still loading')
      if (screen.queryByText('Sihtric')) throw new Error('not yet')
    })
    expect(screen.getByRole('link', { name: 'Menludiir' })).toBeInTheDocument()
  })

  it('network failure → retries instead of dead-ending, recovers when the server returns', async () => {
    vi.useFakeTimers()
    try {
      let call = 0
      vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        if (url.includes('/api/classes')) return { ok: true, status: 200, json: async () => [] }
        call += 1
        // First hit: the server is mid-restart — connection-level failure.
        if (call === 1) throw new TypeError('NetworkError when attempting to fetch resource.')
        return { ok: true, status: 200, json: async () => STATS }
      }) as unknown as typeof fetch)

      render(<MemoryRouter><StatsPage /></MemoryRouter>)
      // The failure surfaces as a soft retry message, not the red error.
      expect(await vi.waitFor(() => {
        const el = screen.queryByText(/Lost contact with the server/)
        if (!el) throw new Error('not yet')
        return el
      })).toBeInTheDocument()
      expect(screen.queryByText(/NetworkError/)).not.toBeInTheDocument()
      // The 5s retry refetches and real data replaces the message.
      await vi.advanceTimersByTimeAsync(5100)
      await vi.waitFor(() => {
        if (!screen.queryByText(/21,343 characters known to Census/)) throw new Error('not yet')
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('202 building → shows the crunching message and polls until data lands', async () => {
    vi.useFakeTimers()
    try {
      let call = 0
      vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        if (url.includes('/api/classes')) return { ok: true, status: 200, json: async () => [] }
        call += 1
        return call === 1
          ? { ok: true, status: 202, json: async () => ({ status: 'building' }) }
          : { ok: true, status: 200, json: async () => STATS }
      }) as unknown as typeof fetch)

      render(<MemoryRouter><StatsPage /></MemoryRouter>)
      // First response: 202 → the building copy shows.
      expect(await vi.waitFor(() => {
        const el = screen.queryByText(/Crunching server statistics/)
        if (!el) throw new Error('not yet')
        return el
      })).toBeInTheDocument()
      // The 5s poll refetches and real data replaces the message.
      await vi.advanceTimersByTimeAsync(5100)
      await vi.waitFor(() => {
        if (!screen.queryByText(/21,343 characters known to Census/)) throw new Error('not yet')
      })
    } finally {
      vi.useRealTimers()
    }
  })
})
