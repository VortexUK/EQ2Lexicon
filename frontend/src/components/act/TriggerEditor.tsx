import { useState } from 'react'
import { Button } from '../ui'
import { SpellTimerEditor } from './SpellTimerEditor'
import { Checkbox, Field, inputCls } from './primitives'
import type { SpellTimer, SpellTimerDraft, Trigger } from './types'
import { buildTimerBody, defaultSpellTimerDraft } from './types'

// ── Editor (combined Trigger + optional Spell timer) ──────────────────────────

export interface TriggerEditorProps {
  base: string
  spellTimers: SpellTimer[]
  existing?: Trigger
  /** The spell-timer row corresponding to `existing.timer_name`, if any. */
  existingTimer?: SpellTimer | null
  onCancel: () => void
  onSaved: () => Promise<void>
}

export interface TriggerDraft {
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
  /** EQ2Parser enrichment — never in ACT XML. */
  cooldown_seconds: number
}

export function defaultTriggerDraft(t?: Trigger): TriggerDraft {
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
    cooldown_seconds: t?.cooldown_seconds ?? 1,
  }
}

export function TriggerEditor({ base, spellTimers, existing, existingTimer, onCancel, onSaved }: TriggerEditorProps) {
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
        cooldown_seconds: draft.cooldown_seconds,
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
        const timerBody = buildTimerBody(timerDraft)

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
          <Field label="Cooldown (s) — EQ2Parser only; ACT fixes this at 1s">
            <input
              type="number"
              min={0}
              step={0.5}
              value={draft.cooldown_seconds}
              onChange={e => setDraft({ ...draft, cooldown_seconds: Number(e.target.value) })}
              className={inputCls}
            />
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
