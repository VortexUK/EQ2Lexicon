import { useEffect, useMemo, useState } from 'react'

import { fmtRelative } from '../formatters'
import { useAuth } from '../hooks/useAuth'
import { Button, LinkButton, SectionLabel } from './ui'

// ── Types (mirror the route's Pydantic models) ────────────────────────────────

interface Trigger {
  id: number
  raid_encounter_id: number
  position: number
  label: string | null
  notes: string | null
  active: boolean
  regex: string
  sound_data: string
  sound_type: number
  category_restrict: boolean
  category: string | null
  timer: boolean
  timer_name: string | null
  tabbed: boolean
  last_edited_at: number | null
  last_edited_by: string | null
  created_at: number
}

interface SpellTimer {
  id: number
  raid_encounter_id: number
  name: string
  checked: boolean
  timer_duration_s: number
  only_master_ticks: boolean
  restrict: boolean
  absolute: boolean
  start_wav: string
  warning_wav: string
  warning_value: number
  radial_display: boolean
  modable: boolean
  tooltip: string
  fill_color: number
  panel1: boolean
  panel2: boolean
  remove_value: number
  category: string | null
  restrict_category: boolean
  last_edited_at: number | null
  last_edited_by: string | null
  created_at: number
}

interface Props {
  zoneName: string
  position: number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * ACT stores FillColor as a .NET ARGB packed signed int. Convert to a CSS
 * `#rrggbb` (alpha dropped for the swatch — the contributor cares about hue,
 * not the rarely-used opacity).
 */
function argbToHex(packed: number): string {
  // Convert signed-int → unsigned 32-bit, then take the bottom 24 bits.
  const unsigned = packed >>> 0
  const rgb = unsigned & 0xffffff
  return '#' + rgb.toString(16).padStart(6, '0')
}

function hexToArgb(hex: string, existing: number): number {
  // Keep the existing alpha byte so a user editing the colour swatch doesn't
  // accidentally flip the timer to fully-transparent.
  const trimmed = hex.replace(/^#/, '')
  if (trimmed.length !== 6) return existing
  const rgb = Number.parseInt(trimmed, 16)
  if (Number.isNaN(rgb)) return existing
  const alpha = (existing >>> 24) & 0xff || 0xff
  // .NET ARGB packed int — produce as signed 32-bit so it round-trips back
  // into the same negative numbers ACT writes natively.
  const packed = ((alpha << 24) | rgb) | 0
  return packed
}

/** First non-empty of {label, sound_data, regex} truncated for the summary row. */
function summarise(t: Trigger): string {
  if (t.label && t.label.trim()) return t.label.trim()
  if (t.sound_data && t.sound_data.trim()) return t.sound_data.trim()
  return t.regex.length > 80 ? t.regex.slice(0, 77) + '…' : t.regex
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ActTriggers({ zoneName, position }: Props) {
  const auth = useAuth()
  const canEdit =
    auth.status === 'authenticated' &&
    (auth.user.is_admin || auth.user.static_roles.includes('contributor'))

  const [triggers, setTriggers] = useState<Trigger[]>([])
  const [spellTimers, setSpellTimers] = useState<SpellTimer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [importing, setImporting] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const base = `/api/zones/${encodeURIComponent(zoneName)}/encounters/${position}`

  // Spell timers indexed by lowercase name for the "is this timer real?"
  // affordance on each trigger row.
  const timersByName = useMemo(() => {
    const m = new Map<string, SpellTimer>()
    for (const s of spellTimers) m.set(s.name.toLowerCase(), s)
    return m
  }, [spellTimers])

  // How many triggers reference each spell-timer (for the Spell Timers section
  // badge and the nameEditable guard).
  const triggerUsageByTimer = useMemo(() => {
    const m = new Map<string, number>()
    for (const t of triggers) {
      if (t.timer && t.timer_name) {
        const k = t.timer_name.toLowerCase()
        m.set(k, (m.get(k) ?? 0) + 1)
      }
    }
    return m
  }, [triggers])

  // Fetch on encounter change. Both endpoints are public-GET; failures
  // surface as a single error banner (the trigger list is the primary view).
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setTriggers([])
    setSpellTimers([])
    setEditingId(null)
    setImporting(false)
    setExpanded(new Set())

    Promise.all([
      fetch(`${base}/triggers`, { credentials: 'include' }).then(handle<Trigger[]>),
      fetch(`${base}/spell-timers`, { credentials: 'include' }).then(handle<SpellTimer[]>),
    ])
      .then(([ts, ss]) => {
        if (cancelled) return
        setTriggers(ts)
        setSpellTimers(ss)
      })
      .catch(err => { if (!cancelled) setError(String((err as Error).message ?? err)) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
    // base captures zoneName/position; using it directly avoids the deps lint trip-up.
  }, [base])

  function toggleExpand(id: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function refresh() {
    const [ts, ss] = await Promise.all([
      fetch(`${base}/triggers`, { credentials: 'include' }).then(handle<Trigger[]>),
      fetch(`${base}/spell-timers`, { credentials: 'include' }).then(handle<SpellTimer[]>),
    ])
    setTriggers(ts)
    setSpellTimers(ss)
  }

  async function deleteTrigger(id: number) {
    if (!confirm('Delete this trigger? Cannot be undone.')) return
    const r = await fetch(`${base}/triggers/${id}`, { method: 'DELETE', credentials: 'include' })
    if (!r.ok) {
      alert(`Failed: ${r.status} ${r.statusText}`)
      return
    }
    await refresh()
  }

  return (
    <section>
      <header className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
        <SectionLabel>ACT Triggers</SectionLabel>
        <div className="flex items-center gap-2">
          {triggers.length > 0 && (
            <LinkButton
              size="sm"
              variant="secondary"
              href={`${base}/triggers/export.xml`}
              download
            >
              Download all
            </LinkButton>
          )}
          {canEdit && !importing && (
            <Button size="sm" variant="secondary" onClick={() => { setImporting(true); setEditingId(null) }}>
              Import XML
            </Button>
          )}
          {canEdit && editingId !== 'new' && (
            <Button size="sm" variant="primary" onClick={() => { setEditingId('new'); setImporting(false) }}>
              Add trigger
            </Button>
          )}
        </div>
      </header>

      {loading && <p className="text-text-muted text-sm">Loading…</p>}
      {error && <p className="text-danger text-sm">Couldn't load triggers: {error}</p>}

      {!loading && !error && (
        <>
          {importing && (
            <XmlImporter
              base={base}
              onCancel={() => setImporting(false)}
              onImported={async () => {
                setImporting(false)
                await refresh()
              }}
            />
          )}

          {editingId === 'new' && (
            <TriggerEditor
              base={base}
              spellTimers={spellTimers}
              onCancel={() => setEditingId(null)}
              onSaved={async () => {
                setEditingId(null)
                await refresh()
              }}
            />
          )}

          {triggers.length === 0 && editingId !== 'new' && (
            <p className="text-text-muted text-sm leading-relaxed">
              No ACT triggers shared for this encounter yet.
              {canEdit && <> Click <em>Add trigger</em> above to contribute one.</>}
            </p>
          )}

          {triggers.length > 0 && (
            <ul className="border border-border rounded-md divide-y divide-border/60 overflow-hidden mt-1">
              {triggers.map(t => (
                <li key={t.id} className="bg-surface-raised/30">
                  {editingId === t.id ? (
                    <TriggerEditor
                      base={base}
                      spellTimers={spellTimers}
                      existing={t}
                      existingTimer={t.timer_name ? timersByName.get(t.timer_name.toLowerCase()) ?? null : null}
                      onCancel={() => setEditingId(null)}
                      onSaved={async () => {
                        setEditingId(null)
                        await refresh()
                      }}
                    />
                  ) : (
                    <TriggerRow
                      trigger={t}
                      timer={t.timer_name ? timersByName.get(t.timer_name.toLowerCase()) ?? null : null}
                      expanded={expanded.has(t.id)}
                      onToggle={() => toggleExpand(t.id)}
                      canEdit={canEdit}
                      onEdit={() => setEditingId(t.id)}
                      onDelete={() => deleteTrigger(t.id)}
                      exportUrl={`${base}/triggers/${t.id}/export.xml`}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
          <SpellTimersSection
            base={base}
            spellTimers={spellTimers}
            triggerUsageByTimer={triggerUsageByTimer}
            canEdit={canEdit}
            onReload={refresh}
          />
        </>
      )}
    </section>
  )
}

// ── Per-trigger row ───────────────────────────────────────────────────────────

interface TriggerRowProps {
  trigger: Trigger
  timer: SpellTimer | null
  expanded: boolean
  onToggle: () => void
  canEdit: boolean
  onEdit: () => void
  onDelete: () => void
  exportUrl: string
}

function TriggerRow({
  trigger, timer, expanded, onToggle, canEdit, onEdit, onDelete, exportUrl,
}: TriggerRowProps) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-baseline gap-3 px-3 py-2 text-left hover:bg-surface-raised/60 appearance-none border-0 bg-transparent"
        aria-expanded={expanded}
      >
        <span className="text-[0.72rem] text-gold-dim w-4 shrink-0">{expanded ? '▾' : '▸'}</span>
        <span className="flex-1 min-w-0 text-[0.92rem] text-text">
          {summarise(trigger)}
          {!trigger.active && (
            <span className="ml-2 text-text-muted text-[0.7rem] uppercase tracking-[0.08em]">disabled</span>
          )}
        </span>
        {trigger.timer && trigger.timer_name && (
          <span
            className="text-[0.72rem] px-2 py-[1px] rounded-sm border border-border bg-bg/60 shrink-0"
            title={timer ? `Defined: ${timer.timer_duration_s}s` : 'Timer name referenced but not defined for this encounter'}
            style={timer ? { borderColor: argbToHex(timer.fill_color) } : undefined}
          >
            ⏱ {trigger.timer_name}
            {timer && <span className="text-text-muted ml-1">{timer.timer_duration_s}s</span>}
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-3 pt-1 border-t border-border/60 bg-bg/40">
          <TriggerDetail trigger={trigger} timer={timer} />
          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <LinkButton size="sm" variant="secondary" href={exportUrl} download>
              Download XML
            </LinkButton>
            {canEdit && (
              <>
                <Button size="sm" variant="ghost" onClick={onEdit}>Edit</Button>
                <Button size="sm" variant="danger" onClick={onDelete}>Delete</Button>
              </>
            )}
            {trigger.last_edited_at && (
              <span className="ml-auto text-text-muted text-[0.72rem]">
                Edited {fmtRelative(trigger.last_edited_at)}
                {trigger.last_edited_by ? ` · ${trigger.last_edited_by}` : ''}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Per-trigger expanded detail ───────────────────────────────────────────────

function TriggerDetail({ trigger, timer }: { trigger: Trigger; timer: SpellTimer | null }) {
  return (
    <div className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[0.85rem]">
      <Label>Regex</Label>
      <code className="bg-bg/60 border border-border rounded-sm px-2 py-1 font-mono text-[0.82rem] break-all">
        {trigger.regex}
      </code>

      <Label>Sound</Label>
      <span className="text-text">
        {trigger.sound_data || <em className="text-text-muted">none</em>}
        <span className="text-text-muted ml-2 text-[0.78rem]">
          ({trigger.sound_type === 3 ? 'TTS' : trigger.sound_type === 0 ? 'silent / file' : `type ${trigger.sound_type}`})
        </span>
      </span>

      <Label>Category</Label>
      <span className="text-text">
        {trigger.category || <em className="text-text-muted">—</em>}
        {trigger.category_restrict && (
          <span className="text-text-muted ml-2 text-[0.78rem]">(restricted)</span>
        )}
      </span>

      <Label>Tabbed</Label>
      <span className="text-text">{trigger.tabbed ? 'Yes' : 'No'}</span>

      {trigger.notes && (
        <>
          <Label>Notes</Label>
          <span className="text-text whitespace-pre-wrap leading-relaxed">{trigger.notes}</span>
        </>
      )}

      {trigger.timer && trigger.timer_name && (
        <>
          <Label>Spell timer</Label>
          <div className="text-text">
            <span className="font-semibold">{trigger.timer_name}</span>
            {timer ? (
              <SpellTimerDetail timer={timer} />
            ) : (
              <p className="text-danger text-[0.78rem] mt-1">
                ⚠ Referenced by this trigger but no matching Spell timer is defined for this encounter.
                Add one with the same name so the XML export round-trips cleanly.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function SpellTimerDetail({ timer }: { timer: SpellTimer }) {
  return (
    <div className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-[2px] text-[0.78rem]">
      <Mini>Duration</Mini>     <span className="text-text">{timer.timer_duration_s}s</span>
      <Mini>Warning</Mini>      <span className="text-text">{timer.warning_value}s before end</span>
      <Mini>Colour</Mini>       <span className="flex items-center gap-2 text-text"><Swatch packed={timer.fill_color} /> <code className="font-mono text-[0.72rem]">{argbToHex(timer.fill_color)}</code></span>
      <Mini>Panels</Mini>       <span className="text-text">{[timer.panel1 && '1', timer.panel2 && '2'].filter(Boolean).join(' & ') || '—'}</span>
      <Mini>Absolute</Mini>     <span className="text-text">{timer.absolute ? 'Yes' : 'No'}</span>
      <Mini>Master ticks</Mini> <span className="text-text">{timer.only_master_ticks ? 'Yes' : 'No'}</span>
      {timer.tooltip && (<><Mini>Tooltip</Mini><span className="text-text">{timer.tooltip}</span></>)}
    </div>
  )
}

function Swatch({ packed }: { packed: number }) {
  return (
    <span
      className="inline-block w-4 h-4 rounded-sm border border-border align-middle"
      style={{ backgroundColor: argbToHex(packed) }}
      aria-hidden
    />
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-gold-dim uppercase tracking-[0.08em] text-[0.7rem] pt-1">{children}</span>
}

function Mini({ children }: { children: React.ReactNode }) {
  return <span className="text-text-muted uppercase tracking-[0.06em] text-[0.68rem]">{children}</span>
}

// ── Editor (combined Trigger + optional Spell timer) ──────────────────────────

interface EditorProps {
  base: string
  spellTimers: SpellTimer[]
  existing?: Trigger
  /** The spell-timer row corresponding to `existing.timer_name`, if any. */
  existingTimer?: SpellTimer | null
  onCancel: () => void
  onSaved: () => Promise<void>
}

interface TriggerDraft {
  label: string
  notes: string
  regex: string
  sound_data: string
  sound_type: number
  category_restrict: boolean
  category: string
  timer: boolean
  timer_name: string
  tabbed: boolean
  active: boolean
}

interface SpellTimerDraft {
  name: string
  timer_duration_s: number
  warning_value: number
  fill_color_hex: string
  fill_color_packed: number
  panel1: boolean
  panel2: boolean
  absolute: boolean
  only_master_ticks: boolean
  tooltip: string
}

// ── Shared SpellTimerEditor sub-form ──────────────────────────────────────────

interface SpellTimerEditorProps {
  draft: SpellTimerDraft
  onChange: (next: SpellTimerDraft) => void
  nameEditable?: boolean
  /** Fired on blur of the name field (no-op when nameEditable is false). */
  onNameBlur?: (name: string) => void
}

function SpellTimerEditor({ draft, onChange, nameEditable = true, onNameBlur }: SpellTimerEditorProps) {
  return (
    <>
      <Field label="Timer name *">
        {nameEditable ? (
          <input
            type="text"
            value={draft.name}
            onChange={e => onChange({ ...draft, name: e.target.value })}
            onBlur={e => onNameBlur?.(e.target.value)}
            placeholder="e.g. Doom Cooldown"
            className={inputCls}
          />
        ) : (
          <div className={inputCls + ' text-text-muted cursor-not-allowed select-none'}>
            {draft.name}
            <span className="ml-2 text-[0.72rem] text-gold-dim uppercase tracking-[0.06em]">(in use — rename blocked)</span>
          </div>
        )}
      </Field>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <Field label="Duration (s)">
          <input
            type="number"
            min={1}
            value={draft.timer_duration_s}
            onChange={e => onChange({ ...draft, timer_duration_s: Number(e.target.value) })}
            className={inputCls}
          />
        </Field>
        <Field label="Warning (s)">
          <input
            type="number"
            min={0}
            value={draft.warning_value}
            onChange={e => onChange({ ...draft, warning_value: Number(e.target.value) })}
            className={inputCls}
          />
        </Field>
        <Field label="Fill colour">
          <div className="flex items-center gap-2">
            <input
              type="color"
              value={draft.fill_color_hex}
              onChange={e => {
                const hex = e.target.value
                onChange({
                  ...draft,
                  fill_color_hex: hex,
                  fill_color_packed: hexToArgb(hex, draft.fill_color_packed),
                })
              }}
              className="h-8 w-12 border border-border rounded-sm bg-bg/60"
            />
            <code className="font-mono text-[0.78rem] text-text-muted">{draft.fill_color_hex}</code>
          </div>
        </Field>
      </div>

      <Field label="Tooltip">
        <input
          type="text"
          value={draft.tooltip}
          onChange={e => onChange({ ...draft, tooltip: e.target.value })}
          className={inputCls}
        />
      </Field>

      <div className="flex items-center gap-4 flex-wrap text-[0.85rem]">
        <Checkbox
          label="Panel 1"
          checked={draft.panel1}
          onChange={v => onChange({ ...draft, panel1: v })}
        />
        <Checkbox
          label="Panel 2"
          checked={draft.panel2}
          onChange={v => onChange({ ...draft, panel2: v })}
        />
        <Checkbox
          label="Absolute"
          checked={draft.absolute}
          onChange={v => onChange({ ...draft, absolute: v })}
        />
        <Checkbox
          label="Master ticks"
          checked={draft.only_master_ticks}
          onChange={v => onChange({ ...draft, only_master_ticks: v })}
        />
      </div>
    </>
  )
}

function defaultTriggerDraft(t?: Trigger): TriggerDraft {
  return {
    label: t?.label ?? '',
    notes: t?.notes ?? '',
    regex: t?.regex ?? '',
    sound_data: t?.sound_data ?? '',
    sound_type: t?.sound_type ?? 3,
    category_restrict: t?.category_restrict ?? false,
    category: t?.category ?? '',
    timer: t?.timer ?? false,
    timer_name: t?.timer_name ?? '',
    tabbed: t?.tabbed ?? false,
    active: t?.active ?? true,
  }
}

function defaultSpellTimerDraft(s?: SpellTimer | null, nameHint?: string): SpellTimerDraft {
  const packed = s?.fill_color ?? -16776961
  return {
    name: s?.name ?? nameHint ?? '',
    timer_duration_s: s?.timer_duration_s ?? 30,
    warning_value: s?.warning_value ?? 10,
    fill_color_hex: argbToHex(packed),
    fill_color_packed: packed,
    panel1: s?.panel1 ?? true,
    panel2: s?.panel2 ?? false,
    absolute: s?.absolute ?? false,
    only_master_ticks: s?.only_master_ticks ?? false,
    tooltip: s?.tooltip ?? '',
  }
}

function TriggerEditor({ base, spellTimers, existing, existingTimer, onCancel, onSaved }: EditorProps) {
  const [draft, setDraft] = useState<TriggerDraft>(() => defaultTriggerDraft(existing))
  const [timerDraft, setTimerDraft] = useState<SpellTimerDraft>(() =>
    defaultSpellTimerDraft(existingTimer ?? null, existing?.timer_name ?? '')
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // If the user types a timer name that matches an existing spell-timer for
  // this encounter, snap the timer-draft to that row so they edit the same
  // record. Otherwise treat it as a fresh definition.
  function onTimerNameBlur(name: string) {
    const want = name.trim().toLowerCase()
    if (!want) return
    const hit = spellTimers.find(s => s.name.toLowerCase() === want)
    if (hit && hit.id !== existingTimer?.id) {
      setTimerDraft(defaultSpellTimerDraft(hit))
    }
  }

  // Intercept SpellTimerEditor onChange: when the name changes, keep
  // draft.timer_name in sync so the trigger body stays consistent.
  function handleTimerDraftChange(next: SpellTimerDraft) {
    setTimerDraft(next)
    if (next.name !== timerDraft.name) {
      setDraft(d => ({ ...d, timer_name: next.name }))
    }
  }

  async function save() {
    if (!draft.regex.trim()) {
      setError('Regex is required.')
      return
    }
    if (draft.timer && !timerDraft.name.trim()) {
      setError('Timer enabled but no Timer name given.')
      return
    }

    setSaving(true)
    setError(null)
    try {
      // Always write the trigger row. The route stamps `category` to mob_name
      // when blank — so an empty category here means "default to boss name".
      const triggerBody = {
        label: draft.label.trim() || null,
        notes: draft.notes.trim() || null,
        regex: draft.regex,
        sound_data: draft.sound_data,
        sound_type: draft.sound_type,
        category_restrict: draft.category_restrict,
        category: draft.category.trim() || null,
        timer: draft.timer,
        timer_name: draft.timer ? timerDraft.name.trim() || null : null,
        tabbed: draft.tabbed,
        active: draft.active,
        position: existing?.position ?? 0,
      }

      const triggerUrl = existing ? `${base}/triggers/${existing.id}` : `${base}/triggers`
      const triggerMethod = existing ? 'PUT' : 'POST'
      const r1 = await fetch(triggerUrl, {
        method: triggerMethod,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(triggerBody),
      })
      if (!r1.ok) throw new Error(`Trigger save failed: ${r1.status} ${r1.statusText}`)

      // If the trigger uses a timer, upsert the matching spell-timer row.
      // Match by name (UNIQUE within encounter); fall back to POST then
      // gracefully handle the 409 by switching to PUT against the existing.
      if (draft.timer && timerDraft.name.trim()) {
        const timerBody = {
          name: timerDraft.name.trim(),
          timer_duration_s: timerDraft.timer_duration_s,
          warning_value: timerDraft.warning_value,
          fill_color: timerDraft.fill_color_packed,
          panel1: timerDraft.panel1,
          panel2: timerDraft.panel2,
          absolute: timerDraft.absolute,
          only_master_ticks: timerDraft.only_master_ticks,
          tooltip: timerDraft.tooltip,
        }

        // Existing row known? PUT directly.
        const target = spellTimers.find(
          s => s.name.toLowerCase() === timerDraft.name.trim().toLowerCase()
        ) ?? existingTimer ?? null

        let r2: Response
        if (target) {
          r2 = await fetch(`${base}/spell-timers/${target.id}`, {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(timerBody),
          })
        } else {
          r2 = await fetch(`${base}/spell-timers`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(timerBody),
          })
        }
        if (!r2.ok) throw new Error(`Spell-timer save failed: ${r2.status} ${r2.statusText}`)
      }

      await onSaved()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="px-4 py-3 bg-bg/40">
      <h4 className="font-heading text-gold text-[1rem] mb-2">
        {existing ? 'Edit trigger' : 'New trigger'}
      </h4>

      <div className="flex flex-col gap-2">
        <Field label="Label">
          <input
            type="text"
            value={draft.label}
            onChange={e => setDraft({ ...draft, label: e.target.value })}
            placeholder="Short summary shown in the row"
            className={inputCls}
          />
        </Field>

        <Field label="Regex *">
          <textarea
            value={draft.regex}
            onChange={e => setDraft({ ...draft, regex: e.target.value })}
            rows={3}
            spellCheck={false}
            placeholder={'^\\\\aPC -1 (?<Caster>\\S+)\\\\/a is casting Doom\\.$'}
            className={inputCls + ' font-mono text-[0.85rem]'}
          />
        </Field>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <Field label="Sound (TTS text)">
            <input
              type="text"
              value={draft.sound_data}
              onChange={e => setDraft({ ...draft, sound_data: e.target.value })}
              className={inputCls}
            />
          </Field>
          <Field label="Sound type">
            <select
              value={draft.sound_type}
              onChange={e => setDraft({ ...draft, sound_type: Number(e.target.value) })}
              className={inputCls}
            >
              <option value={3}>TTS (3)</option>
              <option value={0}>Silent / file (0)</option>
            </select>
          </Field>
        </div>

        <Field label="Notes (contributor-only, not exported)">
          <textarea
            value={draft.notes}
            onChange={e => setDraft({ ...draft, notes: e.target.value })}
            rows={2}
            className={inputCls}
          />
        </Field>

        <div className="flex items-center gap-4 flex-wrap text-[0.85rem]">
          <Checkbox
            label="Active"
            checked={draft.active}
            onChange={v => setDraft({ ...draft, active: v })}
          />
          <Checkbox
            label="Tabbed"
            checked={draft.tabbed}
            onChange={v => setDraft({ ...draft, tabbed: v })}
          />
          <Checkbox
            label="Has timer"
            checked={draft.timer}
            onChange={v => setDraft({ ...draft, timer: v })}
          />
        </div>

        {draft.timer && (
          <div className="border border-border rounded-md p-3 bg-surface-raised/30 flex flex-col gap-2">
            <h5 className="font-heading text-gold-dim text-[0.85rem] uppercase tracking-[0.08em]">
              Spell timer
            </h5>

            <SpellTimerEditor
              draft={timerDraft}
              onChange={handleTimerDraftChange}
              onNameBlur={onTimerNameBlur}
            />
          </div>
        )}

        {error && <p className="text-danger text-sm">{error}</p>}

        <div className="flex items-center gap-2 justify-end mt-1">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
          <Button size="sm" variant="primary" onClick={save} disabled={saving || !draft.regex.trim()}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Tiny editor sub-primitives ────────────────────────────────────────────────

const inputCls =
  'w-full bg-bg/60 border border-border rounded-sm px-2 py-1 text-text outline-none focus:border-gold/60 appearance-none'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-gold-dim uppercase tracking-[0.08em] text-[0.7rem]">{label}</span>
      {children}
    </label>
  )
}

function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        className="appearance-none w-4 h-4 border border-border rounded-sm bg-bg/60 checked:bg-gold/40 checked:border-gold cursor-pointer"
      />
      <span className="text-text">{label}</span>
    </label>
  )
}

// ── Spell Timers section ──────────────────────────────────────────────────────

interface SpellTimersSectionProps {
  base: string
  spellTimers: SpellTimer[]
  triggerUsageByTimer: Map<string, number>
  canEdit: boolean
  onReload: () => Promise<void>
}

function SpellTimersSection({
  base, spellTimers, triggerUsageByTimer, canEdit, onReload,
}: SpellTimersSectionProps) {
  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [timerDraft, setTimerDraft] = useState<SpellTimerDraft>(defaultSpellTimerDraft)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)

  const sorted = [...spellTimers].sort((a, b) => a.name.localeCompare(b.name))

  function startNew() {
    setTimerDraft(defaultSpellTimerDraft())
    setSaveError(null)
    setEditingId('new')
  }

  function startEdit(timer: SpellTimer) {
    setTimerDraft(defaultSpellTimerDraft(timer))
    setSaveError(null)
    setEditingId(timer.id)
  }

  function cancel() {
    setEditingId(null)
    setSaveError(null)
  }

  async function saveNew() {
    if (!timerDraft.name.trim()) {
      setSaveError('Timer name is required.')
      return
    }
    setSaving(true)
    setSaveError(null)
    try {
      const body = buildTimerBody(timerDraft)
      const r = await fetch(`${base}/spell-timers`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (r.status === 409) {
        setSaveError('A spell timer with that name already exists for this encounter.')
        return
      }
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      setEditingId(null)
      await onReload()
    } catch (e) {
      setSaveError(String((e as Error).message ?? e))
    } finally {
      setSaving(false)
    }
  }

  async function saveExisting(id: number) {
    if (!timerDraft.name.trim()) {
      setSaveError('Timer name is required.')
      return
    }
    setSaving(true)
    setSaveError(null)
    try {
      const body = buildTimerBody(timerDraft)
      const r = await fetch(`${base}/spell-timers/${id}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (r.status === 409) {
        setSaveError('A spell timer with that name already exists for this encounter.')
        return
      }
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      setEditingId(null)
      await onReload()
    } catch (e) {
      setSaveError(String((e as Error).message ?? e))
    } finally {
      setSaving(false)
    }
  }

  async function deleteTimer(id: number, name: string) {
    const usedBy = triggerUsageByTimer.get(name.toLowerCase()) ?? 0
    const msg = usedBy > 0
      ? `Delete "${name}"? It is referenced by ${usedBy} trigger${usedBy === 1 ? '' : 's'} — those triggers will lose their timer. Cannot be undone.`
      : `Delete "${name}"? Cannot be undone.`
    if (!confirm(msg)) return
    setSaveError(null)
    const r = await fetch(`${base}/spell-timers/${id}`, { method: 'DELETE', credentials: 'include' })
    if (!r.ok) {
      setSaveError(`Failed to delete: ${r.status} ${r.statusText}`)
      return
    }
    await onReload()
  }

  return (
    <section className="mt-6">
      <header className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
        <SectionLabel>Spell Timers</SectionLabel>
        <div className="flex items-center gap-2">
          {canEdit && !importing && (
            <Button size="sm" variant="secondary" onClick={() => { setImporting(true); setEditingId(null) }}>
              Import XML
            </Button>
          )}
          {canEdit && editingId !== 'new' && (
            <Button size="sm" variant="primary" onClick={() => { startNew(); setImporting(false) }}>
              New spell timer
            </Button>
          )}
        </div>
      </header>

      {importing && (
        <XmlImporter
          base={base}
          onCancel={() => setImporting(false)}
          onImported={async () => {
            setImporting(false)
            await onReload()
          }}
        />
      )}

      {editingId === null && saveError && (
        <p className="text-danger text-sm mb-2">{saveError}</p>
      )}

      {editingId === 'new' && (
        <div className="border border-border rounded-md p-3 bg-bg/40 mb-2 flex flex-col gap-2">
          <h4 className="font-heading text-gold text-[1rem]">New spell timer</h4>
          <SpellTimerEditor draft={timerDraft} onChange={setTimerDraft} nameEditable={true} />
          {saveError && <p className="text-danger text-sm">{saveError}</p>}
          <div className="flex items-center gap-2 justify-end mt-1">
            <Button size="sm" variant="ghost" onClick={cancel} disabled={saving}>Cancel</Button>
            <Button size="sm" variant="primary" onClick={saveNew} disabled={saving || !timerDraft.name.trim()}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      )}

      {sorted.length === 0 && editingId !== 'new' && (
        <p className="text-text-muted text-sm leading-relaxed">
          No spell timers defined for this encounter yet.
          {canEdit && <> Click <em>New spell timer</em> above to add a standalone timer.</>}
        </p>
      )}

      {sorted.length > 0 && (
        <ul className="border border-border rounded-md divide-y divide-border/60 overflow-hidden mt-1">
          {sorted.map(timer => {
            const usedBy = triggerUsageByTimer.get(timer.name.toLowerCase()) ?? 0
            return (
              <li key={timer.id} className="bg-surface-raised/30">
                {editingId === timer.id ? (
                  <div className="px-4 py-3 bg-bg/40 flex flex-col gap-2">
                    <h4 className="font-heading text-gold text-[1rem]">Edit spell timer</h4>
                    <SpellTimerEditor
                      draft={timerDraft}
                      onChange={setTimerDraft}
                      nameEditable={usedBy === 0}
                    />
                    {saveError && <p className="text-danger text-sm">{saveError}</p>}
                    <div className="flex items-center gap-2 justify-end mt-1">
                      <Button size="sm" variant="ghost" onClick={cancel} disabled={saving}>Cancel</Button>
                      <Button size="sm" variant="primary" onClick={() => saveExisting(timer.id)} disabled={saving || !timerDraft.name.trim()}>
                        {saving ? 'Saving…' : 'Save'}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 px-3 py-2 flex-wrap">
                    <span
                      className="w-3 h-3 rounded-sm border border-border shrink-0"
                      style={{ backgroundColor: argbToHex(timer.fill_color) }}
                      aria-hidden
                    />
                    <span className="flex-1 min-w-0 text-[0.92rem] text-text font-medium truncate">
                      {timer.name}
                    </span>
                    <span className="text-text-muted text-[0.82rem] shrink-0">{timer.timer_duration_s}s</span>
                    <span className="text-[0.72rem] text-text-muted shrink-0">
                      {[timer.panel1 && 'P1', timer.panel2 && 'P2', timer.absolute && 'Abs', timer.only_master_ticks && 'MT']
                        .filter(Boolean).join(' · ') || ''}
                    </span>
                    <span
                      className={[
                        'text-[0.7rem] px-2 py-[1px] rounded-sm border shrink-0',
                        usedBy > 0
                          ? 'border-gold/40 bg-gold/10 text-gold-dim'
                          : 'border-border bg-bg/60 text-text-muted',
                      ].join(' ')}
                    >
                      {usedBy > 0
                        ? `used by ${usedBy} trigger${usedBy === 1 ? '' : 's'}`
                        : 'standalone'}
                    </span>
                    <div className="flex items-center gap-2 shrink-0 ml-auto">
                      <LinkButton size="sm" variant="ghost" href={`${base}/spell-timers/${timer.id}/export.xml`} download>
                        Export
                      </LinkButton>
                      {canEdit && (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => startEdit(timer)}>Edit</Button>
                          <Button size="sm" variant="danger" onClick={() => deleteTimer(timer.id, timer.name)}>Delete</Button>
                        </>
                      )}
                    </div>
                    {timer.last_edited_at && !canEdit && (
                      <span className="text-text-muted text-[0.72rem]">
                        Edited {fmtRelative(timer.last_edited_at)}
                      </span>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function buildTimerBody(d: SpellTimerDraft) {
  return {
    name: d.name.trim(),
    timer_duration_s: d.timer_duration_s,
    warning_value: d.warning_value,
    fill_color: d.fill_color_packed,
    panel1: d.panel1,
    panel2: d.panel2,
    absolute: d.absolute,
    only_master_ticks: d.only_master_ticks,
    tooltip: d.tooltip,
  }
}

// ── XML paste-import ──────────────────────────────────────────────────────────

interface XmlImporterProps {
  base: string
  onCancel: () => void
  onImported: () => Promise<void>
}

interface ImportResult {
  triggers_added: number
  triggers_skipped_existing: number
  spell_timers_added: number
}

/**
 * Paste-import form. Accepts ACT's shareable short form
 * (`<Trigger R="..." SD="..." ST="3" CR="F" C="..." T="T" TN="..." Ta="F" />`)
 * — what you get from right-click → Copy as Shareable XML — and also the
 * verbose form ACT exports to `spell_timers.xml`. Multiple `<Trigger>` /
 * `<Spell>` siblings in one paste are fine.
 */
function XmlImporter({ base, onCancel, onImported }: XmlImporterProps) {
  const [xml, setXml] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  async function submit() {
    if (!xml.trim()) {
      setError('Paste a trigger XML snippet first.')
      return
    }
    setImporting(true)
    setError(null)
    setResult(null)
    try {
      const r = await fetch(`${base}/triggers/import-xml`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ xml }),
      })
      if (!r.ok) {
        // Try to surface the server's error detail (e.g. "Invalid XML: ...").
        let detail = `${r.status} ${r.statusText}`
        try {
          const body = await r.json()
          if (body?.detail) detail = body.detail
        } catch {
          // non-JSON response, keep status line
        }
        throw new Error(detail)
      }
      const data = (await r.json()) as ImportResult
      setResult(data)
      // Auto-close on a clean import; keep open on a "0 added, all duped"
      // case so the user sees the result.
      if (data.triggers_added > 0 || data.spell_timers_added > 0) {
        await onImported()
      }
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="px-4 py-3 bg-bg/40 border border-border rounded-md mb-2">
      <h4 className="font-heading text-gold text-[1rem] mb-1">Import trigger from XML</h4>
      <p className="text-text-muted text-[0.78rem] leading-relaxed mb-2">
        Paste the snippet from ACT's right-click → <em>Copy as Shareable XML</em>.
        You can paste one trigger, several at once, or a trigger plus its
        matching <code className="font-mono">&lt;Spell&gt;</code> timer.
      </p>

      <textarea
        value={xml}
        onChange={e => setXml(e.target.value)}
        rows={5}
        spellCheck={false}
        placeholder={'<Trigger R="..." SD="..." ST="3" CR="F" C="..." T="T" TN="..." Ta="F" />'}
        className={inputCls + ' font-mono text-[0.82rem] resize-y'}
      />

      {error && <p className="text-danger text-sm mt-2">{error}</p>}
      {result && !error && (
        <p className="text-success text-sm mt-2">
          Imported {result.triggers_added} trigger{result.triggers_added === 1 ? '' : 's'}
          {result.spell_timers_added > 0 && (
            <> + {result.spell_timers_added} spell timer{result.spell_timers_added === 1 ? '' : 's'}</>
          )}
          {result.triggers_skipped_existing > 0 && (
            <span className="text-text-muted"> · {result.triggers_skipped_existing} duplicate{result.triggers_skipped_existing === 1 ? '' : 's'} skipped</span>
          )}
        </p>
      )}

      <div className="flex items-center gap-2 justify-end mt-2">
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={importing}>Cancel</Button>
        <Button size="sm" variant="primary" onClick={submit} disabled={importing || !xml.trim()}>
          {importing ? 'Importing…' : 'Import'}
        </Button>
      </div>
    </div>
  )
}

// ── Generic JSON-fetch helper ─────────────────────────────────────────────────

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return (await r.json()) as T
}
