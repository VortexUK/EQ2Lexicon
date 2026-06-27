/**
 * ClaimsTable — search + 10-per-page pagination (shared usePagedSearch engine).
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { ClaimsTable } from './ClaimsTable'
import type { ClaimDetail } from './types'

function makeClaims(n: number): ClaimDetail[] {
  return Array.from({ length: n }, (_, i): ClaimDetail => ({
    id: i + 1,
    discord_id: `9000000000000${String(i).padStart(5, '0')}`,  // numeric snowflake (discordAvatarUrl does BigInt())
    discord_name: `User${String(i).padStart(2, '0')}`,
    discord_username: `user${String(i).padStart(2, '0')}`,
    avatar: null,
    character_name: `Char${String(i).padStart(2, '0')}`,
    status: 'pending',
    requested_at: 1700000000,
    reviewed_at: null,
    reviewed_by: null,
    note: null,
  }))
}

describe('ClaimsTable pagination + search', () => {
  it('shows at most 10 rows with a page indicator', () => {
    render(<ClaimsTable claims={makeClaims(25)} onAction={() => {}} />)
    expect(screen.getByText('Char00')).toBeInTheDocument()
    expect(screen.getByText('Char09')).toBeInTheDocument()
    expect(screen.queryByText('Char10')).not.toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
  })

  it('Next moves the page window', () => {
    render(<ClaimsTable claims={makeClaims(25)} onAction={() => {}} />)
    fireEvent.click(screen.getByText('Next'))
    expect(screen.getByText('Char10')).toBeInTheDocument()
    expect(screen.queryByText('Char00')).not.toBeInTheDocument()
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument()
  })

  it('search filters by character name and collapses pagination', () => {
    render(<ClaimsTable claims={makeClaims(25)} onAction={() => {}} />)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Char24' } })
    expect(screen.getByText('Char24')).toBeInTheDocument()
    expect(screen.queryByText('Char00')).not.toBeInTheDocument()
    expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument()
  })
})
