// ── Types (mirror the route's Pydantic models) ────────────────────────────────

export interface Trigger {
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
  /** EQ2Parser enrichment — never in ACT XML. */
  cooldown_seconds: number
  last_edited_at: number | null
  last_edited_by: string | null
  created_at: number
}

export interface SpellTimer {
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
  /** EQ2Parser enrichment — never in ACT XML. Comma-joined dominant-first
   * log schools ("poison, disease") / tooltip-vocabulary control effect. */
  damage_type: string
  control_effect: string
  last_edited_at: number | null
  last_edited_by: string | null
  created_at: number
}

export interface SpellTimerDraft {
  name: string
  timer_duration_s: number
  warning_value: number
  remove_value: number
  fill_color_hex: string
  fill_color_packed: number
  checked: boolean
  panel1: boolean
  panel2: boolean
  absolute: boolean
  only_master_ticks: boolean
  restrict: boolean
  restrict_category: boolean
  radial_display: boolean
  modable: boolean
  start_wav: string
  warning_wav: string
  category: string
  tooltip: string
  damage_type: string
  control_effect: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * ACT stores FillColor as a .NET ARGB packed signed int. Convert to a CSS
 * `#rrggbb` (alpha dropped for the swatch — the contributor cares about hue,
 * not the rarely-used opacity).
 */
export function argbToHex(packed: number): string {
  // Convert signed-int → unsigned 32-bit, then take the bottom 24 bits.
  const unsigned = packed >>> 0
  const rgb = unsigned & 0xffffff
  return '#' + rgb.toString(16).padStart(6, '0')
}

export function hexToArgb(hex: string, existing: number): number {
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

export function defaultSpellTimerDraft(s?: SpellTimer | null, nameHint?: string): SpellTimerDraft {
  const packed = s?.fill_color ?? -16776961
  return {
    name: s?.name ?? nameHint ?? '',
    timer_duration_s: s?.timer_duration_s ?? 30,
    warning_value: s?.warning_value ?? 10,
    remove_value: s?.remove_value ?? -15,
    fill_color_hex: argbToHex(packed),
    fill_color_packed: packed,
    // New-timer defaults mirror EQ2Parser/ACT: enabled, modable, tts
    // sounds — the editor now expresses EVERY field, so what's saved is
    // always what the curator saw.
    checked: s?.checked ?? true,
    panel1: s?.panel1 ?? true,
    panel2: s?.panel2 ?? false,
    absolute: s?.absolute ?? false,
    only_master_ticks: s?.only_master_ticks ?? false,
    restrict: s?.restrict ?? false,
    restrict_category: s?.restrict_category ?? false,
    radial_display: s?.radial_display ?? false,
    modable: s?.modable ?? true,
    start_wav: s?.start_wav ?? 'tts',
    warning_wav: s?.warning_wav ?? 'tts',
    category: s?.category ?? '',
    tooltip: s?.tooltip ?? '',
    damage_type: s?.damage_type ?? '',
    control_effect: s?.control_effect ?? '',
  }
}

export function buildTimerBody(d: SpellTimerDraft) {
  return {
    name: d.name.trim(),
    timer_duration_s: d.timer_duration_s,
    warning_value: d.warning_value,
    remove_value: d.remove_value,
    fill_color: d.fill_color_packed,
    checked: d.checked,
    panel1: d.panel1,
    panel2: d.panel2,
    absolute: d.absolute,
    only_master_ticks: d.only_master_ticks,
    restrict: d.restrict,
    restrict_category: d.restrict_category,
    radial_display: d.radial_display,
    modable: d.modable,
    start_wav: d.start_wav,
    warning_wav: d.warning_wav,
    category: d.category.trim() || null,
    tooltip: d.tooltip,
    damage_type: d.damage_type,
    control_effect: d.control_effect,
  }
}
