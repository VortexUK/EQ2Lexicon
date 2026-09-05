import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { GuildAttendanceTab } from './GuildAttendanceTab'

const SESSION = {
  id: 7,
  session_day: '2026-07-25',
  seq: 0,
  started_at: 1_784_500_000,
  ended_at: 1_784_512_000,
  zones: ["Veeshan's Peak"],
  scheduled: true,
  team_index: 0,
  counts: { present: 18, sat_out: 2, afk: 1, awol: 1 },
}

const DETAIL = {
  is_officer: false,
  session: { ...SESSION, counts: undefined, uploaders: null },
  characters: [
    {
      name: 'Tanky',
      role: 'raider',
      category: 'present',
      first_seen: 1_784_500_000,
      last_seen: 1_784_512_000,
      owner_discord_id: 'u1',
    },
    {
      name: 'Alty',
      role: 'raid_alt',
      category: 'present',
      first_seen: 1_784_500_000,
      last_seen: 1_784_512_000,
      owner_discord_id: 'u2',
    },
    { name: 'Ghosty', role: 'raider', category: 'awol', first_seen: null, last_seen: null, owner_discord_id: null },
  ],
  users: [
    { discord_id: 'u1', category: 'present', afk_declared: false, characters: ['Tanky'], display_name: 'Ben', main: 'Tanky', in_voice: false },
    { discord_id: 'u2', category: 'present', afk_declared: false, characters: ['Alty'], display_name: 'Sam', main: 'Mainy', in_voice: true },
  ],
}

function mockFetch(list: unknown, detail: unknown = DETAIL) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (/\/attendance\/\d+$/.test(String(url))) {
        return { ok: true, status: 200, json: async () => detail }
      }
      if (typeof list === 'number') {
        return { ok: false, status: list, json: async () => ({ detail: 'nope' }) }
      }
      return { ok: true, status: 200, json: async () => list }
    }) as unknown as typeof fetch,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('GuildAttendanceTab', () => {
  it('imports without throwing', async () => {
    await expect(import('./GuildAttendanceTab')).resolves.toBeDefined()
  })

  it('shows the members-only notice on 403', async () => {
    mockFetch(403)
    render(<GuildAttendanceTab guildName="Exordium" />)
    expect(await screen.findByText(/guild members only/i)).toBeInTheDocument()
  })

  it('renders the empty state when no sessions exist', async () => {
    mockFetch({ is_officer: false, sessions: [] })
    render(<GuildAttendanceTab guildName="Exordium" />)
    expect(await screen.findByText(/No attendance recorded yet/i)).toBeInTheDocument()
  })

  it('lists sessions with count pills and zone chips', async () => {
    mockFetch({ is_officer: false, sessions: [SESSION] })
    render(<GuildAttendanceTab guildName="Exordium" />)
    expect(await screen.findByText('18 present')).toBeInTheDocument()
    expect(screen.getByText('2 sat out')).toBeInTheDocument()
    expect(screen.getByText('1 AWOL')).toBeInTheDocument()
    expect(screen.getByText("Veeshan's Peak")).toBeInTheDocument()
    expect(screen.queryByText(/off-schedule/)).not.toBeInTheDocument()
  })

  it('flags off-schedule sessions and hides the AWOL pill', async () => {
    mockFetch({
      is_officer: false,
      sessions: [{ ...SESSION, scheduled: false, counts: { ...SESSION.counts, awol: 3 } }],
    })
    render(<GuildAttendanceTab guildName="Exordium" />)
    expect(await screen.findByText('off-schedule')).toBeInTheDocument()
    expect(screen.queryByText('3 AWOL')).not.toBeInTheDocument()
  })

  it('opens the detail view with the by-player rollup, alt attribution and character toggle', async () => {
    mockFetch({ is_officer: false, sessions: [SESSION] })
    render(<GuildAttendanceTab guildName="Exordium" />)
    fireEvent.click(await screen.findByText('18 present'))
    expect(await screen.findByText('Ben')).toBeInTheDocument()
    expect(screen.getByText('· Mainy')).toBeInTheDocument() // Sam's raid main shown despite alting
    expect(screen.getByTitle(/raid voice channel/)).toBeInTheDocument() // Sam's headset marker
    expect(screen.getByText('Alty (alt)')).toBeInTheDocument() // raid alt credits its owner
    expect(screen.getByText(/Unclaimed: Ghosty/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('By character'))
    expect(await screen.findByText('AWOL')).toBeInTheDocument() // Ghosty's row
    expect(screen.queryByRole('button', { name: /Delete session/ })).not.toBeInTheDocument()
  })

  it('offers Delete session to officers', async () => {
    mockFetch({ is_officer: true, sessions: [SESSION] }, { ...DETAIL, is_officer: true })
    render(<GuildAttendanceTab guildName="Exordium" />)
    fireEvent.click(await screen.findByText('18 present'))
    expect(await screen.findByRole('button', { name: /Delete session/ })).toBeInTheDocument()
  })

  it('flags AWOL players who were sitting in voice', async () => {
    const detail = {
      ...DETAIL,
      users: [
        {
          discord_id: 'u3',
          category: 'awol',
          afk_declared: false,
          characters: ['Ghosty'],
          display_name: 'Lurker',
          main: 'Ghosty',
          in_voice: true,
        },
      ],
    }
    mockFetch({ is_officer: false, sessions: [SESSION] }, detail)
    render(<GuildAttendanceTab guildName="Exordium" />)
    fireEvent.click(await screen.findByText('18 present'))
    expect(await screen.findByText('in voice, not in game')).toBeInTheDocument()
  })

  it('shows neither voice marker for players not in voice', async () => {
    mockFetch({ is_officer: false, sessions: [SESSION] }, { ...DETAIL, users: [DETAIL.users[0]] })
    render(<GuildAttendanceTab guildName="Exordium" />)
    fireEvent.click(await screen.findByText('18 present'))
    expect(await screen.findByText('Ben')).toBeInTheDocument()
    expect(screen.queryByTitle(/raid voice channel/)).not.toBeInTheDocument()
    expect(screen.queryByText('in voice, not in game')).not.toBeInTheDocument()
  })
})
