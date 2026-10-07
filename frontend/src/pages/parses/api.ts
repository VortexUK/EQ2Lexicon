/**
 * Delete API for the parses page. No filter-based bulk delete: always delete
 * by an explicit id list, so a delete can never reach beyond what is on screen.
 */
import type { ParseEncounterSummary } from './types'

/**
 * Ids per batch request. Mirrors PARSE_BATCH_MAX_IDS on the server: 200 ids
 * is ~1.6 KB of query string, comfortably under URL limits, and a 500-fight
 * page with mirrors clears in a handful of requests.
 */
export const PARSE_BATCH_CHUNK_SIZE = 200

export async function deleteOne(id: number): Promise<number> {
  const r = await fetch(`/api/parses/${id}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!r.ok) throw new Error(`Delete failed: ${r.status}`)
  const j = await r.json()
  return j.deleted ?? 0
}

// Delete an explicit set of encounter ids in one request (every upload of a
// multi-uploader fight). Server authorises each id independently.
export async function deleteBatch(ids: number[]): Promise<number> {
  const url = new URL('/api/parses/batch', window.location.origin)
  url.searchParams.set('ids', ids.join(','))
  const r = await fetch(url.toString(), { method: 'DELETE', credentials: 'include' })
  if (!r.ok) throw new Error(`Delete failed: ${r.status}`)
  const j = await r.json()
  return j.deleted ?? 0
}

export interface DeleteChunk {
  fightIds: number[]
  uploadIds: number[]
}

/**
 * Pack fights into batch-sized chunks WITHOUT splitting a fight's uploads
 * across two requests, so a chunk's success maps cleanly onto whole fights
 * for the optimistic removal. Mirror groups are at most 24 uploads, far
 * below the chunk size, so every fight fits in some chunk.
 */
export function chunkFightsForDelete(
  fights: ParseEncounterSummary[],
  max: number = PARSE_BATCH_CHUNK_SIZE,
): DeleteChunk[] {
  const chunks: DeleteChunk[] = []
  let current: DeleteChunk = { fightIds: [], uploadIds: [] }
  for (const f of fights) {
    const ids = f.uploads.map(u => u.id)
    if (ids.length === 0) continue
    if (current.uploadIds.length > 0 && current.uploadIds.length + ids.length > max) {
      chunks.push(current)
      current = { fightIds: [], uploadIds: [] }
    }
    current.fightIds.push(f.id)
    current.uploadIds.push(...ids)
  }
  if (current.uploadIds.length > 0) chunks.push(current)
  return chunks
}

export interface ChunkedDeleteResult {
  /** Upload ids we asked the server to delete. */
  requested: number
  /** Upload rows the server reports as deleted. */
  deleted: number
  /** Fights every one of whose uploads the server confirmed deleted. */
  completedFightIds: Set<number>
  /** Message of the first failing request, if any. */
  error: string | null
  /** True when anything requested was NOT confirmed deleted — the caller
   *  must refetch rather than trust its local copy. */
  partial: boolean
}

/**
 * Delete every upload of the given fights, chunk by chunk, sequentially and
 * fail-fast (the same shape as the admin table's bulk purge). A chunk only
 * counts as complete when the server's `deleted` equals the ids sent — the
 * server silently skips ids the caller may not delete, and it does not say
 * which, so a short count marks the whole chunk as unconfirmed.
 */
export async function deleteFightsChunked(fights: ParseEncounterSummary[]): Promise<ChunkedDeleteResult> {
  const chunks = chunkFightsForDelete(fights)
  const result: ChunkedDeleteResult = {
    requested: chunks.reduce((n, c) => n + c.uploadIds.length, 0),
    deleted: 0,
    completedFightIds: new Set<number>(),
    error: null,
    partial: false,
  }
  for (const chunk of chunks) {
    let deleted: number
    try {
      deleted = await deleteBatch(chunk.uploadIds)
    } catch (err) {
      result.error = err instanceof Error ? err.message : 'Delete failed'
      result.partial = true
      return result
    }
    result.deleted += deleted
    if (deleted === chunk.uploadIds.length) {
      for (const id of chunk.fightIds) result.completedFightIds.add(id)
    } else {
      result.partial = true
    }
  }
  return result
}
