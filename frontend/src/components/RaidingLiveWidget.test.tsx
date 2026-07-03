import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import RaidingLiveWidget from './RaidingLiveWidget'

function mockFetch(entries: unknown[]) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => entries })) as unknown as typeof fetch)
}

beforeEach(() => vi.restoreAllMocks())

describe('RaidingLiveWidget', () => {
  it('renders nothing when nobody is live', async () => {
    mockFetch([])
    const { container } = render(<RaidingLiveWidget />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('shows a "N live" pill when teams are live', async () => {
    mockFetch([
      { guild_name: 'Exordium', team_name: 'Main', twitch_login: 'foo', twitch_url: 'https://twitch.tv/foo', viewer_count: 42, title: null, started_at: null },
    ])
    render(<RaidingLiveWidget />)
    expect(await screen.findByText('1 live')).toBeInTheDocument()
  })
})
