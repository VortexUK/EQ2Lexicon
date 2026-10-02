// Per-character simulator state in localStorage — a per-viewer
// convenience only (rotation, buffs, calibration inputs survive a
// reload). Every access is try/caught: private windows and blocked site
// data must degrade to a fresh page, never an error.

import type { ExternalBuffConfig, SimTarget } from './types'
import type { ObservedHits } from './calibration'

const VERSION = 1
const keyFor = (characterName: string) => `eq2lexicon.simulator.v${VERSION}.${characterName.toLowerCase()}`

export interface SavedSimState {
  v: number
  rotation: string[]
  dotHold: string[]
  externalBuffs: ExternalBuffConfig[]
  /** Enabled permanent group buffs (buff ids). */
  permanentBuffs: string[]
  /** Assumed spell tier per raid-wide permanent (buff id → tier name,
   * e.g. 'Expert'). Absent = Expert. */
  permanentTiers?: Record<string, string>
  /** Disabled AA proc passives (base_names) — passives default ON. */
  disabledPassives: string[]
  /** Proc-chance corrections keyed by passive NAME (census text can be
   * stale vs live — Bolt of Power). Absent key = trust census. */
  procChanceOverrides?: Record<string, number>
  /** Group make-up: member CHARACTER names + ticked buff base_names. */
  groupMembers?: string[]
  /** Legacy (pre-character members): generic member classes — ignored. */
  groupClasses?: string[]
  groupBuffs: string[]
  /** User CORRECTIONS to the auto-derived hidden bonuses; null = trust
   * the derived value. */
  overrides?: { base: number | null; cast: number | null; reuse: number | null }
  /** Legacy manual fields (pre-derivation saves) — read as overrides. */
  baseDamageBonusPct?: number
  hiddenCastSpeedPct?: number
  observed: ObservedHits
  fightDuration: number
  /** Hits/min on damage-shield buff targets ('when_damaged' procs). */
  incomingHitsPerMinute?: number
  autoAttack: boolean
  /** Which weapon auto-attacks (legacy saves carry only the boolean). */
  autoAttackMode?: 'melee' | 'ranged' | 'off'
  target: SimTarget
}

export function loadSimState(characterName: string): SavedSimState | null {
  try {
    const raw = localStorage.getItem(keyFor(characterName))
    if (!raw) return null
    const parsed = JSON.parse(raw) as SavedSimState
    if (parsed?.v !== VERSION || !Array.isArray(parsed.rotation)) return null
    return parsed
  } catch {
    return null
  }
}

export function saveSimState(characterName: string, state: Omit<SavedSimState, 'v'>): void {
  try {
    localStorage.setItem(keyFor(characterName), JSON.stringify({ v: VERSION, ...state }))
  } catch {
    /* storage unavailable — the page works without it */
  }
}
