/**
 * ParsesAdminTable — the bulk-restore workflow: "Hidden only" narrows the
 * list, "Select all hidden" ticks the soft-deleted rows, "Unhide selected"
 * POSTs their ids to the batch unhide route in chunks and reloads.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { ParsesAdminTable } from './ParsesAdminTable'
import { PARSE_BATCH_CHUNK_SIZE } from '../parses/api'
import type { AdminParse } from './types'

interface Recorded { url: string; method: string }
let calls: Recorded[]

// Allowance for the one test that renders a full batch of rows (see it).
// Per test only: the global vitest timeout stays at its default.
const SLOW_TEST_MS = 30_000
const SLOW_RENDER_MS = 15_000

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
      const hiddenOnly = new URL(url, 'http://test').searchParams.get('status') === 'hidden'
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

  // Explicit timeouts: this renders one row more than a batch (201 rows) in
  // jsdom and re-renders them as the selection and the reload land (about
  // 700 ms alone). It has overrun vitest's default 5 s when the suite shares
  // the CPU with other work. The row count is the point of the test, so it
  // gets time, not fewer rows.
  it('chunks a large restore at the batch size', async () => {
    const rows = Array.from({ length: PARSE_BATCH_CHUNK_SIZE + 1 }, (_, i) => parse(i + 1, true))
    mockFetch(rows)
    renderTable()
    await screen.findByText('Boss 1', {}, { timeout: SLOW_RENDER_MS })
    fireEvent.click(screen.getByRole('button', { name: /Select all hidden/ }))
    fireEvent.click(screen.getByRole('button', { name: /Unhide selected/ }))
    await waitFor(() => expect(calls.filter(c => c.method === 'POST')).toHaveLength(2), { timeout: SLOW_RENDER_MS })
    const posted = calls.filter(c => c.method === 'POST').map(c => new URL(c.url, 'http://test').searchParams.get('ids')!.split(',').length)
    expect(posted).toEqual([PARSE_BATCH_CHUNK_SIZE, 1])
  }, SLOW_TEST_MS)

  it('the "Hidden only" status filter refetches with status=hidden and drops visible rows', async () => {
    mockFetch([parse(1, true), parse(2, false)])
    renderTable()
    await screen.findByText('Boss 2')
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by status' }), { target: { value: 'hidden' } })
    await waitFor(() => expect(screen.queryByText('Boss 2')).toBeNull())
    expect(calls[calls.length - 1].url).toBe('/api/admin/parses?status=hidden')
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

// One filter per column, ANDed by the server. The table once had a single
// search box, so zone OR uploader OR title, never two at once.
describe('ParsesAdminTable column filters', () => {
  const lastGet = () => {
    const gets = calls.filter(c => c.method === 'GET')
    return new URL(gets[gets.length - 1].url, 'http://test').searchParams
  }

  it('sends every column filter together in one request', async () => {
    mockFetch([parse(1, false)])
    renderTable()
    await screen.findByText('Boss 1')
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter by zone' }), { target: { value: 'thundering' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter by uploader' }), { target: { value: 'menl' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by result' }), { target: { value: '1' } })
    await waitFor(() => expect(lastGet().get('uploader')).toBe('menl'))
    const q = lastGet()
    expect(q.get('zone')).toBe('thundering')
    expect(q.get('result')).toBe('1')
  })

  it('waits for typing to pause before refetching', async () => {
    mockFetch([parse(1, false)])
    renderTable()
    await screen.findByText('Boss 1')
    const before = calls.length
    const box = screen.getByRole('textbox', { name: 'Filter by title' })
    for (const v of ['c', 'co', 'cor', 'corn']) fireEvent.change(box, { target: { value: v } })
    await waitFor(() => expect(lastGet().get('title')).toBe('corn'))
    expect(calls.length - before).toBe(1)
  })

  it('"Load all matching" pages with the keyset cursor until a short page', async () => {
    const first = Array.from({ length: 200 }, (_, i) => parse(i + 1, false))
    const second = Array.from({ length: 50 }, (_, i) => parse(i + 201, false))
    calls = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push({ url, method: 'GET' })
        const q = new URL(url, 'http://test').searchParams
        return { ok: true, status: 200, json: async () => (q.get('before') ? second : first) }
      }) as unknown as typeof fetch,
    )
    renderTable()
    await screen.findByText('Boss 1', {}, { timeout: SLOW_RENDER_MS })
    fireEvent.click(screen.getByRole('button', { name: 'Load all matching' }))
    await screen.findByText('Boss 250', {}, { timeout: SLOW_RENDER_MS })
    const q = lastGet()
    expect(q.get('limit')).toBe('1000')
    expect(q.get('before')).toBe(String(first[199].started_at))
    expect(q.get('before_id')).toBe('200')
    expect(screen.queryByRole('button', { name: 'Load all matching' })).toBeNull()  // nothing more to load
  }, SLOW_TEST_MS)
})

// The ⚠ chip once showed for any non-empty client_warnings, and EQ2Parser
// stamps `log_writer_eq2` (the game held the log: the good case) on every
// automatic upload, so every parse from the desktop parser carried it. Which
// values count is decided in clientWarnings.ts.
describe('ParsesAdminTable client-warning chip', () => {
  async function chipFor(clientWarnings: string[] | null): Promise<HTMLElement | null> {
    mockFetch([{ ...parse(1, false), client_warnings: clientWarnings }])
    renderTable()
    await screen.findByText('Boss 1')
    return screen.queryByLabelText(/Client warnings/)
  }

  it('shows no chip when the upload carried no warnings', async () => {
    expect(await chipFor(null)).toBeNull()
  })

  it('shows no chip for the benign EQ2Parser stamp alone', async () => {
    expect(await chipFor(['log_writer_eq2'])).toBeNull()
  })

  it.each(['log_writer_unverified', 'log_foreign_holder', 'folder_hint_mismatch'])(
    'shows the chip for %s',
    async code => {
      const chip = await chipFor([code])
      expect(chip).not.toBeNull()
      expect(chip).toHaveTextContent('⚠ 1')
      expect(chip).toHaveAttribute('title', expect.stringContaining(code))
    },
  )

  it('shows the chip for a value no client sends today', async () => {
    const chip = await chipFor(['brand_new_signal_v2'])
    expect(chip).toHaveTextContent('⚠ 1')
    expect(chip).toHaveAttribute('title', expect.stringContaining('brand_new_signal_v2'))
  })

  it('counts only the values needing attention but lists every value as sent', async () => {
    const chip = await chipFor(['log_writer_eq2', 'log_foreign_holder'])
    expect(chip).toHaveTextContent('⚠ 1')  // not 2: log_writer_eq2 is not a warning
    const title = chip!.getAttribute('title')!
    expect(title).toContain('needing a look: log_foreign_holder.')
    // The benign stamp is still there for an admin who wants the detail.
    expect(title).toContain('Everything the client sent: log_writer_eq2, log_foreign_holder')
    expect(chip).toHaveAttribute('aria-label', title)
  })

  it('counts two when two values need attention', async () => {
    const chip = await chipFor(['log_writer_unverified', 'log_foreign_holder'])
    expect(chip).toHaveTextContent('⚠ 2')
  })
})

// ACT's success level: 1 = win, 2 = partial / unclear, 3 = loss. The column
// once read 2 as "loss" and 3 as "mixed".
describe('ParsesAdminTable Result column', () => {
  const RESULT_COL = 8  // checkbox, #, Title, Zone, Guild, Uploader, Date, Players, Result

  async function resultFor(successLevel: number): Promise<string | null> {
    mockFetch([{ ...parse(1, false), success_level: successLevel }])
    renderTable()
    const row = (await screen.findByText('Boss 1')).closest('tr')!
    return row.cells[RESULT_COL].textContent
  }

  it('has Result as the ninth column', async () => {
    mockFetch([parse(1, false)])
    renderTable()
    await screen.findByText('Boss 1')
    expect(screen.getAllByRole('columnheader')[RESULT_COL]).toHaveTextContent('Result')
  })

  it('labels 1 "win"', async () => {
    expect(await resultFor(1)).toBe('win')
  })

  it('labels 2 "partial", not "loss"', async () => {
    expect(await resultFor(2)).toBe('partial')
  })

  it('labels 3 "loss", not "mixed"', async () => {
    expect(await resultFor(3)).toBe('loss')
  })

  it('shows a dash for 0 (unknown)', async () => {
    expect(await resultFor(0)).toBe('—')
  })
})
