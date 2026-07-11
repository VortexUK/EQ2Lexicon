import { useEffect, useState } from 'react'

import { Button, Card, SectionLabel, Badge } from '../../components/ui'
import { RaidPlanner } from './raidplanner/RaidPlanner'
import { toErrorMessage } from '../../lib/errors'
import {
  WEEKDAYS,
  getBrowserTimeZone,
  listTimeZones,
  minutesToHHMM,
  toViewerMinutes,
} from '../../lib/timezone'

// ── Types ──────────────────────────────────────────────────────────────────

interface RaidSlot { days: number[]; start_min: number; end_min: number; label: string | null }
interface RaidTeam { name: string; primary_tz: string; twitch_url: string | null; raids: RaidSlot[] }

// Editor-shaped (times as "HH:MM").
interface EditSlot { days: number[]; start: string; end: string; label: string }
interface EditTeam { name: string; primary_tz: string; twitch_url: string; raids: EditSlot[] }

const MAX_TEAMS = 4
const MAX_RAIDS = 4
const VIEWER_TZ = getBrowserTimeZone()

function toEdit(t: RaidTeam): EditTeam {
  return {
    name: t.name,
    primary_tz: t.primary_tz,
    twitch_url: t.twitch_url ?? '',
    raids: t.raids.map(r => ({
      days: [...r.days],
      start: minutesToHHMM(r.start_min),
      end: minutesToHHMM(r.end_min),
      label: r.label ?? '',
    })),
  }
}

/** Team-tz "HH:MM" plus the viewer's equivalent (with a +1/-1 day note). */
function viewerRange(startMin: number, endMin: number, teamTz: string): string {
  if (teamTz === VIEWER_TZ) return 'your timezone'
  const s = toViewerMinutes(startMin, teamTz, VIEWER_TZ)
  const e = toViewerMinutes(endMin, teamTz, VIEWER_TZ)
  const shift = (n: number) => (n < 0 ? ' (prev day)' : n > 0 ? ' (next day)' : '')
  return `${minutesToHHMM(s.minutes)}${shift(s.dayShift)}–${minutesToHHMM(e.minutes)}${shift(e.dayShift)} your time`
}

// ── Component ──────────────────────────────────────────────────────────────

export function GuildRaidScheduleTab({ guildName, isOfficer }: { guildName: string; isOfficer: boolean }) {
  const [teams, setTeams] = useState<RaidTeam[] | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<EditTeam[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/raid-schedule`, { credentials: 'include' })
      .then(async res => {
        if (!res.ok) { setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        const loaded = (await res.json()).teams as RaidTeam[]
        setTeams(loaded)
        // A single team starts expanded; multiple teams start collapsed so
        // the tab stays scannable.
        setExpanded(loaded.length === 1 ? new Set([0]) : new Set())
      })
      .catch(err => setError(toErrorMessage(err)))
      .finally(() => setLoading(false))
  }, [guildName])

  const toggleExpand = (i: number) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })

  function startEdit() {
    setDraft((teams ?? []).map(toEdit))
    setSaveError(null)
    setEditing(true)
  }

  async function save() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        teams: draft.map(t => ({
          name: t.name,
          primary_tz: t.primary_tz,
          twitch_url: t.twitch_url.trim() || null,
          raids: t.raids.map(r => ({ days: r.days, start: r.start, end: r.end, label: r.label.trim() || null })),
        })),
      }
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/raid-schedule`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) { setSaveError((await res.json().catch(() => ({}))).detail ?? 'Failed to save'); return }
      setTeams((await res.json()).teams as RaidTeam[])
      setEditing(false)
    } catch (err) {
      setSaveError(toErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  // draft mutators
  const patchTeam = (i: number, p: Partial<EditTeam>) => setDraft(d => d.map((t, k) => (k === i ? { ...t, ...p } : t)))
  const patchRaid = (ti: number, ri: number, p: Partial<EditSlot>) =>
    setDraft(d => d.map((t, k) => (k === ti ? { ...t, raids: t.raids.map((r, j) => (j === ri ? { ...r, ...p } : r)) } : t)))
  const toggleDay = (ti: number, ri: number, day: number) =>
    setDraft(d => d.map((t, k) => k !== ti ? t : {
      ...t,
      raids: t.raids.map((r, j) => j !== ri ? r : {
        ...r, days: r.days.includes(day) ? r.days.filter(x => x !== day) : [...r.days, day].sort((a, b) => a - b),
      }),
    }))

  if (loading) return <p className="text-text-muted p-4">Loading raid schedule…</p>
  if (error) return <p className="text-danger p-4">{error}</p>

  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between mb-3">
        <SectionLabel variant="gold" className="mb-0">Raid Schedule</SectionLabel>
        {isOfficer && !editing && (
          <Button variant="secondary" size="sm" onClick={startEdit}>Edit</Button>
        )}
      </div>

      {!editing && (
        (teams && teams.length > 0) ? (
          <div className="flex flex-col gap-3">
            {teams.map((t, i) => (
              <Card key={i} className="p-3">
                <button
                  type="button"
                  onClick={() => toggleExpand(i)}
                  className="appearance-none border-0 bg-transparent w-full flex items-baseline gap-2 flex-wrap cursor-pointer text-left"
                >
                  <span className="text-gold text-[0.8rem] leading-none self-center select-none">
                    {expanded.has(i) ? '▾' : '▸'}
                  </span>
                  <span className="font-heading text-gold text-[1.05rem]">{t.name}</span>
                  <span className="text-[0.72rem] text-text-muted">{t.primary_tz}</span>
                  {t.twitch_url && (
                    <a href={t.twitch_url} target="_blank" rel="noopener noreferrer"
                      onClick={e => e.stopPropagation()}
                      className="text-[0.75rem] text-gold underline decoration-dotted ml-auto">Twitch ↗</a>
                  )}
                </button>
                {expanded.has(i) && (
                  <div className="mt-1.5">
                    {t.raids.length === 0 ? (
                      <p className="text-text-muted text-[0.82rem]">No raids set.</p>
                    ) : (
                      <div className="flex flex-col gap-1.5">
                        {t.raids.map((r, j) => (
                          <div key={j} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.85rem]">
                            <span className="flex gap-0.5">
                              {WEEKDAYS.map(d => (
                                <span key={d.n}
                                  className={`text-[0.62rem] px-1 py-0.5 rounded-sm ${r.days.includes(d.n) ? 'bg-gold/20 text-gold' : 'text-text-muted/40'}`}>
                                  {d.label}
                                </span>
                              ))}
                            </span>
                            <span className="text-text font-medium tabular-nums">
                              {minutesToHHMM(r.start_min)}–{minutesToHHMM(r.end_min)}
                            </span>
                            <span className="text-text-muted text-[0.78rem]">({viewerRange(r.start_min, r.end_min, t.primary_tz)})</span>
                            {r.label && <Badge variant="muted">{r.label}</Badge>}
                          </div>
                        ))}
                      </div>
                    )}
                    <RaidPlanner
                      guildName={guildName}
                      teamIndex={i}
                      raidDays={t.raids.map(r => r.days)}
                    />
                  </div>
                )}
              </Card>
            ))}
          </div>
        ) : (
          <p className="text-text-muted py-4">No raid schedule has been set for this guild yet.</p>
        )
      )}

      {editing && (
        <div className="flex flex-col gap-4">
          {draft.map((t, ti) => (
            <Card key={ti} className="p-3 flex flex-col gap-2">
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-0.5">
                  <span className="text-[0.66rem] text-text-muted uppercase tracking-[0.05em]">Team name</span>
                  <input value={t.name} onChange={e => patchTeam(ti, { name: e.target.value })}
                    className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem] w-[150px]" />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[0.66rem] text-text-muted uppercase tracking-[0.05em]">Primary timezone</span>
                  <select value={t.primary_tz} onChange={e => patchTeam(ti, { primary_tz: e.target.value })}
                    className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem] max-w-[220px]">
                    {listTimeZones().map(tz => <option key={tz} value={tz}>{tz}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-0.5 flex-1 min-w-[180px]">
                  <span className="text-[0.66rem] text-text-muted uppercase tracking-[0.05em]">Twitch URL (optional)</span>
                  <input value={t.twitch_url} onChange={e => patchTeam(ti, { twitch_url: e.target.value })}
                    placeholder="https://twitch.tv/yourchannel"
                    className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem]" />
                </label>
                <Button variant="danger" size="sm" onClick={() => setDraft(d => d.filter((_, k) => k !== ti))}>Remove team</Button>
              </div>

              <div className="flex flex-col gap-2 pl-1">
                {t.raids.map((r, ri) => (
                  <div key={ri} className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-2">
                    <span className="flex gap-0.5">
                      {WEEKDAYS.map(d => (
                        <button key={d.n} type="button" onClick={() => toggleDay(ti, ri, d.n)}
                          className={`text-[0.66rem] px-1.5 py-1 rounded-sm border ${r.days.includes(d.n) ? 'bg-gold/20 text-gold border-gold/50' : 'bg-surface text-text-muted border-border'}`}>
                          {d.label}
                        </button>
                      ))}
                    </span>
                    <input type="time" value={r.start} onChange={e => patchRaid(ti, ri, { start: e.target.value })}
                      className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem]" />
                    <span className="text-text-muted">–</span>
                    <input type="time" value={r.end} onChange={e => patchRaid(ti, ri, { end: e.target.value })}
                      className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem]" />
                    <input value={r.label} onChange={e => patchRaid(ti, ri, { label: e.target.value })} placeholder="label (optional)"
                      className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem] w-[130px]" />
                    <Button variant="ghost" size="sm" onClick={() => patchTeam(ti, { raids: t.raids.filter((_, j) => j !== ri) })}>✕</Button>
                  </div>
                ))}
                {t.raids.length < MAX_RAIDS && (
                  <button type="button" onClick={() => patchTeam(ti, { raids: [...t.raids, { days: [], start: '20:00', end: '23:00', label: '' }] })}
                    className="self-start text-[0.8rem] text-gold hover:text-gold-bright">+ Add raid</button>
                )}
              </div>
            </Card>
          ))}

          {draft.length < MAX_TEAMS && (
            <Button variant="secondary" size="sm"
              onClick={() => setDraft(d => [...d, { name: `Team ${d.length + 1}`, primary_tz: VIEWER_TZ, twitch_url: '', raids: [] }])}>
              + Add team
            </Button>
          )}

          {saveError && <p className="text-danger text-[0.85rem]">{saveError}</p>}
          <div className="flex gap-2">
            <Button variant="primary" size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save schedule'}</Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  )
}
