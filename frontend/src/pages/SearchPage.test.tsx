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

import { CharacterSearchPage } from './SearchPage'

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => ({ results: [], total: 0 }),
  })) as unknown as typeof fetch)
})

describe('SearchPage URL mirror', () => {
  it('keeps responding to typing when setSearchParams throws a SecurityError', () => {
    render(<MemoryRouter><CharacterSearchPage /></MemoryRouter>)
    const input = screen.getByRole('textbox') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'vortex' } })
    expect(input.value).toBe('vortex')
  })
})
