/**
 * RoleRequestsTable — search + 10-per-page pagination (shared usePagedSearch engine).
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { RoleRequestsTable } from './RoleRequestsTable'
import type { RoleRequest } from './types'

function makeRequests(n: number): RoleRequest[] {
  return Array.from({ length: n }, (_, i): RoleRequest => ({
    id: i + 1,
    discord_id: `9000000000000${String(i).padStart(5, '0')}`,  // numeric snowflake (discordAvatarUrl does BigInt())
    discord_name: `Req${String(i).padStart(2, '0')}`,
    discord_username: `req${String(i).padStart(2, '0')}`,
    avatar: null,
    role: 'contributor',
    status: 'pending',
    requested_at: 1700000000,
    reviewed_at: null,
    reviewed_by: null,
    user_note: null,
    admin_note: null,
  }))
}

describe('RoleRequestsTable pagination + search', () => {
  it('shows at most 10 rows with a page indicator', () => {
    render(<RoleRequestsTable requests={makeRequests(25)} onAction={() => {}} />)
    expect(screen.getByText('Req00')).toBeInTheDocument()
    expect(screen.getByText('Req09')).toBeInTheDocument()
    expect(screen.queryByText('Req10')).not.toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
  })

  it('Next moves the page window', () => {
    render(<RoleRequestsTable requests={makeRequests(25)} onAction={() => {}} />)
    fireEvent.click(screen.getByText('Next'))
    expect(screen.getByText('Req10')).toBeInTheDocument()
    expect(screen.queryByText('Req00')).not.toBeInTheDocument()
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument()
  })

  it('search filters by requester and collapses pagination', () => {
    render(<RoleRequestsTable requests={makeRequests(25)} onAction={() => {}} />)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Req24' } })
    expect(screen.getByText('Req24')).toBeInTheDocument()
    expect(screen.queryByText('Req00')).not.toBeInTheDocument()
    expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument()
  })

  it('no search box or pager for a small list', () => {
    render(<RoleRequestsTable requests={makeRequests(5)} onAction={() => {}} />)
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument()
  })
})
