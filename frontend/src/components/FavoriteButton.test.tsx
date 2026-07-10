import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

import FavoriteButton from './FavoriteButton'

type Status = { count: number; favorited_by_me: boolean }

/** Stub fetch: GET returns `initial`; PUT/DELETE resolve via a held promise so
 * tests can observe the optimistic state before the server answers. */
function mockFetch(initial: Status) {
  let releaseMutation!: (r: { ok: boolean; status?: number; body?: unknown }) => void
  const gate = new Promise<{ ok: boolean; status?: number; body?: unknown }>(res => { releaseMutation = res })
  const calls: { url: string; method: string }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET'
    calls.push({ url, method })
    if (method === 'GET') {
      return { ok: true, status: 200, json: async () => initial }
    }
    const r = await gate
    return {
      ok: r.ok,
      status: r.status ?? (r.ok ? 200 : 500),
      json: async () => r.body ?? {},
    }
  }) as unknown as typeof fetch)
  return { releaseMutation, calls }
}

beforeEach(() => vi.restoreAllMocks())

describe('FavoriteButton', () => {
  it('renders the star with count and aria-pressed from the initial status', async () => {
    mockFetch({ count: 3, favorited_by_me: false })
    render(<FavoriteButton name="Menludiir" />)
    const btn = await screen.findByRole('button')
    expect(btn).toHaveAttribute('aria-pressed', 'false')
    expect(btn.textContent).toBe('☆')
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('optimistically flips before the PUT resolves, then reconciles', async () => {
    const { releaseMutation, calls } = mockFetch({ count: 3, favorited_by_me: false })
    render(<FavoriteButton name="Menludiir" />)
    const btn = await screen.findByRole('button')

    fireEvent.click(btn)
    // Optimistic: starred + count bumped while the PUT is still in flight.
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    expect(btn.textContent).toBe('★')
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(calls.some(c => c.method === 'PUT')).toBe(true)

    // Server reconciles with a different count (someone else favourited too).
    await act(async () => { releaseMutation({ ok: true, body: { count: 5, favorited_by_me: true } }) })
    await waitFor(() => expect(screen.getByText('5')).toBeInTheDocument())
    expect(btn).toHaveAttribute('aria-pressed', 'true')
  })

  it('rolls back and shows the error when the mutation fails', async () => {
    const { releaseMutation } = mockFetch({ count: 3, favorited_by_me: false })
    render(<FavoriteButton name="Menludiir" />)
    const btn = await screen.findByRole('button')

    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'true') // optimistic

    await act(async () => {
      releaseMutation({ ok: false, status: 409, body: { detail: 'Favourite limit reached (50 per server).' } })
    })
    await waitFor(() => expect(btn).toHaveAttribute('aria-pressed', 'false')) // rolled back
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText(/Favourite limit reached/)).toBeInTheDocument()
  })

  it('unfavourites with DELETE when already favorited', async () => {
    const { releaseMutation, calls } = mockFetch({ count: 3, favorited_by_me: true })
    render(<FavoriteButton name="Menludiir" />)
    const btn = await screen.findByRole('button')
    expect(btn.textContent).toBe('★')

    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'false') // optimistic un-star
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(calls.some(c => c.method === 'DELETE')).toBe(true)
    await act(async () => { releaseMutation({ ok: true, body: { count: 2, favorited_by_me: false } }) })
  })
})
