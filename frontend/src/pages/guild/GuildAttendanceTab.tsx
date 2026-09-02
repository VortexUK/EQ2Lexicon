// Guild raid-attendance tab: merged sessions uploaded by the EQ2Parser Raid
// tab. Session list (newest first, category count pills) → click-through
// detail with a by-player rollup (raid alts credit their owner) and a
// by-character toggle. Officers can delete junk/test sessions.
//
// Categories are derived server-side at read time: present (in raid),
// sat_out (rostered + online, not raiding), afk (declared on the
// availability calendar), awol (scheduled night + raider + absent +
// undeclared), absent (everyone else).

import { useState } from 'react'

import { Badge, Button } from '../../components/ui'
import { fmtDuration, fmtLocalDate, fmtLocalTime } from '../../formatters'
import { useFetch, useLazyFetch } from '../../hooks/useFetch'
import { toErrorMessage } from '../../lib/errors'

// ── Types (mirror backend/server/api/attendance.py responses) ──────────────

type Category = 'present' | 'sat_out' | 'afk' | 'awol' | 'absent'

interface SessionCounts {
  present: number
  sat_out: number
  afk: number
  awol: number
}

interface AttSession {
  id: number
  session_day: string
  seq: number
  started_at: number
  ended_at: number
  zones: string[]
  scheduled: boolean
  team_index: number | null
  counts: SessionCounts
}

interface AttListResponse {
  is_officer: boolean
  sessions: AttSession[]
}

interface CharRow {
  name: string
  role: 'raider' | 'raid_alt' | null
  category: Category
  first_seen: number | null
  last_seen: number | null
  owner_discord_id: string | null
}

interface UserRow {
  discord_id: string
  category: Category
  afk_declared: boolean
  characters: string[]
  display_name: string
  /** Raid-main character name (claimed raider, primary claim preferred). */
  main: string | null
}

interface AttDetailResponse {
  is_officer: boolean
  session: Omit<AttSession, 'counts'> & { uploaders: string[] | null }
  characters: CharRow[]
  users: UserRow[]
}

const CATEGORY_LABEL: Record<Category, string> = {
  present: 'Present',
  sat_out: 'Sat out',
  afk: 'AFK',
  awol: 'AWOL',
  absent: 'Absent',
}

const CATEGORY_VARIANT: Record<Category, 'success' | 'warning' | 'info' | 'danger' | 'muted'> = {
  present: 'success',
  sat_out: 'warning',
  afk: 'info',
  awol: 'danger',
  absent: 'muted',
}

function CategoryBadge({ category }: { category: Category }) {
  return <Badge variant={CATEGORY_VARIANT[category]}>{CATEGORY_LABEL[category]}</Badge>
}

// ── Component ──────────────────────────────────────────────────────────────

export function GuildAttendanceTab({ guildName }: { guildName: string }) {
  const list = useFetch<AttListResponse>(`/api/guild/${encodeURIComponent(guildName)}/attendance`)
  const detail = useLazyFetch<AttDetailResponse>()

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [view, setView] = useState<'players' | 'characters'>('players')
  const [deleteError, setDeleteError] = useState<string | null>(null)

  function openSession(id: number) {
    setSelectedId(id)
    setDeleteError(null)
    detail.run(`/api/guild/${encodeURIComponent(guildName)}/attendance/${id}`)
  }

  async function deleteSession(id: number) {
    if (!window.confirm('Delete this attendance session? This cannot be undone.')) return
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/attendance/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) {
        setDeleteError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`)
        return
      }
      setSelectedId(null)
      list.refetch()
    } catch (err) {
      setDeleteError(toErrorMessage(err))
    }
  }

  if (list.statusCode === 401 || list.statusCode === 403) {
    return (
      <p className="text-text-muted text-[0.9rem] p-4">
        Attendance is visible to guild members only — claim a character in {guildName} to see it.
      </p>
    )
  }
  if (list.error) return <p className="text-danger text-[0.9rem] p-4">{list.error}</p>
  if (list.loading || !list.data) return <p className="text-text-muted text-[0.9rem] p-4">Loading attendance…</p>

  const { is_officer: isOfficer, sessions } = list.data

  if (sessions.length === 0) {
    return (
      <div className="p-4 text-[0.9rem] text-text-muted flex flex-col gap-1.5">
        <p>No attendance recorded yet.</p>
        <p className="text-[0.8rem]">
          Sessions appear automatically when someone runs the EQ2Parser <em>Raid</em> tab during a raid night —
          multiple uploaders merge into one record per night.
        </p>
      </div>
    )
  }

  // ── detail view ──
  if (selectedId !== null) {
    const meta = sessions.find(s => s.id === selectedId)
    return (
      <div className="p-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setSelectedId(null)}>← All sessions</Button>
          {meta && (
            <span className="font-heading text-gold text-[1.05rem]">
              {fmtLocalDate(meta.started_at)}
              {meta.seq > 0 ? ` · raid ${meta.seq + 1}` : ''}
            </span>
          )}
          {meta && (
            <span className="text-[0.8rem] text-text-muted">
              {fmtLocalTime(meta.started_at)}–{fmtLocalTime(meta.ended_at)} · {fmtDuration(meta.ended_at - meta.started_at)}
            </span>
          )}
          {meta && !meta.scheduled && <Badge variant="muted">off-schedule — no AWOL tracking</Badge>}
          {isOfficer && (
            <Button variant="danger" size="sm" className="ml-auto" onClick={() => deleteSession(selectedId)}>
              Delete session
            </Button>
          )}
        </div>

        {meta && meta.zones.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {meta.zones.map(z => <Badge key={z} variant="gold">{z}</Badge>)}
          </div>
        )}

        {deleteError && <p className="text-danger text-[0.8rem]">{deleteError}</p>}
        {detail.error && <p className="text-danger text-[0.85rem]">{detail.error}</p>}
        {detail.loading && <p className="text-text-muted text-[0.85rem]">Loading session…</p>}

        {detail.data && (
          <>
            <div className="flex border-b border-border gap-1.5">
              {(['players', 'characters'] as const).map(v => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  className={`appearance-none bg-transparent border-0 border-b-2 px-2.5 py-1.5 text-[0.82rem] cursor-pointer ${
                    view === v ? 'border-gold text-gold' : 'border-transparent text-text-muted hover:text-text'
                  }`}
                >
                  {v === 'players' ? 'By player' : 'By character'}
                </button>
              ))}
            </div>

            {view === 'players' && (
              <PlayersTable users={detail.data.users} characters={detail.data.characters} />
            )}
            {view === 'characters' && <CharactersTable characters={detail.data.characters} />}
          </>
        )}
      </div>
    )
  }

  // ── list view ──
  return (
    <div className="p-4 flex flex-col gap-2">
      {sessions.map(s => (
        <button
          key={s.id}
          type="button"
          onClick={() => openSession(s.id)}
          className="appearance-none bg-transparent text-left w-full border border-border rounded-md px-3 py-2 cursor-pointer hover:border-gold/50 transition-colors flex flex-wrap items-center gap-2"
        >
          <span className="font-heading text-gold text-[0.95rem]">
            {fmtLocalDate(s.started_at)}
            {s.seq > 0 ? ` · raid ${s.seq + 1}` : ''}
          </span>
          <span className="text-[0.78rem] text-text-muted">
            {fmtLocalTime(s.started_at)}–{fmtLocalTime(s.ended_at)} · {fmtDuration(s.ended_at - s.started_at)}
          </span>
          {s.zones.slice(0, 3).map(z => <Badge key={z} variant="gold">{z}</Badge>)}
          {s.zones.length > 3 && <span className="text-[0.72rem] text-text-muted">+{s.zones.length - 3} more</span>}
          {!s.scheduled && <Badge variant="muted">off-schedule</Badge>}
          <span className="ml-auto flex items-center gap-1.5">
            <Badge variant="success">{s.counts.present} present</Badge>
            {s.counts.sat_out > 0 && <Badge variant="warning">{s.counts.sat_out} sat out</Badge>}
            {s.counts.afk > 0 && <Badge variant="info">{s.counts.afk} AFK</Badge>}
            {s.scheduled && s.counts.awol > 0 && <Badge variant="danger">{s.counts.awol} AWOL</Badge>}
          </span>
        </button>
      ))}
    </div>
  )
}

// ── Detail tables ──────────────────────────────────────────────────────────

function PlayersTable({ users, characters }: { users: UserRow[]; characters: CharRow[] }) {
  const charCategory = new Map(characters.map(c => [c.name, c] as const))
  const unclaimed = characters.filter(c => c.owner_discord_id === null && c.category !== 'absent')
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.85rem] border-collapse">
        <thead>
          <tr className="text-left text-text-muted text-[0.72rem] uppercase tracking-wide">
            <th className="py-1.5 pr-3 font-normal">Player</th>
            <th className="py-1.5 pr-3 font-normal">Status</th>
            <th className="py-1.5 font-normal">Characters</th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => (
            <tr key={u.discord_id} className="border-t border-border/50">
              <td className="py-1.5 pr-3">
                {u.display_name}
                {u.main && <span className="text-text-muted text-[0.75rem]"> · {u.main}</span>}
              </td>
              <td className="py-1.5 pr-3"><CategoryBadge category={u.category} /></td>
              <td className="py-1.5">
                <span className="flex flex-wrap gap-x-3 gap-y-0.5">
                  {u.characters.map(name => {
                    const c = charCategory.get(name)
                    const attended = c?.category === 'present'
                    return (
                      <span key={name} className={attended ? 'text-text' : 'text-text-muted'}>
                        {name}
                        {attended && c?.role === 'raid_alt' ? ' (alt)' : ''}
                        {c && c.category !== 'absent' && !attended ? ` — ${CATEGORY_LABEL[c.category].toLowerCase()}` : ''}
                      </span>
                    )
                  })}
                </span>
              </td>
            </tr>
          ))}
          {users.length === 0 && (
            <tr><td colSpan={3} className="py-2 text-text-muted">No claimed characters in this session.</td></tr>
          )}
        </tbody>
      </table>
      {unclaimed.length > 0 && (
        <p className="text-[0.75rem] text-text-muted mt-2">
          Unclaimed: {unclaimed.map(c => c.name).join(', ')} — claim these characters to roll them up to their player.
        </p>
      )}
    </div>
  )
}

function CharactersTable({ characters }: { characters: CharRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.85rem] border-collapse">
        <thead>
          <tr className="text-left text-text-muted text-[0.72rem] uppercase tracking-wide">
            <th className="py-1.5 pr-3 font-normal">Character</th>
            <th className="py-1.5 pr-3 font-normal">Role</th>
            <th className="py-1.5 pr-3 font-normal">Status</th>
            <th className="py-1.5 pr-3 font-normal">First seen</th>
            <th className="py-1.5 font-normal">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {characters.map(c => (
            <tr key={c.name} className="border-t border-border/50">
              <td className="py-1.5 pr-3">{c.name}</td>
              <td className="py-1.5 pr-3 text-text-muted">
                {c.role === 'raid_alt' ? 'raid alt' : c.role ?? '—'}
              </td>
              <td className="py-1.5 pr-3"><CategoryBadge category={c.category} /></td>
              <td className="py-1.5 pr-3 text-text-muted">{c.first_seen ? fmtLocalTime(c.first_seen) : '—'}</td>
              <td className="py-1.5 text-text-muted">{c.last_seen ? fmtLocalTime(c.last_seen) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
