import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import GuildHistoryTab from './GuildHistoryTab'
import type { GuildHistoryPoint } from './types'

vi.mock('../../hooks/useServer', () => ({
  useServer: () => ({ world: 'Wuoshi', displayName: 'Wuoshi', maxLevel: 80 }),
}))

let urls: string[]

function mockFetch(points: GuildHistoryPoint[] | ((days: number) => GuildHistoryPoint[])) {
  urls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url)
      const days = Number(new URL(url, 'http://test').searchParams.get('days'))
      const rows = typeof points === 'function' ? points(days) : points
      return { ok: true, status: 200, json: async () => ({ guild: 'Exordium', world: 'Wuoshi', days, points: rows }) }
    }) as unknown as typeof fetch,
  )
}

function point(day: string, members: number): GuildHistoryPoint {
  return {
    day,
    captured_at: 0,
    level: 300,
    members,
    accounts: members - 5,
    achievement_count: 1,
    max_level_members: 9,
    distinct_classes: 8,
  }
}

beforeEach(() => vi.restoreAllMocks())

describe('GuildHistoryTab', () => {
  it('fetches 90 days by default and shows the empty state for a guild with no rows', async () => {
    mockFetch([])
    render(<GuildHistoryTab guildName="Exordium" chartWidth={600} />)
    expect(await screen.findByText(/History starts collecting from today/)).toBeInTheDocument()
    expect(urls).toEqual(['/api/guild/Exordium/history?days=90'])
    expect(document.querySelector('svg')).toBeNull()
  })

  it('renders the three charts with the server max level in the heading', async () => {
    mockFetch([point('2026-09-26', 38), point('2026-09-27', 39), point('2026-09-28', 40)])
    render(<GuildHistoryTab guildName="Exordium" chartWidth={600} />)
    expect(await screen.findByText('Members and accounts')).toBeInTheDocument()
    expect(screen.getByText('Guild level')).toBeInTheDocument()
    expect(screen.getByText('Members at level 80')).toBeInTheDocument()
    await waitFor(() => expect(document.querySelectorAll('.recharts-wrapper')).toHaveLength(3))
  })

  it('refetches with the chosen range when a pill is clicked', async () => {
    mockFetch((days) => (days === 365 ? [point('2026-01-01', 20), point('2026-09-28', 40)] : []))
    render(<GuildHistoryTab guildName="Exordium" chartWidth={600} />)
    await screen.findByText(/History starts collecting/)
    fireEvent.click(screen.getByRole('button', { name: '1 year' }))
    await screen.findByText('Members and accounts')
    expect(urls).toEqual(['/api/guild/Exordium/history?days=90', '/api/guild/Exordium/history?days=365'])
  })

  it('URL-encodes the guild name', async () => {
    mockFetch([])
    render(<GuildHistoryTab guildName="Mother Russia" chartWidth={600} />)
    await screen.findByText(/History starts collecting/)
    expect(urls[0]).toBe('/api/guild/Mother%20Russia/history?days=90')
  })
})
