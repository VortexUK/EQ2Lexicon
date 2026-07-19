/**
 * TamperReportsTable — admin audit channel UI tests.
 *
 * Pin the contract with /api/admin/tamper-reports + acknowledge route:
 *   * default fetch uses status=pending
 *   * pending_count badge always shows the working-set size
 *   * Acknowledge button POSTs to the right URL and triggers a reload
 *   * unknown reason codes render verbatim with muted styling (forward
 *     compat with future plugin codes)
 *   * the empty-state copy distinguishes "no pending" (✓) from the
 *     filtered-empty case
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { TamperReportsTable } from './TamperReportsTable'

const baseRow = {
  id: 7,
  world: 'Varsoon',
  act_encid: 'ABCD1234',
  title: 'a krait patriarch',
  zone: 'Great Divide',
  started_at: 1700000000,
  ended_at: 1700000046,
  duration_s: 46,
  total_damage: 500000,
  encdps: 1234.5,
  reason: 'title_enemy_mismatch',
  reported_at: 1700000050,
  uploader_logger_name: 'Menludiir',
  uploader_discord_id: 'discord-7',
  uploader_discord_name: 'Player 7',
  guild_name: 'Exordium',
  acknowledged_at: null,
  acknowledged_by: null,
}

function mockFetchOnce(response: object, ok = true, status = 200) {
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({
      ok,
      status,
      json: () => Promise.resolve(response),
    }),
  ) as unknown as typeof fetch
}

function mockFetchSequence(responses: Array<{ ok?: boolean; body: object }>) {
  let i = 0
  globalThis.fetch = vi.fn(() => {
    const next = responses[i++] ?? { ok: true, body: {} }
    return Promise.resolve({
      ok: next.ok ?? true,
      status: 200,
      json: () => Promise.resolve(next.body),
    })
  }) as unknown as typeof fetch
}

describe('TamperReportsTable', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('defaults to status=pending on first load', async () => {
    mockFetchOnce({ results: [], pending_count: 0 })

    render(<TamperReportsTable />)

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/admin/tamper-reports?status=pending',
        expect.any(Object),
      )
    })
  })

  it('renders the pending-count badge alongside the table title', async () => {
    mockFetchOnce({
      results: [baseRow],
      pending_count: 3,
    })

    render(<TamperReportsTable />)

    expect(await screen.findByText('3 pending')).toBeInTheDocument()
  })

  it('renders the rename-detection reason as a human label', async () => {
    mockFetchOnce({
      results: [baseRow],
      pending_count: 1,
    })

    render(<TamperReportsTable />)

    // Server emits the wire code; UI presents the friendlier label.
    expect(await screen.findByText('Rename detected')).toBeInTheDocument()
  })

  it('renders an unknown reason code verbatim (forward-compat)', async () => {
    mockFetchOnce({
      results: [{ ...baseRow, reason: 'brand_new_heuristic_v2' }],
      pending_count: 1,
    })

    render(<TamperReportsTable />)

    expect(await screen.findByText('brand_new_heuristic_v2')).toBeInTheDocument()
  })

  it('shows the ✓ empty-state when there are no pending reports', async () => {
    mockFetchOnce({ results: [], pending_count: 0 })

    render(<TamperReportsTable />)

    expect(await screen.findByText(/No pending tamper reports/)).toBeInTheDocument()
  })

  it('Acknowledge button POSTs to the right URL and reloads', async () => {
    mockFetchSequence([
      // initial GET — one pending row
      { body: { results: [baseRow], pending_count: 1 } },
      // POST acknowledge
      { body: { acknowledged: true } },
      // reload GET — empty now
      { body: { results: [], pending_count: 0 } },
    ])

    render(<TamperReportsTable />)

    const button = await screen.findByRole('button', { name: 'Acknowledge' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/admin/tamper-reports/7/acknowledge',
        expect.objectContaining({
          method: 'POST',
          credentials: 'include',
        }),
      )
    })

    // After the acknowledge succeeds the table reloads — the empty
    // state copy now shows.
    expect(await screen.findByText(/No pending tamper reports/)).toBeInTheDocument()
  })

  it('does not render an Acknowledge button for already-acknowledged rows', async () => {
    mockFetchOnce({
      results: [
        {
          ...baseRow,
          acknowledged_at: 1700001000,
          acknowledged_by: 'admin-prev',
        },
      ],
      pending_count: 0,
    })

    render(<TamperReportsTable />)

    await screen.findByText('Rename detected')
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull()
    // The "Acknowledged" text appears in two places — the filter chip
    // Button and the row-status Badge. Both must be present; we assert
    // ≥ 2 matches rather than a single one.
    const ackLabels = screen.getAllByText('Acknowledged')
    expect(ackLabels.length).toBeGreaterThanOrEqual(2)
  })

  it('switching to the "Acknowledged" filter fetches status=ack', async () => {
    mockFetchSequence([
      { body: { results: [], pending_count: 0 } },
      { body: { results: [], pending_count: 0 } },
    ])

    render(<TamperReportsTable />)

    const ackButton = await screen.findByRole('button', { name: 'Acknowledged' })
    fireEvent.click(ackButton)

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/admin/tamper-reports?status=ack',
        expect.any(Object),
      )
    })
  })
})

describe('TamperReportsTable bulk acknowledge', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('select-all + Acknowledge selected POSTs the batch and reloads', async () => {
    const rowA = { ...baseRow, id: 11, title: 'a krait one' }
    const rowB = { ...baseRow, id: 12, title: 'a krait two' }
    const acked = { ...baseRow, id: 13, title: 'a krait three', acknowledged_at: 1700000099, acknowledged_by: 'x' }
    mockFetchSequence([
      { body: { results: [rowA, rowB, acked], pending_count: 2 } }, // initial load ("all"-shaped is fine)
      { body: { acknowledged: 2 } },                                 // batch POST
      { body: { results: [acked], pending_count: 0 } },              // reload
    ])

    render(<TamperReportsTable />)
    await screen.findByText('a krait one')

    // Header checkbox selects only the pending rows (the ack'd row has no checkbox).
    fireEvent.click(screen.getByLabelText('Select all pending reports'))
    const bulkBtn = screen.getByRole('button', { name: /Acknowledge selected \(2\)/ })
    fireEvent.click(bulkBtn)

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      const batch = calls.find(c => String(c[0]).includes('acknowledge-batch'))
      expect(batch).toBeTruthy()
      expect(JSON.parse((batch![1] as RequestInit).body as string)).toEqual({ ids: [11, 12] })
    })
    // Reload happened — the two acknowledged rows dropped out.
    expect(await screen.findByText('a krait three')).toBeInTheDocument()
    expect(screen.queryByText('a krait one')).not.toBeInTheDocument()
  })

  it('the bulk button is disabled with nothing selected', async () => {
    mockFetchOnce({ results: [baseRow], pending_count: 1 })
    render(<TamperReportsTable />)
    await screen.findByText('a krait patriarch')
    expect(screen.getByRole('button', { name: /Acknowledge selected \(0\)/ })).toBeDisabled()
  })
})
