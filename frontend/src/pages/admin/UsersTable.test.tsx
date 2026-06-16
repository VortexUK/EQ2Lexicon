/**
 * UsersTable — admin user list pagination + search.
 *
 * Pins the contract:
 *   * at most 10 rows render per page (PER_PAGE)
 *   * Prev/Next move the window and the "Page X of Y" indicator tracks it
 *   * the search box filters by display name / username (resets to page 1)
 *   * pagination controls hide once the filtered set fits on one page
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { UsersTable } from './UsersTable'
import type { UserItem } from './types'

function makeUsers(n: number): UserItem[] {
  return Array.from({ length: n }, (_, i): UserItem => ({
    discord_id: `9000000000000${String(i).padStart(5, '0')}`,  // numeric snowflake (discordAvatarUrl does BigInt())
    discord_name: `Player${String(i).padStart(2, '0')}`,
    discord_username: `player${String(i).padStart(2, '0')}`,
    avatar: null,
    first_seen: 1700000000,
    last_seen: 1700000000,
    access_status: 'approved',
    claim_count: 0,
    roles: [],
  }))
}

describe('UsersTable pagination + search', () => {
  it('shows at most 10 rows and a page indicator', () => {
    render(<UsersTable users={makeUsers(25)} onAction={() => {}} />)
    // Page 1: first ten present, eleventh not.
    expect(screen.getByText('Player00')).toBeInTheDocument()
    expect(screen.getByText('Player09')).toBeInTheDocument()
    expect(screen.queryByText('Player10')).not.toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
  })

  it('Next/Prev move the page window', () => {
    render(<UsersTable users={makeUsers(25)} onAction={() => {}} />)
    fireEvent.click(screen.getByText('Next'))
    expect(screen.getByText('Player10')).toBeInTheDocument()
    expect(screen.queryByText('Player00')).not.toBeInTheDocument()
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Prev'))
    expect(screen.getByText('Player00')).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
  })

  it('search filters the list and collapses pagination when it fits one page', () => {
    render(<UsersTable users={makeUsers(25)} onAction={() => {}} />)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Player24' } })
    expect(screen.getByText('Player24')).toBeInTheDocument()
    expect(screen.queryByText('Player00')).not.toBeInTheDocument()
    // One match → no pagination controls.
    expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument()
  })

  it('no pagination controls when there are 10 or fewer users', () => {
    render(<UsersTable users={makeUsers(10)} onAction={() => {}} />)
    expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument()
    expect(screen.getByText('Player09')).toBeInTheDocument()
  })
})
