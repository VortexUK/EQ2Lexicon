// The per-team raid planner: 4 groups × 6 slots + sitout + bench, with
// archetype-coloured character chips, drag-and-drop (click-to-place
// fallback), per-group composition warnings and an availability overlay
// for the selected date.
//
// Officers' changes save automatically (debounced). Non-officer guild
// members get a local sandbox: they can drag everything around, nothing
// persists, and a reset chip restores the live plan.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Badge, Button, SectionLabel } from '../../../components/ui'
import { toErrorMessage } from '../../../lib/errors'
import { useClasses } from '../../../useClasses'
import {
  buildGrid,
  computeWarnings,
  moveCharacter,
  nextRaidDate,
  placementsEqual,
  swapGroups,
} from './logic'
import { AVAILABILITY_LABEL, GROUPS, SLOTS_PER_GROUP } from './types'
import type { DropTarget, Placement, PlannerData, RosterEntry } from './types'

// ── Character chip ───────────────────────────────────────────────────────────

function CharChip({
  entry,
  availability,
  player,
  colour,
  selected,
  interactive,
  onPick,
  onDragStart,
}: {
  entry: RosterEntry
  availability: 'tentative' | 'afk' | undefined
  player: string | undefined
  colour: string
  selected: boolean
  interactive: boolean
  onPick?: () => void
  onDragStart?: (e: React.DragEvent) => void
}) {
  const afk = availability === 'afk'
  return (
    <button
      type="button"
      draggable={interactive}
      onDragStart={onDragStart}
      onClick={interactive ? onPick : undefined}
      title={player ? `Played by ${player}` : undefined}
      className={`appearance-none border bg-transparent w-full flex items-center gap-1.5 px-1.5 py-1 rounded-sm text-left transition-colors ${
        selected ? 'border-gold ring-1 ring-gold/60' : 'border-transparent'
      } ${interactive ? 'cursor-grab hover:border-gold/40' : 'cursor-default'} ${afk ? 'opacity-45' : ''}`}
    >
      <span className="w-1 self-stretch rounded-full shrink-0" style={{ background: colour }} />
      <span className="min-w-0 flex-1">
        <span className="block text-[0.84rem] leading-tight truncate" style={{ color: colour }}>
          {entry.name}
        </span>
        <span className="block text-[0.66rem] text-text-muted leading-tight truncate">
          {entry.cls ?? '—'}
          {entry.level ? ` · ${entry.level}` : ''}
          {entry.role === 'raid_alt' ? ' · alt' : ''}
        </span>
      </span>
      {availability && (
        <span
          className={`text-[0.58rem] px-1 py-px rounded-sm shrink-0 uppercase tracking-wide ${
            afk ? 'bg-danger/20 text-danger' : 'bg-warning/20 text-warning'
          }`}
        >
          {AVAILABILITY_LABEL[availability]}
        </span>
      )}
    </button>
  )
}

// ── Planner ──────────────────────────────────────────────────────────────────

export function RaidPlanner({ guildName, teamIndex, raidDays }: {
  guildName: string
  teamIndex: number
  raidDays: number[][]
}) {
  const { byName, colourFor } = useClasses()

  const [data, setData] = useState<PlannerData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [date, setDate] = useState(() => nextRaidDate(raidDays))

  // Working copy of placements (officer: autosaves; member: sandbox).
  const [placements, setPlacements] = useState<Placement[]>([])
  const savedRef = useRef<Placement[]>([])
  const [dirtySandbox, setDirtySandbox] = useState(false)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)

  // click-to-place selection (keyboard/mobile fallback for drag & drop)
  const [selected, setSelected] = useState<string | null>(null)
  const [managing, setManaging] = useState(false)
  const [rosterFilter, setRosterFilter] = useState('')

  const load = useCallback(() => {
    setError(null)
    fetch(
      `/api/guild/${encodeURIComponent(guildName)}/raid-planning/${teamIndex}?date=${date}`,
      { credentials: 'include' },
    )
      .then(async res => {
        if (res.status === 401 || res.status === 403) { setForbidden(true); return }
        if (!res.ok) { setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        const d = (await res.json()) as PlannerData
        setData(d)
        setPlacements(d.placements)
        savedRef.current = d.placements
        setDirtySandbox(false)
      })
      .catch(err => setError(toErrorMessage(err)))
  }, [guildName, teamIndex, date])

  useEffect(load, [load])

  // ── officer autosave (debounced) ──
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!data?.is_officer) return
    if (placementsEqual(placements, savedRef.current)) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    setSaveState('saving')
    const toSave = placements
    saveTimer.current = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/guild/${encodeURIComponent(guildName)}/raid-planning/${teamIndex}/placements`,
          {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ placements: toSave }),
          },
        )
        if (!res.ok) {
          setSaveState('error')
          setSaveError((await res.json().catch(() => ({}))).detail ?? 'Save failed')
          return
        }
        savedRef.current = toSave
        setSaveState('saved')
        setSaveError(null)
      } catch (err) {
        setSaveState('error')
        setSaveError(toErrorMessage(err))
      }
    }, 700)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
  }, [placements, data?.is_officer, guildName, teamIndex])

  // ── derived state ──
  const roled = useMemo(() => (data?.roster ?? []).filter(r => r.role), [data])
  const rosterByLower = useMemo(() => {
    const m = new Map<string, RosterEntry>()
    for (const r of data?.roster ?? []) m.set(r.name.toLowerCase(), r)
    return m
  }, [data])
  const grid = useMemo(
    () => buildGrid(placements, roled.filter(r => r.role === 'raider').map(r => r.name)),
    [placements, roled],
  )
  const altBench = useMemo(() => {
    const placed = new Set(placements.map(p => p.character_name.toLowerCase()))
    return roled.filter(r => r.role === 'raid_alt' && !placed.has(r.name.toLowerCase()))
  }, [placements, roled])
  const classByChar = useMemo(() => {
    const m = new Map<string, ReturnType<typeof byName.get>>()
    for (const r of roled) m.set(r.name.toLowerCase(), r.cls ? byName.get(r.cls) : undefined)
    return m
  }, [roled, byName])
  const warnings = useMemo(
    () => (data ? computeWarnings(data, placements, classByChar) : []),
    [data, placements, classByChar],
  )

  const interactive = Boolean(data) // everyone can move; officers persist, members sandbox
  const isOfficer = Boolean(data?.is_officer)

  // ── movement ──
  function applyMove(name: string, target: DropTarget) {
    setPlacements(prev => {
      const next = moveCharacter(prev, name, target)
      if (!isOfficer && !placementsEqual(next, savedRef.current)) setDirtySandbox(true)
      return next
    })
    setSelected(null)
  }

  function dropHandler(target: DropTarget) {
    return (e: React.DragEvent) => {
      e.preventDefault()
      const name = e.dataTransfer.getData('text/x-character')
      const group = e.dataTransfer.getData('text/x-group')
      if (group && target.kind === 'slot') {
        setPlacements(prev => swapGroups(prev, Number(group), target.group))
        return
      }
      if (name) applyMove(name, target)
    }
  }

  const dragChip = (name: string) => (e: React.DragEvent) => {
    e.dataTransfer.setData('text/x-character', name)
    e.dataTransfer.effectAllowed = 'move'
  }
  const dragGroup = (group: number) => (e: React.DragEvent) => {
    e.dataTransfer.setData('text/x-group', String(group))
    e.dataTransfer.effectAllowed = 'move'
  }
  const allowDrop = (e: React.DragEvent) => e.preventDefault()

  function resetSandbox() {
    setPlacements(savedRef.current)
    setDirtySandbox(false)
  }

  async function toggleRole(name: string, role: 'raider' | 'raid_alt' | null) {
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/raid-planning/roles`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_name: name, role }),
      })
      if (!res.ok) {
        setSaveError((await res.json().catch(() => ({}))).detail ?? 'Failed to update role')
        return
      }
      load()
    } catch (err) {
      setSaveError(toErrorMessage(err))
    }
  }

  // ── render helpers ──
  const chipFor = (name: string) => {
    const entry = rosterByLower.get(name.toLowerCase())
    if (!entry) return null
    return (
      <CharChip
        entry={entry}
        availability={data?.availability[name.toLowerCase()]}
        player={data?.players[name.toLowerCase()]}
        colour={colourFor(entry.cls)}
        selected={selected?.toLowerCase() === name.toLowerCase()}
        interactive={interactive}
        onPick={() => setSelected(s => (s?.toLowerCase() === name.toLowerCase() ? null : name))}
        onDragStart={dragChip(name)}
      />
    )
  }

  const slotClasses = (occupied: boolean) =>
    `min-h-[38px] rounded-sm border ${
      occupied ? 'border-border/60 bg-surface' : 'border-dashed border-border/70 bg-surface/40'
    } ${selected ? 'hover:border-gold cursor-pointer' : ''}`

  if (forbidden) return null // not a guild member — planner simply doesn't exist for them
  if (error) return <p className="text-danger text-[0.85rem] px-1 py-2">{error}</p>
  if (!data) return <p className="text-text-muted text-[0.85rem] px-1 py-2">Loading raid plan…</p>

  const groupCount = (g: number) => grid.groups[g - 1].filter(Boolean).length
  const placedTotal = grid.groups.flat().filter(Boolean).length

  return (
    <div className="mt-3 border-t border-border/60 pt-3 flex flex-col gap-3">
      {/* header row: title, date, status chips */}
      <div className="flex flex-wrap items-center gap-2">
        <SectionLabel variant="muted" className="mb-0">Raid Plan</SectionLabel>
        <Badge variant="muted">{placedTotal}/24</Badge>
        <label className="flex items-center gap-1.5 text-[0.75rem] text-text-muted ml-auto">
          Availability for
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="bg-surface border border-border rounded-sm px-1.5 py-0.5 text-[0.8rem] text-text"
          />
        </label>
        {isOfficer && saveState === 'saving' && <Badge variant="muted">Saving…</Badge>}
        {isOfficer && saveState === 'saved' && <Badge variant="success">Saved</Badge>}
        {isOfficer && saveState === 'error' && <Badge variant="danger">Save failed</Badge>}
        {!isOfficer && dirtySandbox && (
          <span className="flex items-center gap-1.5">
            <Badge variant="warning">Sandbox — not saved</Badge>
            <Button variant="ghost" size="sm" onClick={resetSandbox}>Reset</Button>
          </span>
        )}
      </div>

      {saveError && <p className="text-danger text-[0.8rem]">{saveError}</p>}
      {selected && (
        <p className="text-[0.78rem] text-gold">
          Placing <strong>{selected}</strong> — click a slot, the sitout strip or the bench. Click the character again to cancel.
        </p>
      )}

      {/* warnings */}
      {warnings.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {warnings.map((w, i) => (
            <p key={i} className={`text-[0.78rem] ${w.severity === 'warn' ? 'text-warning' : 'text-text-muted'}`}>
              {w.severity === 'warn' ? '⚠ ' : 'ℹ '}
              {w.text}
            </p>
          ))}
        </div>
      )}

      {/* the 4 groups */}
      <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: GROUPS }, (_, gi) => gi + 1).map(g => (
          <div key={g} className="border border-border rounded-md bg-surface/60 p-2 flex flex-col gap-1">
            <div
              draggable={interactive}
              onDragStart={dragGroup(g)}
              onDragOver={allowDrop}
              onDrop={dropHandler({ kind: 'slot', group: g, slot: 0 })}
              className={`flex items-baseline justify-between px-1 pb-1 ${interactive ? 'cursor-grab' : ''}`}
              title="Drag onto another group to swap the two groups"
            >
              <span className="font-heading text-gold text-[0.9rem]">Group {g}</span>
              <span className="text-[0.68rem] text-text-muted">{groupCount(g)}/{SLOTS_PER_GROUP}</span>
            </div>
            {Array.from({ length: SLOTS_PER_GROUP }, (_, si) => {
              const p = grid.groups[g - 1][si]
              return (
                <div
                  key={si}
                  onDragOver={allowDrop}
                  onDrop={dropHandler({ kind: 'slot', group: g, slot: si })}
                  onClick={selected ? () => applyMove(selected, { kind: 'slot', group: g, slot: si }) : undefined}
                  className={slotClasses(Boolean(p))}
                >
                  {p ? chipFor(p.character_name) : (
                    <span className="block px-2 py-2 text-[0.68rem] text-text-muted/50 select-none">empty</span>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* sitout strip */}
      <div
        onDragOver={allowDrop}
        onDrop={dropHandler({ kind: 'sitout' })}
        onClick={selected ? () => applyMove(selected, { kind: 'sitout' }) : undefined}
        className={`border border-border rounded-md p-2 ${selected ? 'hover:border-gold cursor-pointer' : ''}`}
      >
        <div className="flex items-baseline gap-2 mb-1">
          <span className="font-heading text-text text-[0.85rem]">Sitout</span>
          <span className="text-[0.68rem] text-text-muted">bench for tonight — still raiders</span>
        </div>
        <div className="grid gap-1 grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 min-h-[38px]">
          {grid.sitout.map(p => (
            <div key={p.character_name}>{chipFor(p.character_name)}</div>
          ))}
          {grid.sitout.length === 0 && (
            <span className="text-[0.68rem] text-text-muted/50 px-1 py-2 select-none">nobody sitting out</span>
          )}
        </div>
      </div>

      {/* bench: rostered raiders not placed + raid alts */}
      <div
        onDragOver={allowDrop}
        onDrop={dropHandler({ kind: 'bench' })}
        onClick={selected ? () => applyMove(selected, { kind: 'bench' }) : undefined}
        className={`border border-dashed border-border rounded-md p-2 ${selected ? 'hover:border-gold cursor-pointer' : ''}`}
      >
        <div className="flex items-baseline gap-2 mb-1">
          <span className="font-heading text-text text-[0.85rem]">Unassigned</span>
          <span className="text-[0.68rem] text-text-muted">
            raiders without a spot{altBench.length > 0 ? ' · raid alts listed after' : ''}
          </span>
          {isOfficer && (
            <button
              type="button"
              onClick={() => setManaging(m => !m)}
              className="appearance-none border-0 bg-transparent ml-auto text-[0.75rem] text-gold hover:text-gold-bright cursor-pointer"
            >
              {managing ? 'Done managing' : 'Manage raiders'}
            </button>
          )}
        </div>
        <div className="grid gap-1 grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 min-h-[38px]">
          {grid.bench.map(n => (
            <div key={n}>{chipFor(n)}</div>
          ))}
          {altBench.map(r => (
            <div key={r.name}>{chipFor(r.name)}</div>
          ))}
          {grid.bench.length === 0 && altBench.length === 0 && (
            <span className="text-[0.68rem] text-text-muted/50 px-1 py-2 select-none">everyone is placed</span>
          )}
        </div>
      </div>

      {/* officer: manage which guild members are raiders / alts */}
      {isOfficer && managing && (
        <div className="border border-border rounded-md p-2 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <SectionLabel variant="muted" className="mb-0">Roster designations</SectionLabel>
            <input
              value={rosterFilter}
              onChange={e => setRosterFilter(e.target.value)}
              placeholder="filter members…"
              className="bg-surface border border-border rounded-sm px-2 py-1 text-[0.8rem] ml-auto w-[160px]"
            />
          </div>
          <div className="grid gap-x-3 gap-y-0.5 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 max-h-[320px] overflow-y-auto pr-1">
            {data.roster
              .filter(r => r.name.toLowerCase().includes(rosterFilter.toLowerCase()))
              .map(r => (
                <div key={r.name} className="flex items-center gap-2 py-0.5">
                  <span className="w-1 h-4 rounded-full shrink-0" style={{ background: colourFor(r.cls) }} />
                  <span className="text-[0.82rem] truncate flex-1" style={{ color: colourFor(r.cls) }}>
                    {r.name}
                    <span className="text-text-muted text-[0.7rem]"> {r.cls ?? ''}{r.level ? ` ${r.level}` : ''}</span>
                  </span>
                  {(['raider', 'raid_alt'] as const).map(role => (
                    <button
                      key={role}
                      type="button"
                      onClick={() => toggleRole(r.name, r.role === role ? null : role)}
                      className={`appearance-none cursor-pointer text-[0.66rem] px-1.5 py-0.5 rounded-sm border ${
                        r.role === role
                          ? 'bg-gold/20 text-gold border-gold/50'
                          : 'bg-surface text-text-muted border-border hover:border-gold/40'
                      }`}
                    >
                      {role === 'raider' ? 'Raider' : 'Alt'}
                    </button>
                  ))}
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}
