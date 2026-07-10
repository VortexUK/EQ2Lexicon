import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import FavoritesSection from './FavoritesSection'

function mockFetch(favorites: unknown[]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (typeof url === 'string' && url.startsWith('/api/favorites')) {
      return { ok: true, status: 200, json: async () => ({ favorites }) }
    }
    // useClasses expects an array payload
    return { ok: true, status: 200, json: async () => ([]) }
  }) as unknown as typeof fetch)
}

const entry = (over: Record<string, unknown> = {}) => ({
  character_name: 'Menludiir',
  world: 'Varsoon',
  created_at: 100,
  level: 70,
  cls: 'Templar',
  ts_class: 'sage',
  ts_level: 70,
  guild_name: 'Exordium',
  ...over,
})

beforeEach(() => vi.restoreAllMocks())

describe('FavoritesSection', () => {
  it('renders nothing when the user has no favourites', async () => {
    mockFetch([])
    const { container } = render(<MemoryRouter><FavoritesSection /></MemoryRouter>)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('renders the header and a card per favourite', async () => {
    mockFetch([entry(), entry({ character_name: 'Sihtric', cls: 'Shadowknight', guild_name: null })])
    render(<MemoryRouter><FavoritesSection /></MemoryRouter>)
    expect(await screen.findByText('Favourites')).toBeInTheDocument()
    expect(screen.getByText('Menludiir')).toBeInTheDocument()
    expect(screen.getByText('Sihtric')).toBeInTheDocument()
    expect(screen.getByText('<Exordium>')).toBeInTheDocument()
  })

  it('renders a name-only favourite with dash placeholders (not loading)', async () => {
    mockFetch([entry({ cls: null, level: null, ts_class: null, ts_level: null, guild_name: null })])
    render(<MemoryRouter><FavoritesSection /></MemoryRouter>)
    expect(await screen.findByText('Menludiir')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('loading…')).not.toBeInTheDocument()
  })
})
