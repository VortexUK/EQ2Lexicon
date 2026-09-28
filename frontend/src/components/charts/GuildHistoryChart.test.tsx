import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { GuildHistoryChart, type HistorySeries } from './GuildHistoryChart'
import { dayToUnix } from './chartUtils'
import type { GuildHistoryPoint } from '../../pages/guild/types'

function point(day: string, over: Partial<GuildHistoryPoint> = {}): GuildHistoryPoint {
  return {
    day,
    captured_at: dayToUnix(day),
    level: 300,
    members: 40,
    accounts: 30,
    achievement_count: 5,
    max_level_members: 10,
    distinct_classes: 8,
    ...over,
  }
}

const MEMBERS: HistorySeries = { dataKey: 'members', label: 'Members', colourVar: '--color-gold', fallback: '#c8a96e' }
const ACCOUNTS: HistorySeries = {
  dataKey: 'accounts',
  label: 'Accounts',
  colourVar: '--color-stat-secondary',
  fallback: '#00e5ff',
}

describe('dayToUnix', () => {
  it('is midday UTC so the calendar date survives every common timezone', () => {
    const t = dayToUnix('2026-09-28')
    expect(new Date(t * 1000).toISOString()).toBe('2026-09-28T12:00:00.000Z')
  })
})

describe('GuildHistoryChart', () => {
  it('shows the empty state rather than an SVG for fewer than two rows', () => {
    const { container } = render(<GuildHistoryChart rows={[point('2026-09-28')]} series={[MEMBERS]} width={600} />)
    expect(screen.getByText(/Not enough history yet/)).toBeInTheDocument()
    expect(container.querySelector('svg')).toBeNull()
  })

  it('renders one line per series in its fixed colour', () => {
    const rows = [point('2026-09-26', { members: 38 }), point('2026-09-27', { members: 39 }), point('2026-09-28')]
    const { container } = render(<GuildHistoryChart rows={rows} series={[MEMBERS, ACCOUNTS]} width={600} />)
    expect(container.querySelector('svg')).not.toBeNull()
    const curves = container.querySelectorAll('.recharts-line-curve')
    expect(curves).toHaveLength(2)
    expect(curves[0].getAttribute('stroke')).toBe('#c8a96e')
    expect(curves[1].getAttribute('stroke')).toBe('#00e5ff')
  })

  it('shows a legend only when two or more series share the chart', () => {
    const rows = [point('2026-09-27'), point('2026-09-28')]
    const one = render(<GuildHistoryChart rows={rows} series={[MEMBERS]} width={600} />)
    expect(one.container.querySelector('.recharts-legend-wrapper')).toBeNull()
    one.unmount()
    render(<GuildHistoryChart rows={rows} series={[MEMBERS, ACCOUNTS]} width={600} />)
    expect(screen.getByText('Members')).toBeInTheDocument()
    expect(screen.getByText('Accounts')).toBeInTheDocument()
  })

  it('keeps a series colour when the other series is removed', () => {
    const rows = [point('2026-09-27'), point('2026-09-28')]
    const { container } = render(<GuildHistoryChart rows={rows} series={[ACCOUNTS]} width={600} />)
    expect(container.querySelector('.recharts-line-curve')?.getAttribute('stroke')).toBe('#00e5ff')
  })
})
