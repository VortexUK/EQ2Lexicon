import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'

const stream = { health: 'up' as 'up' | 'down' | 'unknown' }
vi.mock('../hooks/useCensusStream', () => ({
  useCensusStream: () => ({ health: stream.health, connected: true, subscribe: () => () => {} }),
}))

import { FreshnessBadge } from './FreshnessBadge'

describe('FreshnessBadge', () => {
  it('renders nothing for fresh data', () => {
    const { container } = render(<FreshnessBadge stale={false} refreshing />)
    expect(container.textContent).toBe('')
  })

  it('only claims to be updating when a refresh actually started', () => {
    stream.health = 'up'
    expect(render(<FreshnessBadge stale refreshing />).container.textContent).toBe('Updating from Census…')
    expect(render(<FreshnessBadge stale refreshing={false} />).container.textContent).toBe('Showing stored data')
    expect(render(<FreshnessBadge stale />).container.textContent).toBe('Showing stored data')
  })

  it('reports Census down regardless of the refreshing flag', () => {
    stream.health = 'down'
    expect(render(<FreshnessBadge stale refreshing />).container.textContent).toBe(
      'Census unavailable — showing stored data',
    )
  })
})
