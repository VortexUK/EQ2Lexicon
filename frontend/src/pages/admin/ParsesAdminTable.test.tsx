/**
 * ParsesAdminTable — the bulk-restore workflow added after the 2026-09-27
 * guild-wide delete: "Hidden only" narrows the list, "Select all hidden"
 * ticks the soft-deleted rows, "Unhide selected" POSTs their ids to the
 * batch unhide route in chunks and reloads.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { ParsesAdminTable } from './ParsesAdminTable'
import { PARSE_BATCH_CHUNK_SIZE } from '../parses/api'
import type { AdminParse } from './types'

interface Recorded { url: string; method: string }
let calls: Recorded[]

function parse(id: number, hidden: boolean): AdminParse {
  return {
    id,
    title: `Boss ${id}`,
    zone: 'Veeshan’s Peak',
    guild_name: 'Exordium',
    uploaded_by: 'Menludiir',
    started_at: 1_700_000_000 - id,
    success_level: 1,
    player_count: 24,
    hidden,
    hidden_at: hidden ? 1_700_000_500 : null,
    hidden_by: hidden ? '346418357693841418' : null,
    hidden_by_name: hidden ? 'Doorian' : null,
    client_warnings: null,
  }
}

function mockFetch(rows: AdminParse[]) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      if (method === 'POST') {
        const ids = new URL(url, 'http://test').searchParams.get('ids')!.split(',')
        return { ok: true, status: 200, json: async () => ({ unhidden: ids.length }) }
      }
      const hiddenOnly = new URL(url, 'http://test').searchParams.get('hidden') === 'true'
      return { ok: true, status: 200, json: async () => (hiddenOnly ? rows.filter(r => r.hidden) : rows) }
    }) as unknown as typeof fetch,
  )
}

function renderTable() {
  return render(
    <MemoryRouter>
      <ParsesAdminTable />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('confirm', vi.fn(() => true))
})

describe('ParsesAdminTable bulk unhide', () => {
  it('selects only hidden rows and posts their ids to the batch unhide route', async () => {
    mockFetch([parse(1, true), parse(2, false), parse(3, true)])
    renderTable()
    await screen.findByText('Boss 1')

    const unhideBtn = screen.getByRole('button', { name: /Unhide selected \(0\)/ })
    expect(unhideBtn).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /Select all hidden \(2\)/ }))
    expect(screen.getByRole('checkbox', { name: 'Select Boss 1' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Boss 2' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Boss 3' })).toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: /Unhide selected \(2\)/ }))
    await waitFor(() => expect(calls.filter(c => c.method === 'POST')).toHaveLength(1))
    expect(calls.find(c => c.method === 'POST')!.url).toBe('/api/parses/batch/unhide?ids=1,3')
    // Reloads the list afterwards.
    await waitFor(() => expect(calls.filter(c => c.method === 'GET')).toHaveLength(2))
  })

  it('counts only the hidden rows in a mixed selection', async () => {
    mockFetch([parse(1, true), parse(2, false)])
    renderTable()
    await screen.findByText('Boss 1')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all visible parses' }))
    expect(screen.getByRole('button', { name: /Purge selected \(2\)/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Unhide selected \(1\)/ })).toBeEnabled()
  })

  it('chunks a large restore at the batch size', async () => {
    const rows = Array.from({ length: PARSE_BATCH_CHUNK_SIZE + 1 }, (_, i) => parse(i + 1, true))
    mockFetch(rows)
    renderTable()
    await screen.findByText('Boss 1')
    fireEvent.click(screen.getByRole('button', { name: /Select all hidden/ }))
    fireEvent.click(screen.getByRole('button', { name: /Unhide selected/ }))
    await waitFor(() => expect(calls.filter(c => c.method === 'POST')).toHaveLength(2))
    const posted = calls.filter(c => c.method === 'POST').map(c => new URL(c.url, 'http://test').searchParams.get('ids')!.split(',').length)
    expect(posted).toEqual([PARSE_BATCH_CHUNK_SIZE, 1])
  })

  it('"Hidden only" refetches with hidden=true and drops visible rows', async () => {
    mockFetch([parse(1, true), parse(2, false)])
    renderTable()
    await screen.findByText('Boss 2')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hidden only' }))
    await waitFor(() => expect(screen.queryByText('Boss 2')).toBeNull())
    expect(calls[calls.length - 1].url).toBe('/api/admin/parses?search=&hidden=true')
    expect(screen.getByText('Boss 1')).toBeInTheDocument()
  })

  it('sends nothing when the confirm is cancelled', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
    mockFetch([parse(1, true)])
    renderTable()
    await screen.findByText('Boss 1')
    fireEvent.click(screen.getByRole('button', { name: /Select all hidden/ }))
    fireEvent.click(screen.getByRole('button', { name: /Unhide selected \(1\)/ }))
    expect(calls.filter(c => c.method === 'POST')).toHaveLength(0)
  })
})
