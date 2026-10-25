/**
 * Column filters for the admin parses table: one value per column, ANDed by
 * GET /api/admin/parses (backend `ParsesStore.list_encounters_for_admin`).
 * Values are kept as the strings the inputs hold; `filtersToParams` turns
 * them into the query string and drops anything empty or malformed, so a
 * half-typed id or player count never reaches the server.
 */

export type ResultFilter = '' | '0' | '1' | '2' | '3'
export type StatusFilter = 'all' | 'visible' | 'hidden'

export interface AdminParseFilters {
  id:         string
  title:      string
  zone:       string
  guild:      string
  uploader:   string
  /** Local calendar days, `YYYY-MM-DD` as a date input holds them; both inclusive. */
  from:       string
  to:         string
  minPlayers: string
  result:     ResultFilter
  status:     StatusFilter
}

export const EMPTY_FILTERS: AdminParseFilters = {
  id: '', title: '', zone: '', guild: '', uploader: '',
  from: '', to: '', minPlayers: '', result: '', status: 'all',
}

/** Unix seconds of local midnight on `YYYY-MM-DD`, or null when it isn't one.
 *  `dayOffset` 1 gives the following midnight, the exclusive end of that day. */
export function localDayStart(ymd: string, dayOffset = 0): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd)
  if (!m) return null
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + dayOffset)
  return Number.isNaN(d.getTime()) ? null : Math.floor(d.getTime() / 1000)
}

export function hasActiveFilters(f: AdminParseFilters): boolean {
  return (Object.keys(EMPTY_FILTERS) as (keyof AdminParseFilters)[]).some(k => f[k].trim() !== EMPTY_FILTERS[k])
}

/** Query string for GET /api/admin/parses: the filters plus paging. */
export function filtersToParams(
  f: AdminParseFilters,
  page: { limit?: number; before?: number; beforeId?: number } = {},
): string {
  const p = new URLSearchParams()
  const id = f.id.trim().replace(/^#/, '')
  if (/^\d+$/.test(id)) p.set('id', id)
  for (const key of ['title', 'zone', 'guild', 'uploader'] as const) {
    const v = f[key].trim()
    if (v) p.set(key, v)
  }
  const since = localDayStart(f.from)
  if (since !== null) p.set('since', String(since))
  const until = localDayStart(f.to, 1)
  if (until !== null) p.set('until', String(until))
  const minPlayers = f.minPlayers.trim()
  if (/^\d+$/.test(minPlayers) && Number(minPlayers) > 0) p.set('min_players', minPlayers)
  if (f.result) p.set('result', f.result)
  if (f.status !== 'all') p.set('status', f.status)
  if (page.limit !== undefined) p.set('limit', String(page.limit))
  if (page.before !== undefined) p.set('before', String(page.before))
  if (page.beforeId !== undefined) p.set('before_id', String(page.beforeId))
  return p.toString()
}
