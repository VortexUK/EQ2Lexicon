import type { CSSProperties } from 'react'
import { useState, useEffect, useMemo, useCallback } from 'react'
import { useFetch } from '../hooks/useFetch'
import { useSearchParams, Link } from 'react-router-dom'

import Caret from '../components/Caret'
import { Card } from '../components/ui'
import { FilterPill } from '../components/FilterPill'
import { UploaderTag } from '../components/UploaderTag'
import { fmtDuration, fmtLocalDate, fmtLocalTime, fmtNum } from '../formatters'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ParsePermissions {
  can_delete: boolean
}

interface ParseUploadSummary {
  id: number
  uploaded_by: string                       // EQ2 character name (logger_name)
  uploader_discord_id: string | null        // resolved from source_dsn ('plugin:<id>')
  uploader_display_name: string | null      // joined from users.discord_name
  started_at: number
  duration_s: number
  total_damage: number
  encdps: number
  success_level: number
  permissions: ParsePermissions
}

interface ParseEncounterSummary {
  id: number
  act_encid: string
  title: string
  zone: string | null
  started_at: number       // unix seconds, UTC
  ended_at: number
  duration_s: number
  total_damage: number
  encdps: number
  kills: number
  deaths: number
  success_level: number      // ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
  combatant_count: number
  player_count: number
  uploaded_by: string                       // canonical upload's character name
  uploader_discord_id: string | null        // canonical upload's Discord ID
  uploader_display_name: string | null      // canonical upload's Discord display name
  guild_name: string | null   // stamped at ingest from uploader's Census guild
  permissions: ParsePermissions
  // Server-side mirror grouping (B2.15e) — every raider's upload for this
  // fight, including the canonical (single-upload fights have length 1).
  uploads: ParseUploadSummary[]
}

interface ParsesListResponse {
  results: ParseEncounterSummary[]
  total: number
}

type SizeFilter = '' | 'individual' | 'group' | 'raid12' | 'raid24'

// ── Constants ─────────────────────────────────────────────────────────────────

const SIZE_OPTIONS: { value: SizeFilter; label: string; range: string }[] = [
  { value: '',           label: 'All sizes',  range: '' },
  { value: 'raid24',     label: 'Raid (24)',  range: '13–24' },
  { value: 'raid12',     label: 'Raid (12)',  range: '7–12'  },
  { value: 'group',      label: 'Group',      range: '2–6'   },
  { value: 'individual', label: 'Individual', range: '1'     },
]

const NO_GUILD = 'No Guild'

// ── Helpers ───────────────────────────────────────────────────────────────────

function sizeLabel(playerCount: number): string {
  if (playerCount >= 13) return 'Raid (24)'
  if (playerCount >= 7)  return 'Raid (12)'
  if (playerCount >= 2)  return 'Group'
  return 'Individual'
}

// EQ2 mob naming convention: trash is "a krait warrior" / "an ancient guard"
// (article + lowercase noun), bosses have a proper capitalised name
// ("Captain Krasniv", "The Shadowed One"). First-character capitalisation is
// the simplest reliable signal.
function isBoss(title: string): boolean {
  if (!title) return false
  const first = title.charCodeAt(0)
  return first >= 65 && first <= 90  // 'A'..'Z'
}

// ── Grouped structure ─────────────────────────────────────────────────────────
// Guild → (LocalDate + Zone) → ParseEncounterSummary[]   (each = one fight)
//
// Mirror grouping (collapsing multiple raider uploads of the same fight)
// happens server-side now (B2.15e). Each ParseEncounterSummary IS a fight,
// with the canonical upload's fields at the top level and every raider's
// view in `.uploads`. The frontend just buckets fights by guild + zone-day.

interface ZoneBucket {
  key: string                          // "2026-05-24 · Great Divide"
  date: string                         // "2026-05-24"
  zone: string                         // "Great Divide"
  fights: ParseEncounterSummary[]
}

interface GuildBucket {
  guild: string                        // "Exordium" or "No Guild"
  zoneBuckets: ZoneBucket[]
}

// Visible joiner (used in display) + internal joiner (used in Map keys).
const KEY_SEP = String.fromCharCode(31)   // ASCII Unit Separator — never appears in zone names / dates
const DISPLAY_SEP = ' · '   // " · "

function groupEncounters(fights: ParseEncounterSummary[]): GuildBucket[] {
  const byGuild = new Map<string, Map<string, ParseEncounterSummary[]>>()

  for (const e of fights) {
    // Guild attribution stamped at ingest time from the uploader's Census
    // guild. NULL means the row pre-dates the column or the uploader's guild
    // couldn't be resolved.
    const guild = e.guild_name || NO_GUILD
    const date = fmtLocalDate(e.started_at)
    const zone = e.zone || '(unknown zone)'
    const zoneKey = [date, zone].join(KEY_SEP)

    let guildMap = byGuild.get(guild)
    if (!guildMap) {
      guildMap = new Map()
      byGuild.set(guild, guildMap)
    }
    let zoneFights = guildMap.get(zoneKey)
    if (!zoneFights) {
      zoneFights = []
      guildMap.set(zoneKey, zoneFights)
    }
    zoneFights.push(e)
  }

  // Build result with zones sorted by their newest fight (desc) within each guild.
  const result: GuildBucket[] = []
  for (const [guild, zoneMap] of byGuild) {
    const zoneBuckets: ZoneBucket[] = []
    for (const [key, fightsInZone] of zoneMap) {
      const [date, zone] = key.split(KEY_SEP)
      // Server returns fights newest-first overall; re-sort within the
      // bucket too so the most recent fight shows on top.
      fightsInZone.sort((a, b) => b.started_at - a.started_at)
      zoneBuckets.push({
        key: `${date}${DISPLAY_SEP}${zone}`,
        date, zone,
        fights: fightsInZone,
      })
    }
    zoneBuckets.sort((a, b) => {
      const ax = a.fights[0]?.started_at ?? 0
      const bx = b.fights[0]?.started_at ?? 0
      return bx - ax
    })
    result.push({ guild, zoneBuckets })
  }
  // Sort guilds: "No Guild" last, others alphabetical.
  result.sort((a, b) => {
    if (a.guild === NO_GUILD) return 1
    if (b.guild === NO_GUILD) return -1
    return a.guild.localeCompare(b.guild)
  })
  return result
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ParsesPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const [size, setSize] = useState<SizeFilter>(
    (searchParams.get('size') as SizeFilter) ?? '',
  )
  const [bossesOnly, setBossesOnly] = useState<boolean>(
    searchParams.get('bosses') === '1',
  )

  const parsesUrl = useMemo(() => {
    const url = new URL('/api/parses', window.location.origin)
    if (size) url.searchParams.set('size', size)
    url.searchParams.set('limit', '500')
    return url.toString()
  }, [size])

  const { data: fetchedData, loading, error } = useFetch<ParsesListResponse>(parsesUrl)

  // Local copy for optimistic deletions — seeded from fetchedData on each
  // successful fetch, then mutated locally so deletes don't trigger a full
  // reload (which would unmount GuildSection / ZoneSection, losing open state).
  const [localData, setLocalData] = useState<ParsesListResponse | null>(null)
  useEffect(() => {
    if (fetchedData !== null) setLocalData(fetchedData)
  }, [fetchedData])
  const data = localData ?? fetchedData

  // URL sync
  useEffect(() => {
    const p: Record<string, string> = {}
    if (size) p.size = size
    if (bossesOnly) p.bosses = '1'
    setSearchParams(p, { replace: true })
  }, [size, bossesOnly, setSearchParams])

  // Optimistic local removal after a successful delete — avoids a full
  // refetch (which would briefly toggle `loading` and unmount every
  // GuildSection / ZoneSection, losing their open/closed state).
  const removeEncounters = useCallback((pred: (e: ParseEncounterSummary) => boolean) => {
    setLocalData(prev => {
      if (!prev) return prev
      const kept = prev.results.filter(e => !pred(e))
      const removed = prev.results.length - kept.length
      return { results: kept, total: Math.max(0, prev.total - removed) }
    })
  }, [])

  const grouped = useMemo(() => {
    if (!data) return []
    const filtered = bossesOnly
      ? data.results.filter(e => isBoss(e.title))
      : data.results
    return groupEncounters(filtered)
  }, [data, bossesOnly])

  const setFilter = useCallback((v: SizeFilter) => setSize(v), [])

  return (
    <main className="max-w-[1100px] mx-auto px-4 py-6">
      <div className="flex items-baseline gap-4 mb-4">
        <h1 className="font-heading text-[1.7rem] text-gold m-0">
          Parses
        </h1>
        {data && (
          <span className="text-[0.82rem] text-text-muted">
            {data.total.toLocaleString()} encounter{data.total !== 1 ? 's' : ''}{size && ' (filtered)'}
          </span>
        )}
      </div>

      {/* Filter pills */}
      <div className="flex flex-wrap gap-[0.4rem] mb-[1.2rem]">
        {SIZE_OPTIONS.map(opt => (
          <FilterPill key={opt.value || 'all'} active={size === opt.value} onClick={() => setFilter(opt.value)}>
            {opt.label}
            {opt.range && <span className="ml-[0.35rem] opacity-60 text-[0.72rem]">{opt.range}</span>}
          </FilterPill>
        ))}
        <span className="w-px bg-border mx-[0.2rem]" />
        <FilterPill
          active={bossesOnly}
          onClick={() => setBossesOnly(v => !v)}
          title="Hide trash mobs (titles starting with 'a' / 'an')"
        >
          Bosses only
        </FilterPill>
      </div>

      {loading && <p className="text-text-muted">Loading…</p>}
      {error && <p className="text-danger">{error}</p>}

      {!loading && !error && data && data.results.length === 0 && (
        <p className="text-text-muted">
          No parses {size ? `match the ${size} filter` : 'yet'}.
        </p>
      )}

      {!loading && grouped.length > 0 && (
        <div className="flex flex-col gap-[0.4rem]">
          {grouped.map(g => (
            <GuildSection
              key={g.guild}
              bucket={g}
              defaultExpanded={grouped.length === 1}
              onDeleted={removeEncounters}
            />
          ))}
        </div>
      )}
    </main>
  )
}

// ── Delete helpers ────────────────────────────────────────────────────────────

async function deleteOne(id: number): Promise<number> {
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
async function deleteBatch(ids: number[]): Promise<number> {
  const url = new URL('/api/parses/batch', window.location.origin)
  url.searchParams.set('ids', ids.join(','))
  const r = await fetch(url.toString(), { method: 'DELETE', credentials: 'include' })
  if (!r.ok) throw new Error(`Delete failed: ${r.status}`)
  const j = await r.json()
  return j.deleted ?? 0
}

async function deleteByFilter(params: {
  guild: string
  zone?: string
  date?: string
  uploader?: string
}): Promise<number> {
  const url = new URL('/api/parses', window.location.origin)
  url.searchParams.set('guild', params.guild)
  if (params.zone) url.searchParams.set('zone', params.zone)
  if (params.date) url.searchParams.set('date', params.date)
  if (params.uploader) url.searchParams.set('uploader', params.uploader)
  const r = await fetch(url.toString(), { method: 'DELETE', credentials: 'include' })
  if (!r.ok) throw new Error(`Bulk delete failed: ${r.status}`)
  const j = await r.json()
  return j.deleted ?? 0
}

// ── Guild / Zone / Encounter rendering ────────────────────────────────────────

function GuildSection({
  bucket, defaultExpanded, onDeleted,
}: {
  bucket: GuildBucket
  defaultExpanded: boolean
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
}) {
  const [open, setOpen] = useState(defaultExpanded)
  const totalEncs = bucket.zoneBuckets.reduce(
    (s, z) => s + z.fights.reduce((n, f) => n + f.uploads.length, 0),
    0,
  )
  const totalFights = bucket.zoneBuckets.reduce((s, z) => s + z.fights.length, 0)
  // Officers / admins can wipe the whole guild only when they have delete
  // perms on every visible row for it (admins always do; officers only
  // within their own guild).
  const canDeleteGuild =
    bucket.guild !== NO_GUILD
    && bucket.zoneBuckets.every(z => z.fights.every(f => f.uploads.every(u => u.permissions.can_delete)))

  async function handleDeleteGuild(e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirm(`Delete all ${totalEncs} encounter${totalEncs === 1 ? '' : 's'} for ${bucket.guild}? This cannot be undone.`)) return
    try {
      await deleteByFilter({ guild: bucket.guild })
      onDeleted(enc => (enc.guild_name || NO_GUILD) === bucket.guild)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  return (
    <Card className="p-0">
      <div className="flex items-center">
        <button onClick={() => setOpen(v => !v)} className={headerBtnCls}>
          <Caret open={open} />
          <span className="font-heading text-[0.98rem] text-gold">
            {bucket.guild}
          </span>
          <span className="text-text-muted text-[0.78rem] ml-auto">
            {bucket.zoneBuckets.length} zone-{bucket.zoneBuckets.length === 1 ? 'day' : 'days'} · {totalFights} fight{totalFights !== 1 ? 's' : ''}{totalEncs !== totalFights ? ` (${totalEncs} uploads)` : ''}
          </span>
        </button>
        {canDeleteGuild && (
          <TrashButton onClick={handleDeleteGuild} title={`Delete all parses for ${bucket.guild}`} />
        )}
      </div>
      {open && (
        <div className="flex flex-col gap-[0.35rem] px-2 pb-[0.6rem]">
          {bucket.zoneBuckets.map(z => (
            <ZoneSection
              key={z.key}
              guild={bucket.guild}
              bucket={z}
              defaultExpanded={bucket.zoneBuckets.length === 1}
              onDeleted={onDeleted}
            />
          ))}
        </div>
      )}
    </Card>
  )
}

function ZoneSection({
  guild, bucket, defaultExpanded, onDeleted,
}: {
  guild: string
  bucket: ZoneBucket
  defaultExpanded: boolean
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
}) {
  const [open, setOpen] = useState(defaultExpanded)
  const totalUploads = bucket.fights.reduce((n, f) => n + f.uploads.length, 0)
  const allUploads = bucket.fights.flatMap(f => f.uploads)
  const canDeleteBucket =
    guild !== NO_GUILD
    && allUploads.every(u => u.permissions.can_delete)

  async function handleDeleteBucket(e: React.MouseEvent) {
    e.stopPropagation()
    const fightCount = bucket.fights.length
    if (!confirm(`Delete ${totalUploads} encounter${totalUploads === 1 ? '' : 's'} (${fightCount} fight${fightCount === 1 ? '' : 's'}, across all uploaders) for ${guild} · ${bucket.zone} · ${bucket.date}? This cannot be undone.`)) return
    try {
      // No uploader filter now — bucket spans every uploader in this zone-day.
      await deleteByFilter({ guild, zone: bucket.zone, date: bucket.date })
      const ids = new Set(allUploads.map(u => u.id))
      onDeleted(enc => ids.has(enc.id))
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  return (
    <div className="border border-border rounded-[6px]" style={{ background: 'rgba(0,0,0,0.15)' }}>
      <div className="flex items-center">
        <button onClick={() => setOpen(v => !v)} className={`${headerBtnCls} py-[0.4rem] px-[0.6rem]`}>
          <Caret open={open} />
          <span className="text-[0.88rem] text-text">
            <span className="text-text-muted mr-2">{bucket.date}</span>
            {bucket.zone}
          </span>
          <span className="text-text-muted text-[0.76rem] ml-auto">
            {bucket.fights.length} fight{bucket.fights.length !== 1 ? 's' : ''}{totalUploads !== bucket.fights.length ? ` (${totalUploads} uploads)` : ''}
          </span>
        </button>
        {canDeleteBucket && (
          <TrashButton onClick={handleDeleteBucket} title="Delete this zone-day bucket" />
        )}
      </div>
      {open && (
        <div className="px-[0.4rem] pb-[0.4rem]">
          <EncounterTable fights={bucket.fights} onDeleted={onDeleted} />
        </div>
      )}
    </div>
  )
}

function EncounterTable({
  fights, onDeleted,
}: {
  fights: ParseEncounterSummary[]
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
}) {
  return (
    <div
      className="grid items-center gap-x-2 gap-y-[2px] text-[0.82rem]"
      style={{ gridTemplateColumns: '1fr 70px 70px 110px 90px 90px 28px' }}
    >
      <div className={HDR_CELL_CLS}>Encounter</div>
      <div className={`${HDR_CELL_CLS} text-right`}>Time</div>
      <div className={`${HDR_CELL_CLS} text-right`}>Dur</div>
      <div className={`${HDR_CELL_CLS} text-right`}>Damage</div>
      <div className={`${HDR_CELL_CLS} text-right`}>DPS</div>
      <div className={`${HDR_CELL_CLS} text-right`}>Players</div>
      <div className={HDR_CELL_CLS} />
      {fights.map(f => (
        <MirrorRowGroup key={f.id} fight={f} onDeleted={onDeleted} />
      ))}
    </div>
  )
}

function MirrorRowGroup({
  fight, onDeleted,
}: {
  fight: ParseEncounterSummary
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
}) {
  const e = fight  // top-level fields are the canonical upload's
  const isMirror = fight.uploads.length > 1
  const [expanded, setExpanded] = useState(false)

  // "Delete the whole encounter" is only offered when the caller can delete
  // every upload in the group — i.e. an admin or an officer of the fight's
  // guild. A plain uploader can only delete their own among several, so this
  // is false for them (they still get their per-upload trash in the expansion).
  const canDeleteAll = isMirror && fight.uploads.length > 0 && fight.uploads.every(u => u.permissions.can_delete)

  // ACT outcome: 1 = win (green), 2 = loss (red), 3 = mixed (gold), 0 = unknown.
  const titleColor =
    e.success_level === 1 ? 'var(--success, #4caf50)'
    : e.success_level === 2 ? 'var(--danger, #e57373)'
    : e.success_level === 3 ? 'var(--warning, #d8a657)'
    : 'var(--text)'
  // Boss rows get a subtle yellow tint so they stand out at a glance.
  // Applied per-cell because the grid cells are direct siblings — no row
  // wrapper to style.
  const rowBg = isBoss(e.title) ? 'rgba(255, 204, 102, 0.07)' : undefined
  const cellBase: CSSProperties = { padding: '4px 0', background: rowBg }

  async function handleDeletePrimary(ev: React.MouseEvent) {
    ev.preventDefault()
    ev.stopPropagation()
    if (!confirm(`Delete encounter "${e.title}"? This cannot be undone.`)) return
    try {
      await deleteOne(e.id)
      onDeleted(other => other.id === e.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  async function handleDeleteFight(ev: React.MouseEvent) {
    ev.preventDefault()
    ev.stopPropagation()
    const n = fight.uploads.length
    if (!confirm(`Delete this entire encounter — all ${n} uploads of "${e.title}"? This cannot be undone.`)) return
    try {
      await deleteBatch(fight.uploads.map(u => u.id))
      onDeleted(other => other.id === e.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  async function handleDeleteUpload(upload: ParseUploadSummary, ev: React.MouseEvent) {
    ev.preventDefault()
    ev.stopPropagation()
    if (!confirm(`Delete ${upload.uploaded_by}'s upload of "${e.title}"? This cannot be undone.`)) return
    try {
      await deleteOne(upload.id)
      onDeleted(other => other.id === upload.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  // For a non-mirror row, the title links straight to the parse and the
  // trash deletes that single encounter — same UX as before. For a mirror
  // group, the title click toggles expansion (no direct /parse navigation
  // since there are multiple options); the top-level trash deletes the whole
  // encounter (all uploads) and is shown only to those allowed to remove
  // every upload — admins and officers of the fight's guild.
  return (
    <>
      {isMirror ? (
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-[0.4rem] border-none text-left cursor-pointer py-1 no-underline"
          style={{
            ...cellBase, color: titleColor,
            background: rowBg ?? 'none', font: 'inherit',
          }}
        >
          <Caret open={expanded} />
          {e.title}
          <span className="text-[0.7rem] text-text-muted font-normal">
            {fight.uploads.length} uploads
          </span>
        </button>
      ) : (
        <Link
          to={`/parse/${e.id}`}
          className="no-underline"
          style={{ ...cellBase, color: titleColor }}
        >
          {e.title}
        </Link>
      )}
      <div className="text-right text-text-muted" style={cellBase}>{fmtLocalTime(e.started_at)}</div>
      <div className="text-right text-text-muted" style={cellBase}>{fmtDuration(e.duration_s)}</div>
      <div className="text-right" style={cellBase}>{fmtNum(e.total_damage)}</div>
      <div className="text-right text-gold" style={cellBase}>{fmtNum(Math.round(e.encdps))}</div>
      <div className="text-right text-text-muted" style={cellBase}>
        {e.player_count} <span className="opacity-55 text-[0.7rem]">({sizeLabel(e.player_count)})</span>
      </div>
      <div className="text-center" style={cellBase}>
        {!isMirror && e.permissions.can_delete && (
          <TrashButton onClick={handleDeletePrimary} title="Delete this encounter" small />
        )}
        {/* Mirror group: officers/admins (who can delete every upload) get a
            single button that removes the whole encounter at once. */}
        {canDeleteAll && (
          <TrashButton
            onClick={handleDeleteFight}
            title={`Delete entire encounter (all ${fight.uploads.length} uploads)`}
            small
          />
        )}
      </div>

      {isMirror && expanded && (
        <div
          className="col-[1/-1] flex flex-col gap-[0.15rem] text-[0.78rem] pt-[0.25rem] pb-2 pl-6"
          style={{ background: rowBg ?? undefined }}
        >
          <div className="text-text-muted text-[0.7rem] mb-[0.15rem]">
            Pick a raider's view:
          </div>
          {fight.uploads.map(u => (
            <div
              key={u.id}
              className="grid items-center gap-x-2"
              style={{ gridTemplateColumns: '1fr 70px 110px 90px 28px' }}
            >
              <Link
                to={`/parse/${u.id}`}
                className="text-text no-underline"
              >
                <span className="text-gold">
                  <UploaderTag
                    characterName={u.uploaded_by}
                    discordId={u.uploader_discord_id}
                    displayName={u.uploader_display_name}
                  />
                </span>
                {u.id === fight.id && (
                  <span className="ml-[0.4rem] text-[0.65rem] text-text-muted">(primary)</span>
                )}
              </Link>
              <span className="text-right text-text-muted">{fmtDuration(u.duration_s)}</span>
              <span className="text-right">{fmtNum(u.total_damage)}</span>
              <span className="text-right text-gold">{fmtNum(Math.round(u.encdps))}</span>
              <span className="text-center">
                {u.permissions.can_delete && (
                  <TrashButton onClick={ev => handleDeleteUpload(u, ev)} title={`Delete ${u.uploaded_by}'s upload`} small />
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

function TrashButton({ onClick, title, small = false }: {
  onClick: (e: React.MouseEvent) => void
  title: string
  small?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="bg-transparent border-none text-text-muted cursor-pointer leading-none opacity-55 transition-[opacity,color] duration-100"
      style={{
        padding: small ? '0 4px' : '0 8px',
        fontSize: small ? '0.95rem' : '1.05rem',
      }}
      onMouseEnter={ev => {
        ev.currentTarget.style.opacity = '1'
        ev.currentTarget.style.color = 'var(--danger, #e57373)'
      }}
      onMouseLeave={ev => {
        ev.currentTarget.style.opacity = '0.55'
        ev.currentTarget.style.color = 'var(--text-muted)'
      }}
    >
      ✕
    </button>
  )
}

// ── Style helpers ─────────────────────────────────────────────────────────────

const headerBtnCls = 'flex items-center gap-2 w-full bg-transparent border-none text-inherit cursor-pointer py-2 px-3 text-left font-inherit'

const HDR_CELL_CLS = 'text-text-muted text-[0.7rem] uppercase tracking-[0.06em] py-1 border-b border-border mb-[0.2rem]'


