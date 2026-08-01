import { Checkbox, Field, inputCls } from './primitives'
import type { SpellTimerDraft } from './types'
import { hexToArgb } from './types'
import { CONTROL_EFFECTS, DAMAGE_SCHOOLS, parseSchools, toggleSchool } from './vocabulary'

// ── Shared SpellTimerEditor sub-form ──────────────────────────────────────────

interface SpellTimerEditorProps {
  draft: SpellTimerDraft
  onChange: (next: SpellTimerDraft) => void
  nameEditable?: boolean
  /** Fired on blur of the name field (no-op when nameEditable is false). */
  onNameBlur?: (name: string) => void
}

export function SpellTimerEditor({ draft, onChange, nameEditable = true, onNameBlur }: SpellTimerEditorProps) {
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

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
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
        <Field label="Remove at (s)">
          <input
            type="number"
            title="Seconds remaining when the bar disappears — negative keeps counting past zero"
            value={draft.remove_value}
            onChange={e => onChange({ ...draft, remove_value: Number(e.target.value) })}
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

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <Field label="Start sound (text / 'tts' = speak name)">
          <input
            type="text"
            value={draft.start_wav}
            onChange={e => onChange({ ...draft, start_wav: e.target.value })}
            className={inputCls}
          />
        </Field>
        <Field label="Warning sound ('tts' = name + soon)">
          <input
            type="text"
            value={draft.warning_wav}
            onChange={e => onChange({ ...draft, warning_wav: e.target.value })}
            className={inputCls}
          />
        </Field>
        <Field label="Category (blank = boss name)">
          <input
            type="text"
            value={draft.category}
            onChange={e => onChange({ ...draft, category: e.target.value })}
            placeholder="a|b for split mobs"
            className={inputCls}
          />
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

      {/* EQ2Parser enrichment — rides /api/act/pack, never the ACT XML. */}
      <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
        <Field label="Damage type (toggle all that apply — first picked leads)">
          <div className="flex flex-wrap gap-1">
            {DAMAGE_SCHOOLS.map(school => {
              const selected = parseSchools(draft.damage_type).includes(school)
              return (
                <button
                  key={school}
                  type="button"
                  onClick={() => onChange({ ...draft, damage_type: toggleSchool(draft.damage_type, school) })}
                  className={
                    'appearance-none border rounded-pill px-2 py-0.5 text-[0.75rem] cursor-pointer transition-colors ' +
                    (selected
                      ? 'border-gold bg-gold/15 text-gold'
                      : 'border-border bg-transparent text-text-muted hover:border-gold-dim hover:text-text')
                  }
                >
                  {school}
                </button>
              )
            })}
          </div>
        </Field>
        <Field label="Control effect">
          <select
            value={draft.control_effect}
            onChange={e => onChange({ ...draft, control_effect: e.target.value })}
            className={inputCls}
          >
            <option value="">none</option>
            {CONTROL_EFFECTS.map(effect => (
              <option key={effect} value={effect}>{effect}</option>
            ))}
          </select>
        </Field>
      </div>

      {/* Full parity with EQ2Parser's timer options — every field the
          parser honours is expressible here, so a save can never silently
          strip behaviour again. */}
      <div className="flex items-center gap-4 flex-wrap text-[0.85rem]">
        <Checkbox
          label="Enabled"
          checked={draft.checked}
          onChange={v => onChange({ ...draft, checked: v })}
        />
        <Checkbox
          label="Timer mods"
          checked={draft.modable}
          onChange={v => onChange({ ...draft, modable: v })}
        />
        <Checkbox
          label="Category lock"
          checked={draft.restrict_category}
          onChange={v => onChange({ ...draft, restrict_category: v })}
        />
        <Checkbox
          label="Only mine"
          checked={draft.restrict}
          onChange={v => onChange({ ...draft, restrict: v })}
        />
        <Checkbox
          label="Radial"
          checked={draft.radial_display}
          onChange={v => onChange({ ...draft, radial_display: v })}
        />
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
