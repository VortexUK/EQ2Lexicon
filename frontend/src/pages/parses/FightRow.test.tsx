/**
 * FightRow title colour by ACT outcome.
 *
 * `success_level` is ACT's GetEncounterSuccessLevel(): 1 = win, 2 = partial /
 * unclear, 3 = loss. The list once showed 2 in red and 3 in amber (the two
 * were the wrong way round); the mapping now lives in encounterOutcome.ts.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { FightRow } from './FightRow'
import type { ParseEncounterSummary, ParseUploadSummary } from './types'

function mkUpload(id: number, successLevel: number): ParseUploadSummary {
  return {
    id,
    uploaded_by: `Raider${id}`,
    uploader_discord_id: null,
    uploader_display_name: null,
    started_at: 1_700_000_000,
    duration_s: 60,
    total_damage: 1000,
    encdps: 100,
    success_level: successLevel,
    permissions: { can_delete: false },
  }
}

function mkFight(successLevel: number, uploadIds: number[] = [1]): ParseEncounterSummary {
  return {
    id: uploadIds[0],
    act_encid: 'enc-1',
    title: 'Tarinax',
    zone: 'Deathtoll',
    started_at: 1_700_000_000,
    ended_at: 1_700_000_060,
    duration_s: 60,
    total_damage: 1000,
    encdps: 100,
    kills: 1,
    deaths: 0,
    success_level: successLevel,
    combatant_count: 24,
    player_count: 24,
    category: 'raid',
    uploaded_by: 'Raider1',
    uploader_discord_id: null,
    uploader_display_name: null,
    guild_name: 'Exordium',
    permissions: { can_delete: false },
    uploads: uploadIds.map(u => mkUpload(u, successLevel)),
  }
}

function renderRow(fight: ParseEncounterSummary) {
  return render(
    <MemoryRouter>
      <FightRow fight={fight} onDeleted={vi.fn()} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  // UploaderTag loads the supporter list once; keep it off the network.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ supporter_ids: [] }) })) as unknown as typeof fetch,
  )
})

describe('FightRow title colour by outcome', () => {
  const linkColor = (successLevel: number): string => {
    renderRow(mkFight(successLevel))
    return screen.getByRole('link', { name: 'Tarinax' }).style.color
  }

  it('shows a win (1) in the success colour', () => {
    expect(linkColor(1)).toBe('var(--color-success)')
  })

  it('shows a partial outcome (2) in the warning colour', () => {
    expect(linkColor(2)).toBe('var(--color-warning)')
  })

  it('shows a loss (3) in the danger colour', () => {
    expect(linkColor(3)).toBe('var(--color-danger)')
  })

  it('leaves an unknown outcome (0) as plain text', () => {
    expect(linkColor(0)).toBe('var(--text)')
  })

  it('applies the same colours to a multi-upload fight, whose title is a toggle', () => {
    const toggleColor = (successLevel: number): string => {
      const { unmount } = renderRow(mkFight(successLevel, [1, 2]))
      const color = screen.getByRole('button', { name: /Tarinax/ }).style.color
      unmount()
      return color
    }
    expect(toggleColor(2)).toBe('var(--color-warning)')
    expect(toggleColor(3)).toBe('var(--color-danger)')
  })
})
