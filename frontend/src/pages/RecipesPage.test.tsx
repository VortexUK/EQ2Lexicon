import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const h = vi.hoisted(() => ({
  throwingSet: () => { throw new DOMException('History API quota exhausted', 'SecurityError') },
}))

vi.mock('react-router-dom', async importOriginal => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(), h.throwingSet] as const,
  }
})

import RecipesPage from './RecipesPage'

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => ({ results: [], total: 0, page: 1, per_page: 25 }),
  })) as unknown as typeof fetch)
})

describe('RecipesPage URL mirror', () => {
  it('keeps responding to typing when setSearchParams throws a SecurityError', () => {
    render(<MemoryRouter><RecipesPage /></MemoryRouter>)
    const input = screen.getByPlaceholderText('Search…') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'sabre' } })
    expect(input.value).toBe('sabre')
  })
})
