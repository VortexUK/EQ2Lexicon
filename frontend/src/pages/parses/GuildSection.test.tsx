/**
 * GuildSection header delete: sends exactly the VISIBLE upload ids through
 * the batch endpoint, chunked, and keeps the page's state truthful on
 * partial failure.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { GuildSection } from './GuildSection'
import { chunkFightsForDelete, PARSE_BATCH_CHUNK_SIZE } from './api'
import { NO_GUILD } from './types'
import type { GuildBucket, ParseEncounterSummary, ParseUploadSummary } from './types'

function mkUpload(id: number, canDelete = true): ParseUploadSummary {
  return {
    id,
    uploaded_by: `Raider${id}`,
    uploader_discord_id: null,
    uploader_display_name: null,
    started_at: 1_700_000_000,
    duration_s: 60,
    total_damage: 1000,
    encdps: 100,
    success_level: 1,
    permissions: { can_delete: canDelete },
  }
}

function mkFight(id: number, uploadIds: number[], opts: { guild?: string; canDelete?: boolean; category?: 'raid' | 'dungeon' | 'other' } = {}): ParseEncounterSummary {
  const canDelete = opts.canDelete ?? true
  return {
    id,
    act_encid: `enc-${id}`,
    title: 'Tarinax',
    zone: 'Deathtoll',
    started_at: 1_700_000_000 + id,
    ended_at: 1_700_000_060 + id,
    duration_s: 60,
    total_damage: 1000,
    encdps: 100,
    kills: 1,
    deaths: 0,
    success_level: 1,
    combatant_count: 24,
    player_count: 24,
    category: opts.category ?? 'raid',
    uploaded_by: 'Raider1',
    uploader_discord_id: null,
    uploader_display_name: null,
    guild_name: opts.guild ?? 'Exordium',
    permissions: { can_delete: canDelete },
    uploads: uploadIds.map(u => mkUpload(u, canDelete)),
  }
}

/** Bucket the way ParsesPage.groupEncounters does — one zone-day per category. */
function mkBucket(fights: ParseEncounterSummary[], guild = 'Exordium'): GuildBucket {
  const byCat = { raid: [] as ParseEncounterSummary[], dungeon: [] as ParseEncounterSummary[], other: [] as ParseEncounterSummary[] }
  for (const f of fights) byCat[f.category].push(f)
  const zd = (list: ParseEncounterSummary[]) =>
    list.length ? [{ key: '2023-11-14 · Deathtoll', date: '2023-11-14', zone: 'Deathtoll', fights: list }] : []
  return {
    guild,
    fightsByCategory: { raid: zd(byCat.raid), dungeon: zd(byCat.dungeon), other: zd(byCat.other) },
    totalFights: fights.length,
  }
}

interface Recorded { url: string; init: RequestInit | undefined }
let calls: Recorded[]

function idsIn(url: string): number[] {
  return (new URL(url, 'http://test').searchParams.get('ids') ?? '').split(',').filter(Boolean).map(Number)
}

/** fetch stub: every call succeeds with deleted = ids sent, unless `respond` overrides. */
function mockFetch(respond?: (call: number, ids: number[]) => { ok: boolean; status: number; deleted?: number }) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      const ids = idsIn(url)
      const r = respond?.(calls.length, ids) ?? { ok: true, status: 200, deleted: ids.length }
      return { ok: r.ok, status: r.status, json: async () => ({ deleted: r.deleted ?? 0 }) }
    }) as unknown as typeof fetch,
  )
}

function renderSection(bucket: GuildBucket, onDeleted = vi.fn(), onResync = vi.fn()) {
  render(<GuildSection bucket={bucket} defaultExpanded={false} onDeleted={onDeleted} onResync={onResync} />)
  return { onDeleted, onResync }
}

const trash = () => screen.queryByRole('button', { name: /^Delete the \d+ parses? shown for/ })

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('alert', vi.fn())
})

describe('chunkFightsForDelete', () => {
  it('packs fights up to the chunk size without splitting a fight', () => {
    const singles = Array.from({ length: 190 }, (_, i) => mkFight(i + 1, [i + 1]))
    const big = mkFight(999, Array.from({ length: 24 }, (_, i) => 5000 + i))
    const chunks = chunkFightsForDelete([...singles, big])
    expect(chunks).toHaveLength(2)
    expect(chunks[0].uploadIds).toHaveLength(190)
    expect(chunks[1].fightIds).toEqual([999])
    expect(chunks[1].uploadIds).toHaveLength(24)
  })

  it('skips fights with no uploads and returns nothing for an empty list', () => {
    expect(chunkFightsForDelete([])).toEqual([])
    expect(chunkFightsForDelete([mkFight(1, [])])).toEqual([])
  })
})

describe('GuildSection header delete', () => {
  it('hides the button when any visible upload is not deletable, and for the no-guild bucket', () => {
    mockFetch()
    const { unmount } = render(
      <GuildSection bucket={mkBucket([mkFight(1, [1]), mkFight(2, [2, 3], { canDelete: false })])} defaultExpanded={false} onDeleted={vi.fn()} />,
    )
    expect(trash()).toBeNull()
    unmount()
    render(<GuildSection bucket={mkBucket([mkFight(1, [1], { guild: null as unknown as string })], NO_GUILD)} defaultExpanded={false} onDeleted={vi.fn()} />)
    expect(trash()).toBeNull()
  })

  it('confirms with the visible UPLOAD count and scope wording', async () => {
    mockFetch()
    const { onDeleted } = renderSection(mkBucket([mkFight(1, [1]), mkFight(2, [2, 3, 4]), mkFight(3, [5])]))
    fireEvent.click(trash()!)
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/^Delete the 5 parses shown for Exordium\? Only the parses currently listed under your filters are removed/),
    )
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1))
  })

  it('sends exactly the visible upload ids across categories via the batch endpoint', async () => {
    mockFetch()
    const fights = [mkFight(1, [11, 12]), mkFight(2, [21], { category: 'dungeon' }), mkFight(3, [31, 32, 33], { category: 'other' })]
    const { onDeleted, onResync } = renderSection(mkBucket(fights))
    fireEvent.click(trash()!)
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1))
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toMatch(/^\/api\/parses\/batch\?ids=|\/api\/parses\/batch\?ids=/)
    expect(calls[0].init?.method).toBe('DELETE')
    expect(calls[0].init?.credentials).toBe('include')
    expect(idsIn(calls[0].url).sort((a, b) => a - b)).toEqual([11, 12, 21, 31, 32, 33])
    // Predicate removes exactly the visible fights.
    const pred = onDeleted.mock.calls[0][0] as (f: ParseEncounterSummary) => boolean
    expect(fights.every(pred)).toBe(true)
    expect(pred(mkFight(99, [990], { guild: 'Remnant' }))).toBe(false)
    expect(onResync).not.toHaveBeenCalled()
    expect(window.alert).not.toHaveBeenCalled()
  })

  it('chunks at PARSE_BATCH_CHUNK_SIZE ids per request', async () => {
    mockFetch()
    const fights = Array.from({ length: PARSE_BATCH_CHUNK_SIZE + 1 }, (_, i) => mkFight(i + 1, [i + 1]))
    const { onDeleted } = renderSection(mkBucket(fights))
    fireEvent.click(trash()!)
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1))
    expect(calls).toHaveLength(2)
    expect(idsIn(calls[0].url)).toHaveLength(PARSE_BATCH_CHUNK_SIZE)
    expect(idsIn(calls[1].url)).toHaveLength(1)
  })

  it('on a failed later chunk, removes only the confirmed fights, alerts, and resyncs', async () => {
    mockFetch((call, ids) => (call === 2 ? { ok: false, status: 429 } : { ok: true, status: 200, deleted: ids.length }))
    const fights = Array.from({ length: PARSE_BATCH_CHUNK_SIZE + 1 }, (_, i) => mkFight(i + 1, [i + 1]))
    const { onDeleted, onResync } = renderSection(mkBucket(fights))
    fireEvent.click(trash()!)
    await waitFor(() => expect(onResync).toHaveBeenCalledTimes(1))
    const pred = onDeleted.mock.calls[0][0] as (f: ParseEncounterSummary) => boolean
    expect(pred(fights[0])).toBe(true)
    expect(pred(fights[PARSE_BATCH_CHUNK_SIZE])).toBe(false)
    expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(new RegExp(`^Deleted ${PARSE_BATCH_CHUNK_SIZE} of ${PARSE_BATCH_CHUNK_SIZE + 1} parses for Exordium`)))
  })

  it('treats a short deleted count as unconfirmed: removes nothing, resyncs', async () => {
    mockFetch(() => ({ ok: true, status: 200, deleted: 2 }))
    const fights = [mkFight(1, [1]), mkFight(2, [2]), mkFight(3, [3])]
    const { onDeleted, onResync } = renderSection(mkBucket(fights))
    fireEvent.click(trash()!)
    await waitFor(() => expect(onResync).toHaveBeenCalledTimes(1))
    const pred = onDeleted.mock.calls[0][0] as (f: ParseEncounterSummary) => boolean
    expect(fights.some(pred)).toBe(false)
    expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/^Deleted 2 of 3 parses for Exordium/))
  })

  it('does nothing when the confirm is cancelled', () => {
    mockFetch()
    vi.stubGlobal('confirm', vi.fn(() => false))
    const { onDeleted, onResync } = renderSection(mkBucket([mkFight(1, [1])]))
    fireEvent.click(trash()!)
    expect(calls).toHaveLength(0)
    expect(onDeleted).not.toHaveBeenCalled()
    expect(onResync).not.toHaveBeenCalled()
  })
})
