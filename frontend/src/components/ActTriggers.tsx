import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useFetch } from '../hooks/useFetch'

import { fmtRelative } from '../formatters'
import { useAuth, isContributor } from '../hooks/useAuth'
import { Button, LinkButton, SectionLabel } from './ui'

import { ActImportPanel } from './act/ActImportPanel'
import { TriggerEditor } from './act/TriggerEditor'
import { SpellTimerEditor } from './act/SpellTimerEditor'
import {
  Trigger,
  SpellTimer,
  SpellTimerDraft,
  argbToHex,
  defaultSpellTimerDraft,
  buildTimerBody,
} from './act/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

/** First non-empty of {label, sound_data, regex} truncated for the summary row. */
function summarise(t: Trigger): string {
  if (t.label && t.label.trim()) return t.label.trim()
  if (t.sound_data && t.sound_data.trim()) return t.sound_data.trim()
  return t.regex.length > 80 ? t.regex.slice(0, 77) + '…' : t.regex
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  zoneName: string
  position: number
}

export function ActTriggers({ zoneName, position }: Props) {
  const auth = useAuth()
  const canEdit = isContributor(auth)

  const base = `/api/zones/${encodeURIComponent(zoneName)}/encounters/${position}`

  const {
    data: triggersData,
    loading: triggersLoading,
    error: triggersError,
    refetch: refetchTriggers,
  } = useFetch<Trigger[]>(`${base}/triggers`)

  const {
    data: spellTimersData,
    loading: spellTimersLoading,
    error: spellTimersError,
    refetch: refetchSpellTimers,
  } = useFetch<SpellTimer[]>(`${base}/spell-timers`)

  const triggers = triggersData ?? []
  const spellTimers = spellTimersData ?? []
  const loading = triggersLoading || spellTimersLoading
  const error = triggersError ?? spellTimersError ?? null

  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [importing, setImporting] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  // Reset UI state when the encounter changes.
  useEffect(() => {
    setEditingId(null)
    setImporting(false)
    setExpanded(new Set())
  }, [base])

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

  function toggleExpand(id: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function refresh() {
    refetchTriggers()
    refetchSpellTimers()
  }

  async function deleteTrigger(id: number) {
    if (!confirm('Delete this trigger? Cannot be undone.')) return
    const r = await fetch(`${base}/triggers/${id}`, { method: 'DELETE', credentials: 'include' })
    if (!r.ok) {
      alert(`Failed: ${r.status} ${r.statusText}`)
      return
    }
    refresh()
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
            <ActImportPanel
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
    <div className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[0.78rem]">
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

function Label({ children }: { children: ReactNode }) {
  return <span className="text-gold-dim uppercase tracking-[0.08em] text-[0.7rem] pt-1">{children}</span>
}

function Mini({ children }: { children: ReactNode }) {
  return <span className="text-text-muted uppercase tracking-[0.06em] text-[0.68rem]">{children}</span>
}

// ── Spell Timers section ──────────────────────────────────────────────────────

interface SpellTimersSectionProps {
  base: string
  spellTimers: SpellTimer[]
  triggerUsageByTimer: Map<string, number>
  canEdit: boolean
  onReload: () => void | Promise<void>
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
        <ActImportPanel
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
