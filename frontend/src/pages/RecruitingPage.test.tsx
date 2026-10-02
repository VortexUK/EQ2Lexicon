import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import RecruitingPage from './RecruitingPage'

const TAGS = ['casual', 'raiding']

const guild = (over: Record<string, unknown> = {}) => ({
  guild_name: 'Exordium',
  description: 'Core raiding guild.',
  classes: ['Guardian'],
  tags: ['raiding'],
  contacts: ['Sihtric'],
  discord_url: 'https://discord.gg/abc123',
  updated_at: 1_800_000_000,
  has_logo: false,
  logo_uploaded_at: null,
  member_count: 61,
  ...over,
})

function mockFetch(guilds: unknown[]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (typeof url === 'string' && url.startsWith('/api/recruiting')) {
      return { ok: true, status: 200, json: async () => ({ guilds, available_tags: TAGS }) }
    }
    // useClasses expects an array payload
    return { ok: true, status: 200, json: async () => ([]) }
  }) as unknown as typeof fetch)
}

beforeEach(() => vi.restoreAllMocks())

describe('RecruitingPage', () => {
  it('renders a card per recruiting guild with its facts', async () => {
    mockFetch([guild(), guild({ guild_name: 'Remnant', tags: ['casual'], member_count: null, description: 'Chill levelling crew.' })])
    render(<MemoryRouter><RecruitingPage /></MemoryRouter>)
    expect(await screen.findByText('Exordium')).toBeInTheDocument()
    expect(screen.getByText('Remnant')).toBeInTheDocument()
    expect(screen.getByText('Core raiding guild.')).toBeInTheDocument()
    expect(screen.getByText(/61 characters/)).toBeInTheDocument()
    expect(screen.getAllByText('Sihtric').length).toBeGreaterThanOrEqual(1)
  })

  it('tag filter pills narrow the list', async () => {
    mockFetch([guild(), guild({ guild_name: 'Remnant', tags: ['casual'] })])
    render(<MemoryRouter><RecruitingPage /></MemoryRouter>)
    await screen.findByText('Exordium')
    // The "Casual" pill appears both as a filter and on the Remnant card badge;
    // the filter pill is a button.
    fireEvent.click(screen.getByRole('button', { name: 'Casual' }))
    await waitFor(() => expect(screen.queryByText('Exordium')).not.toBeInTheDocument())
    expect(screen.getByText('Remnant')).toBeInTheDocument()
  })

  it('shows the empty state when nobody is recruiting', async () => {
    mockFetch([])
    render(<MemoryRouter><RecruitingPage /></MemoryRouter>)
    expect(await screen.findByText(/No guilds are recruiting right now/)).toBeInTheDocument()
  })
})
