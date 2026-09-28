import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { GuildSettingsTab } from './GuildSettingsTab'

interface Recorded { url: string; init: RequestInit | undefined }
let calls: Recorded[]

interface Stored { officers_can_delete_parses: boolean; updated_at: number | null; updated_by_name: string | null }

/** fetch stub with server-side state: a PUT changes what later GETs return. */
function mockFetch(initial: { officers_can_delete_parses: boolean }, putStatus = 200) {
  calls = []
  let stored: Stored = { ...initial, updated_at: null, updated_by_name: null }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      if (init?.method === 'PUT') {
        if (putStatus !== 200) {
          return { ok: false, status: putStatus, json: async () => ({ detail: 'Guild leader access required' }) }
        }
        const body = JSON.parse(String(init.body)) as { officers_can_delete_parses: boolean }
        stored = { officers_can_delete_parses: body.officers_can_delete_parses, updated_at: 1_700_000_000, updated_by_name: 'Boss' }
      }
      return { ok: true, status: 200, json: async () => ({ ...stored }) }
    }) as unknown as typeof fetch,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('GuildSettingsTab', () => {
  it('renders the stored value and keeps Save disabled until something changes', async () => {
    mockFetch({ officers_can_delete_parses: true })
    render(<GuildSettingsTab guildName="Exordium" />)
    const box = await screen.findByRole('checkbox', { name: /Officers can delete guild parses/ })
    expect(box).toBeChecked()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('PUTs the toggled value with credentials and shows the saved state', async () => {
    mockFetch({ officers_can_delete_parses: true })
    render(<GuildSettingsTab guildName="Exordium" />)
    const box = await screen.findByRole('checkbox', { name: /Officers can delete guild parses/ })
    fireEvent.click(box)
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(screen.getByText('Saved.')).toBeInTheDocument())
    const put = calls.find(c => c.init?.method === 'PUT')!
    expect(put.url).toBe('/api/guild/Exordium/settings')
    expect(put.init?.credentials).toBe('include')
    expect(JSON.parse(String(put.init?.body))).toEqual({ officers_can_delete_parses: false })
    expect(await screen.findByText(/by Boss/)).toBeInTheDocument()
  })

  it('surfaces the server detail when the caller is not the leader', async () => {
    mockFetch({ officers_can_delete_parses: true }, 403)
    render(<GuildSettingsTab guildName="Exordium" />)
    fireEvent.click(await screen.findByRole('checkbox', { name: /Officers can delete guild parses/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/Guild leader access required/)).toBeInTheDocument()
  })
})
