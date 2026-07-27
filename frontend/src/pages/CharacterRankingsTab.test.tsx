/**
 * CharacterRankingsTab unit tests — WCL-style table values, percentile
 * colouring, the Damage/Healing toggle, and the healer default.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import CharacterRankingsTab, { type CharacterRankings } from './CharacterRankingsTab'

let archetype = 'Fighter'
vi.mock('../useClasses', () => ({
  useClasses: () => ({
    classes: [],
    byName: new Map([['Templar', { archetype }]]),
    colourFor: () => 'var(--text)',
    iconUrlFor: () => null,
  }),
}))

const DATA: CharacterRankings = {
  name: 'Ranker',
  cls: 'Templar',
  expansions: [{ short: 'KoS', name: 'Kingdom of Sky' }],
  zones: [
    {
      zone: 'Deathtoll',
      scope: 'raid',
      expansion: 'KoS',
      dps_allstars: { points: 160.5, rank: 2, out_of: 7 },
      hps_allstars: { points: 88, rank: 3, out_of: 5 },
      bosses: [
        {
          boss: 'Tarinax the Destroyer',
          kills: 8,
          fastest_s: 530,
          fastest_encounter_id: 42,
          dps: { best_pct: 99, best_score: 40901.4, median_pct: 91, encounter_id: 41, points: 95.5, rank: 2, out_of: 7 },
          hps: { best_pct: 50, best_score: 3200, median_pct: 42, encounter_id: 40, points: 75, rank: 3, out_of: 5 },
        },
      ],
    },
  ],
}

function renderTab(data: CharacterRankings = DATA) {
  return render(<MemoryRouter><CharacterRankingsTab data={data} /></MemoryRouter>)
}

describe('CharacterRankingsTab', () => {
  it('renders the boss row with WCL colours, links and All Stars', () => {
    archetype = 'Fighter'
    renderTab()
    // Zone header + scope + All Stars summary
    expect(screen.getByText('Deathtoll')).toBeInTheDocument()
    expect(screen.getByText('Raid')).toBeInTheDocument()
    expect(screen.getByText('160.50 pts')).toBeInTheDocument()
    expect(screen.getByText(/#2 of 7 Templars/)).toBeInTheDocument()
    // Boss row values
    const boss = screen.getByRole('link', { name: 'Tarinax the Destroyer' })
    expect(boss).toHaveAttribute('href', '/parse/41')
    expect(screen.getByText('99')).toHaveStyle({ color: '#e268a8' }) // 99 = pink
    expect(screen.getByText('40,901.4')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '8m50s' })).toHaveAttribute('href', '/parse/42')
    expect(screen.getByText('91')).toHaveStyle({ color: '#a335ee' }) // 75+ = purple
    expect(screen.getByText('95.50')).toBeInTheDocument()
    expect(screen.getByText('#2 / 7')).toBeInTheDocument()
    expect(screen.getByText('Highest DPS')).toBeInTheDocument()
  })

  it('toggles to the Healing board', () => {
    archetype = 'Fighter'
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Healing' }))
    expect(screen.getByText('Highest HPS')).toBeInTheDocument()
    expect(screen.getByText('3,200')).toBeInTheDocument()
    expect(screen.getByText('88.00 pts')).toBeInTheDocument()
  })

  it('healers default to the Healing board', () => {
    archetype = 'Priest'
    renderTab()
    expect(screen.getByText('Highest HPS')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Damage' }))
    expect(screen.getByText('Highest DPS')).toBeInTheDocument()
  })

  it('expansion dropdown filters zone sections, newest first by default', () => {
    archetype = 'Fighter'
    renderTab({
      ...DATA,
      expansions: [
        { short: 'RoK', name: 'Rise of Kunark' },
        { short: 'EoF', name: 'Echoes of Faydwer' },
      ],
      zones: [
        { ...DATA.zones[0], zone: 'Veeshan\'s Peak', expansion: 'RoK' },
        { ...DATA.zones[0], zone: 'Emerald Halls', expansion: 'EoF' },
      ],
    })
    // Newest expansion selected by default → only its zone shows.
    expect(screen.getByText("Veeshan's Peak")).toBeInTheDocument()
    expect(screen.queryByText('Emerald Halls')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Expansion'), { target: { value: 'EoF' } })
    expect(screen.getByText('Emerald Halls')).toBeInTheDocument()
    expect(screen.queryByText("Veeshan's Peak")).not.toBeInTheDocument()
  })

  it('shows dashes for a metric the character never parsed', () => {
    archetype = 'Fighter'
    renderTab({
      ...DATA,
      zones: [{ ...DATA.zones[0], hps_allstars: null, bosses: [{ ...DATA.zones[0].bosses[0], hps: null }] }],
    })
    fireEvent.click(screen.getByRole('button', { name: 'Healing' }))
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)
    // Fastest still links even without metric stats
    expect(screen.getByRole('link', { name: '8m50s' })).toBeInTheDocument()
  })
})
