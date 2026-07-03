import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import { GuildRaidScheduleTab } from './GuildRaidScheduleTab'

function mockFetch(teams: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ teams }) })) as unknown as typeof fetch,
  )
}

const TEAMS = [
  {
    name: 'Team 1',
    primary_tz: 'America/New_York',
    twitch_url: null,
    raids: [{ days: [2, 4], start_min: 1200, end_min: 1380, label: null }],
  },
]

beforeEach(() => vi.restoreAllMocks())

describe('GuildRaidScheduleTab', () => {
  it('shows the schedule read-only for non-officers (no Edit control)', async () => {
    mockFetch(TEAMS)
    render(<GuildRaidScheduleTab guildName="Exordium" isOfficer={false} />)
    expect(await screen.findByText('Team 1')).toBeInTheDocument()
    expect(screen.getByText('20:00–23:00')).toBeInTheDocument() // team-tz time
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
  })

  it('offers an Edit control to officers', async () => {
    mockFetch(TEAMS)
    render(<GuildRaidScheduleTab guildName="Exordium" isOfficer={true} />)
    expect(await screen.findByText('Team 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('renders an empty state when the guild has no schedule', async () => {
    mockFetch([])
    render(<GuildRaidScheduleTab guildName="Exordium" isOfficer={false} />)
    expect(await screen.findByText(/No raid schedule/i)).toBeInTheDocument()
  })
})
