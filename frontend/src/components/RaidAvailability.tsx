// "My raid availability" — the personal 3-month calendar raid planners read.
//
// Renders NOTHING unless one of the viewer's claimed characters is on a
// raid roster (is_raider from /api/me/availability), so non-raiders never
// see it. Starts collapsed even for raiders. Clicking a day cycles
// Available → Tentative → AFK → Available; saves are debounced.

import { useEffect, useMemo, useRef, useState } from 'react'

import { toErrorMessage } from '../lib/errors'

type DayStatus = 'available' | 'tentative' | 'afk'

interface AvailabilityData {
  is_raider: boolean
  horizon_days: number
  days: Record<string, 'tentative' | 'afk'>
}

const CYCLE: Record<DayStatus, DayStatus> = { available: 'tentative', tentative: 'afk', afk: 'available' }

const STATUS_STYLE: Record<DayStatus, string> = {
  available: 'bg-success/15 text-success border-success/30',
  tentative: 'bg-warning/20 text-warning border-warning/40',
  afk: 'bg-danger/20 text-danger border-danger/40',
}

function iso(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** The months (as first-of-month Dates) covering today..today+horizon. */
function monthsInWindow(horizonDays: number): Date[] {
  const out: Date[] = []
  const today = new Date()
  const end = new Date(today)
  end.setDate(end.getDate() + horizonDays)
  const cur = new Date(today.getFullYear(), today.getMonth(), 1)
  while (cur <= end) {
    out.push(new Date(cur))
    cur.setMonth(cur.getMonth() + 1)
  }
  return out
}

export default function RaidAvailability() {
  const [data, setData] = useState<AvailabilityData | null>(null)
  const [open, setOpen] = useState(false)
  const [days, setDays] = useState<Record<string, 'tentative' | 'afk'>>({})
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/me/availability', { credentials: 'include' })
      .then(async res => {
        if (!res.ok) return
        const d = (await res.json()) as AvailabilityData
        setData(d)
        setDays(d.days)
      })
      .catch(() => {})
  }, [])

  // debounced save of pending day changes
  const pending = useRef<Record<string, DayStatus>>({})
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function cycleDay(day: string) {
    const current: DayStatus = days[day] ?? 'available'
    const next = CYCLE[current]
    setDays(prev => {
      const copy = { ...prev }
      if (next === 'available') delete copy[day]
      else copy[day] = next
      return copy
    })
    pending.current[day] = next
    if (timer.current) clearTimeout(timer.current)
    setSaveState('saving')
    timer.current = setTimeout(async () => {
      const batch = pending.current
      pending.current = {}
      try {
        const res = await fetch('/api/me/availability', {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ days: batch }),
        })
        if (!res.ok) {
          setSaveState('error')
          setError((await res.json().catch(() => ({}))).detail ?? 'Save failed')
          return
        }
        setSaveState('saved')
        setError(null)
      } catch (err) {
        setSaveState('error')
        setError(toErrorMessage(err))
      }
    }, 600)
  }

  const months = useMemo(() => (data ? monthsInWindow(data.horizon_days) : []), [data])
  const todayIso = iso(new Date())
  const endIso = useMemo(() => {
    if (!data) return todayIso
    const e = new Date()
    e.setDate(e.getDate() + data.horizon_days)
    return iso(e)
  }, [data, todayIso])

  if (!data?.is_raider) return null

  return (
    <section className="mt-6">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="appearance-none border-0 bg-transparent flex items-center gap-2 cursor-pointer p-0"
      >
        <span className="text-gold text-[0.8rem] select-none">{open ? '▾' : '▸'}</span>
        <h2 className="font-heading text-gold text-[1.1rem] m-0">My Raid Availability</h2>
        <span className="text-[0.72rem] text-text-muted">
          {Object.keys(days).length === 0
            ? 'available every day'
            : `${Object.values(days).filter(v => v === 'afk').length} AFK · ${Object.values(days).filter(v => v === 'tentative').length} tentative`}
        </span>
        {open && saveState === 'saving' && <span className="text-[0.7rem] text-text-muted">saving…</span>}
        {open && saveState === 'saved' && <span className="text-[0.7rem] text-success">saved</span>}
      </button>

      {open && (
        <div className="mt-2 border border-border rounded-md bg-surface/60 p-3">
          <p className="text-[0.78rem] text-text-muted mb-2">
            Click a day to cycle{' '}
            <span className="text-success">Available</span> →{' '}
            <span className="text-warning">Tentative</span> →{' '}
            <span className="text-danger">AFK</span>. Days you don't touch count as available. Raid leaders see
            this when planning groups.
          </p>
          {error && <p className="text-danger text-[0.78rem] mb-2">{error}</p>}
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
            {months.map(m => {
              const year = m.getFullYear()
              const month = m.getMonth()
              const firstDow = (new Date(year, month, 1).getDay() + 6) % 7 // Mon=0
              const daysInMonth = new Date(year, month + 1, 0).getDate()
              return (
                <div key={`${year}-${month}`}>
                  <p className="text-[0.78rem] text-text font-medium mb-1">
                    {m.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
                  </p>
                  <div className="grid grid-cols-7 gap-0.5">
                    {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
                      <span key={i} className="text-[0.6rem] text-text-muted text-center select-none">{d}</span>
                    ))}
                    {Array.from({ length: firstDow }, (_, i) => <span key={`pad${i}`} />)}
                    {Array.from({ length: daysInMonth }, (_, i) => {
                      const day = iso(new Date(Date.UTC(year, month, i + 1)))
                      const inWindow = day >= todayIso && day <= endIso
                      const status: DayStatus = days[day] ?? 'available'
                      return (
                        <button
                          key={day}
                          type="button"
                          disabled={!inWindow}
                          onClick={() => cycleDay(day)}
                          title={inWindow ? `${day}: ${status}` : undefined}
                          className={`appearance-none text-[0.68rem] leading-none py-1 rounded-sm border cursor-pointer disabled:cursor-default transition-colors ${
                            inWindow ? STATUS_STYLE[status] : 'bg-transparent text-text-muted/30 border-transparent'
                          } ${day === todayIso ? 'ring-1 ring-gold/70' : ''}`}
                        >
                          {i + 1}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
