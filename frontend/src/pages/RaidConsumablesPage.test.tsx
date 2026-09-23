import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import RaidConsumablesPage from './RaidConsumablesPage'
import { sheetForXpac } from '../data/raidConsumables'

const serverState: { currentXpac: string | null } = { currentXpac: 'RoK' }
vi.mock('../hooks/useServer', () => ({ useServer: () => serverState }))

describe('sheetForXpac', () => {
  it('matches the current xpac case-insensitively', () => {
    const picked = sheetForXpac('rok')
    expect(picked?.exact).toBe(true)
    expect(picked?.sheet.xpac).toBe('RoK')
  })

  it('falls back to the newest curated sheet for an uncurated tier', () => {
    const picked = sheetForXpac('TSO')
    expect(picked?.exact).toBe(false)
    expect(picked?.sheet.xpac).toBe('RoK')
  })
})

describe('RaidConsumablesPage', () => {
  it('renders the current tier sheet with item links and tags', () => {
    serverState.currentXpac = 'RoK'
    render(
      <MemoryRouter>
        <RaidConsumablesPage />
      </MemoryRouter>,
    )
    expect(screen.getByText('Raid Consumables')).toBeInTheDocument()
    expect(screen.getByText('Rise of Kunark (T8)')).toBeInTheDocument()
    const ale = screen.getByRole('link', { name: 'Overthere Reserve Ale' })
    expect(ale).toHaveAttribute('href', '/item/2044227135')
    expect(screen.getByText('5h · max power/HP')).toBeInTheDocument()
    expect(screen.getByText('Reductions')).toBeInTheDocument()
    // The crafted-consumable sections (all recipe-verified, no crate items).
    expect(screen.getByText('Temporary Adornments')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Smoldering Scroll of Tactics' })).toHaveAttribute(
      'href',
      '/item/1407485282',
    )
    expect(screen.getByText('Totems')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Spirit Totem of the Chokidai' })).toBeInTheDocument()
    expect(screen.getByText('Potions')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: "Expert's Elixir of Second Sight" })).toBeInTheDocument()
    expect(screen.queryByText(/No sheet curated/)).not.toBeInTheDocument()
  })

  it('notes the fallback when the current tier has no sheet yet', () => {
    serverState.currentXpac = 'TSO'
    render(
      <MemoryRouter>
        <RaidConsumablesPage />
      </MemoryRouter>,
    )
    expect(screen.getByText(/No sheet curated for TSO yet/)).toBeInTheDocument()
    expect(screen.getByText('Rise of Kunark (T8)')).toBeInTheDocument()
  })
})
