import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { GuildSettingsTab } from './GuildSettingsTab'

interface Recorded { url: string; init: RequestInit | undefined }
let calls: Recorded[]

interface Stored {
  officers_can_delete_parses: boolean
  officer_rank_ids: number[]
  officer_rank_ids_custom: boolean
  updated_at: number | null
  updated_by_name: string | null
}
type PutBody = { officers_can_delete_parses: boolean; officer_rank_ids: number[] | null }
const RANKS = [
  { id: 0, name: 'Guild Leader' },
  { id: 1, name: 'Senior Officer' },
  { id: 2, name: 'Officer' },
  { id: 3, name: 'Member' },
]

/** fetch stub with server-side state: a PUT changes what later GETs return. */
function mockFetch(initial: { officers_can_delete_parses: boolean; officer_rank_ids?: number[]; officer_rank_ids_custom?: boolean }, putStatus = 200) {
  calls = []
  let stored: Stored = { officer_rank_ids: [0, 1], officer_rank_ids_custom: false, ...initial, updated_at: null, updated_by_name: null }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      if (init?.method === 'PUT') {
        if (putStatus !== 200) {
          return { ok: false, status: putStatus, json: async () => ({ detail: 'Guild leader access required' }) }
        }
        const body = JSON.parse(String(init.body)) as PutBody
        const ranks = body.officer_rank_ids ? [...new Set([0, ...body.officer_rank_ids])].sort((a, b) => a - b) : [0, 1]
        stored = {
          officers_can_delete_parses: body.officers_can_delete_parses,
          officer_rank_ids: ranks,
          officer_rank_ids_custom: body.officer_rank_ids !== null,
          updated_at: 1_700_000_000,
          updated_by_name: 'Boss',
        }
      }
      return { ok: true, status: 200, json: async () => ({ ...stored }) }
    }) as unknown as typeof fetch,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('GuildSettingsTab', () => {
  it('renders the stored value and keeps Save disabled until something changes', async () => {
    mockFetch({ officers_can_delete_parses: true })
    render(<GuildSettingsTab guildName="Exordium" ranks={RANKS} />)
    const box = await screen.findByRole('checkbox', { name: /Officers can delete guild parses/ })
    expect(box).toBeChecked()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('PUTs the toggled value with credentials and shows the saved state', async () => {
    mockFetch({ officers_can_delete_parses: true })
    render(<GuildSettingsTab guildName="Exordium" ranks={RANKS} />)
    const box = await screen.findByRole('checkbox', { name: /Officers can delete guild parses/ })
    fireEvent.click(box)
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(screen.getByText('Saved.')).toBeInTheDocument())
    const put = calls.find(c => c.init?.method === 'PUT')!
    expect(put.url).toBe('/api/guild/Exordium/settings')
    expect(put.init?.credentials).toBe('include')
    expect(JSON.parse(String(put.init?.body))).toEqual({ officers_can_delete_parses: false, officer_rank_ids: null })
    // The refetch after save lands a beat later; allow for a loaded runner.
    expect(await screen.findByText(/by Boss/, {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it('surfaces the server detail when the caller is not the leader', async () => {
    mockFetch({ officers_can_delete_parses: true }, 403)
    render(<GuildSettingsTab guildName="Exordium" ranks={RANKS} />)
    fireEvent.click(await screen.findByRole('checkbox', { name: /Officers can delete guild parses/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/Guild leader access required/)).toBeInTheDocument()
  })

  it('lets the leader add a rank to the officer set; the leader rank itself is locked on', async () => {
    mockFetch({ officers_can_delete_parses: true })
    render(<GuildSettingsTab guildName="Exordium" ranks={RANKS} />)
    const leader = await screen.findByRole('checkbox', { name: /Guild Leader/ })
    expect(leader).toBeChecked()
    expect(leader).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /Senior Officer/ })).toBeChecked()
    const officer = screen.getByRole('checkbox', { name: /^Officer rank 3/ })
    expect(officer).not.toBeChecked()
    fireEvent.click(officer)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.getByText('Saved.')).toBeInTheDocument())
    const put = calls.find(c => c.init?.method === 'PUT')!
    expect(JSON.parse(String(put.init?.body))).toEqual({ officers_can_delete_parses: true, officer_rank_ids: [0, 1, 2] })
  })

  it('can reset a custom rank set back to the site default', async () => {
    mockFetch({ officers_can_delete_parses: true, officer_rank_ids: [0, 1, 2], officer_rank_ids_custom: true })
    render(<GuildSettingsTab guildName="Exordium" ranks={RANKS} />)
    await screen.findByRole('checkbox', { name: /Guild Leader/ })
    fireEvent.click(screen.getByRole('button', { name: /Use site default/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.getByText('Saved.')).toBeInTheDocument())
    const put = calls.find(c => c.init?.method === 'PUT')!
    expect(JSON.parse(String(put.init?.body))).toEqual({ officers_can_delete_parses: true, officer_rank_ids: null })
  })
})
