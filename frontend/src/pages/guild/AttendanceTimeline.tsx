// Attendance timeline — a character's session as timed periods
// (present / sat out / AFK) instead of one flat category. Read-only chips
// for everyone; officers get an inline editor whose save replaces the
// character's derived timeline server-side (attendance_segments).

import { useState } from 'react'

import { Button } from '../../components/ui'
import { fmtLocalTime } from '../../formatters'

export type SegCategory = 'present' | 'sat_out' | 'afk'

export interface Segment {
  category: SegCategory
  started_at: number
  ended_at: number
}

export const SEG_LABEL: Record<SegCategory, string> = {
  present: 'present',
  sat_out: 'sat out',
  afk: 'AFK',
}

const SEG_CLASS: Record<SegCategory, string> = {
  present: 'text-success',
  sat_out: 'text-warning',
  afk: 'text-text-muted',
}

// ── Time helpers ───────────────────────────────────────────────────────────
// Officers type clock times (HH:MM), not dates — but raids cross midnight,
// so a bare time is resolved against the session evening: of the three
// candidate days around the session start, take the timestamp closest to
// the session midpoint.

export function tsToTimeInput(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function timeInputToTs(hhmm: string, sessionStart: number, sessionEnd: number): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim())
  if (!m) return null
  const mid = (sessionStart + sessionEnd) / 2
  const base = new Date(sessionStart * 1000)
  let best: number | null = null
  for (const dayOffset of [-1, 0, 1]) {
    const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + dayOffset, Number(m[1]), Number(m[2]))
    const ts = Math.floor(d.getTime() / 1000)
    if (best === null || Math.abs(ts - mid) < Math.abs(best - mid)) best = ts
  }
  return best
}

// ── Read-only chips ────────────────────────────────────────────────────────

export function TimelineChips({
  segments,
  firstSeen,
  lastSeen,
}: {
  segments: Segment[]
  firstSeen: number | null
  lastSeen: number | null
}) {
  if (segments.length === 0) {
    // No timed states (AFK/AWOL/absent rows, or unrostered online guildies
    // whose seen window is still worth showing).
    if (firstSeen !== null && lastSeen !== null) {
      return <span className="text-text-muted">{fmtLocalTime(firstSeen)}–{fmtLocalTime(lastSeen)}</span>
    }
    return <span className="text-text-muted">—</span>
  }
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
      {segments.map((s, i) => (
        <span key={i} className="whitespace-nowrap">
          <span className="text-text-muted">
            {fmtLocalTime(s.started_at)}–{fmtLocalTime(s.ended_at)}{' '}
          </span>
          <span className={SEG_CLASS[s.category]}>{SEG_LABEL[s.category]}</span>
        </span>
      ))}
    </span>
  )
}

// ── Session window fix (officer) ───────────────────────────────────────────
// A runaway merge chain once stretched a session to 955 minutes — this sets
// the session's own start/end; derived timelines clip to it server-side.

export function SessionWindowEditor({
  sessionStart,
  sessionEnd,
  onSave,
  onCancel,
}: {
  sessionStart: number
  sessionEnd: number
  onSave: (startedAt: number, endedAt: number) => void
  onCancel: () => void
}) {
  const [start, setStart] = useState(tsToTimeInput(sessionStart))
  const [end, setEnd] = useState(tsToTimeInput(sessionEnd))
  const [error, setError] = useState<string | null>(null)

  function submit() {
    const s = timeInputToTs(start, sessionStart, sessionEnd)
    const e = timeInputToTs(end, sessionStart, sessionEnd)
    if (s === null || e === null || s >= e) {
      setError('The session must start before it ends.')
      return
    }
    onSave(s, e)
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[0.8rem]">
      <span className="text-text-muted">Session ran</span>
      <input
        type="time"
        value={start}
        onChange={e => setStart(e.target.value)}
        aria-label="Session start time"
        className="bg-surface border border-border rounded-sm px-1.5 py-0.5 text-[0.78rem]"
      />
      <span className="text-text-muted">–</span>
      <input
        type="time"
        value={end}
        onChange={e => setEnd(e.target.value)}
        aria-label="Session end time"
        className="bg-surface border border-border rounded-sm px-1.5 py-0.5 text-[0.78rem]"
      />
      <Button variant="secondary" size="sm" onClick={submit}>Save times</Button>
      <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      {error && <span className="text-danger text-[0.78rem]">{error}</span>}
      <span className="text-[0.72rem] text-text-muted w-full">
        Clock times on the raid evening. Fixing the window also clips every derived timeline to it — hand-edited
        timelines are untouched.
      </span>
    </div>
  )
}

// ── Officer editor ─────────────────────────────────────────────────────────

interface DraftSeg {
  category: SegCategory
  start: string // HH:MM
  end: string
}

export function TimelineEditor({
  initial,
  sessionStart,
  sessionEnd,
  canRevert,
  onSave,
  onCancel,
}: {
  /** Seed periods — the row's current (derived or manual) timeline. */
  initial: Segment[]
  sessionStart: number
  sessionEnd: number
  /** True when a manual timeline exists (shows "Revert to parser"). */
  canRevert: boolean
  /** Empty array = revert to the parser-derived timeline. */
  onSave: (segments: Segment[]) => void
  onCancel: () => void
}) {
  const seed: DraftSeg[] =
    initial.length > 0
      ? initial.map(s => ({ category: s.category, start: tsToTimeInput(s.started_at), end: tsToTimeInput(s.ended_at) }))
      : [{ category: 'present', start: tsToTimeInput(sessionStart), end: tsToTimeInput(sessionEnd) }]
  const [rows, setRows] = useState<DraftSeg[]>(seed)
  const [error, setError] = useState<string | null>(null)

  function update(i: number, patch: Partial<DraftSeg>) {
    setRows(prev => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  function submit() {
    const segs: Segment[] = []
    for (const r of rows) {
      const started_at = timeInputToTs(r.start, sessionStart, sessionEnd)
      const ended_at = timeInputToTs(r.end, sessionStart, sessionEnd)
      if (started_at === null || ended_at === null) {
        setError('Every period needs a start and end time.')
        return
      }
      if (started_at >= ended_at) {
        setError('A period must start before it ends.')
        return
      }
      segs.push({ category: r.category, started_at, ended_at })
    }
    segs.sort((a, b) => a.started_at - b.started_at)
    for (let i = 1; i < segs.length; i++) {
      if (segs[i].started_at < segs[i - 1].ended_at) {
        setError('Periods overlap — they must be sequential.')
        return
      }
    }
    if (segs.length === 0) {
      setError('Add at least one period, or use Revert to parser.')
      return
    }
    onSave(segs)
  }

  return (
    <div className="flex flex-col gap-1.5 py-1">
      {rows.map((r, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5">
          <select
            value={r.category}
            onChange={e => update(i, { category: e.target.value as SegCategory })}
            aria-label={`Period ${i + 1} state`}
            className="bg-surface border border-border rounded-sm px-1.5 py-0.5 text-[0.78rem]"
          >
            {(Object.keys(SEG_LABEL) as SegCategory[]).map(c => (
              <option key={c} value={c}>{SEG_LABEL[c]}</option>
            ))}
          </select>
          <input
            type="time"
            value={r.start}
            onChange={e => update(i, { start: e.target.value })}
            aria-label={`Period ${i + 1} start`}
            className="bg-surface border border-border rounded-sm px-1.5 py-0.5 text-[0.78rem]"
          />
          <span className="text-text-muted">–</span>
          <input
            type="time"
            value={r.end}
            onChange={e => update(i, { end: e.target.value })}
            aria-label={`Period ${i + 1} end`}
            className="bg-surface border border-border rounded-sm px-1.5 py-0.5 text-[0.78rem]"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setRows(prev => prev.filter((_, j) => j !== i))}
            title="Remove this period"
          >
            ✕
          </Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const last = rows[rows.length - 1]
            setRows(prev => [
              ...prev,
              { category: 'sat_out', start: last?.end ?? tsToTimeInput(sessionStart), end: tsToTimeInput(sessionEnd) },
            ])
          }}
        >
          + period
        </Button>
        <Button variant="secondary" size="sm" onClick={submit}>Save timeline</Button>
        {canRevert && (
          <Button variant="ghost" size="sm" onClick={() => onSave([])} title="Discard the hand-edited timeline and go back to what the parser recorded">
            Revert to parser
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        {error && <span className="text-danger text-[0.78rem]">{error}</span>}
      </div>
      <p className="text-[0.72rem] text-text-muted">
        Times are clock times on the raid evening — periods must not overlap. Saving replaces the parser's timeline
        for this character.
      </p>
    </div>
  )
}
