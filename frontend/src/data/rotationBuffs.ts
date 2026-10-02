// Rotation-simulator external buffs, curated per expansion (raidConsumables
// pattern). These are buffs OTHER raid members rotate onto the character
// (Jester's Cap from the troubs, etc.); the character's own temp buffs come
// through /rotation-data and open windows when cast.
//
// DATA HONESTY: live census effect text for these spells is era-drifted
// (Jester's Cap reads modern Fervor values), so the mod numbers below are
// curated estimates flagged `todoValues` — badged "unverified" in the UI
// until confirmed in-game. Duration/recast seed from spells.db/items.db
// where the era rank exists but are user-editable in the panel (census
// carries the modern recast for some, e.g. Jester's Cap's 30s).

import type { BuffMods } from '../pages/simulator/types'

export interface RotationBuffDef {
  id: string
  name: string
  sourceClass: string
  /** 'temporary' = rotated windows (providers × duration/recast);
   * 'permanent' = always-on while that class is in the group. */
  kind: 'temporary' | 'permanent'
  /** Timing — temporary buffs only (ignored for permanents). */
  duration_s: number
  recast_s: number
  mods: BuffMods
  /** Mod numbers are unverified curated estimates — badge them. */
  todoValues?: boolean
  note?: string
  /** Census base spell name — set when a named SUPPLIER character can be
   * attached: their owned rank of this spell (AA-adjusted timing, proc
   * payloads, their stats) then replaces the curated estimates. */
  censusBase?: string
  /** ON/OFF single-provider ally buff (Bolster): the panel shows a
   * checkbox instead of a provider count, and the Results card gets a
   * timing slider to move WHEN it first lands. */
  toggle?: boolean
  /** Mod keys that only land on PRIEST archetypes (Crusade's Fervor):
   * stripped from the resolved mods when the simmed character isn't a
   * priest, wherever the values came from (curated, tier row, supplier). */
  priestOnlyMods?: (keyof BuffMods)[]
}

export interface RotationBuffSheet {
  /** Matches servers.current_xpac (case-insensitive), e.g. "RoK". */
  xpac: string
  /** Other spellings the registry may use ("Rise of Kunark"). */
  aliases?: string[]
  label: string
  buffs: RotationBuffDef[]
}

/** Ordered oldest → newest; the last entry is the fallback sheet. */
export const ROTATION_BUFF_SHEETS: RotationBuffSheet[] = [
  {
    xpac: 'RoK',
    aliases: ['Rise of Kunark', 'Kunark'],
    label: 'Rise of Kunark (T8)',
    buffs: [
      {
        id: 'jesters-cap',
        name: "Jester's Cap",
        sourceClass: 'Troubador',
        kind: 'temporary',
        duration_s: 30,
        // User-confirmed in game: the era recast really is 30s.
        recast_s: 30,
        mods: { castSpeedPct: 30, reuseSpeedPct: 25 },
        todoValues: true,
        censusBase: "Jester's Cap",
        note: 'Cast/reuse speed on one ally. Census text is era-drifted — verify values in-game.',
      },
      {
        id: 'bolster',
        name: 'Bolster',
        sourceClass: 'Mystic',
        kind: 'temporary',
        toggle: true,
        duration_s: 36,
        recast_s: 120,
        mods: { dmgPct: 10 },
        todoValues: true,
        censusBase: 'Bolster',
        note: 'One mystic bolsters YOU (on/off) — move its timing with the slider in Results. Approximated as a damage % — verify in-game.',
      },
      // Mythical raid-wide procs: the epic-weapon versions of the bard
      // group buffs hit the WHOLE raid, so a bard in another group still
      // covers you (assumed mythed). If the bard is IN your group, use
      // Group make-up instead — it carries the real spell values.
      {
        id: 'cacophony-of-blades',
        name: 'Cacophony of Blades (raid-wide)',
        sourceClass: 'Dirge (Mythical)',
        kind: 'temporary',
        // Read in-game from an Expert dirge (2026-10-02): 20.6s up,
        // 49.6s recast (their reuse/AAs), Haste 61.2 — census matches
        // the Expert row EXACTLY, so these values are era-correct.
        duration_s: 20.6,
        recast_s: 49.6,
        mods: { hastePct: 61.2 },
        censusBase: 'Cacophony of Blades',
        note: 'Raid-wide via the Dirge Mythical. Values from an in-game Expert read; attach the supplying dirge for their rank, timing and the Blade Chime proc at their stats.',
      },
      {
        id: 'perfection-of-the-maestro',
        name: 'Perfection of the Maestro (raid-wide)',
        sourceClass: 'Troubador (Mythical)',
        kind: 'temporary',
        duration_s: 20,
        recast_s: 60,
        mods: { castSpeedPct: 0 },
        todoValues: true,
        censusBase: 'Perfection of the Maestro',
        note: 'Troubador Mythical makes PotM raid-wide. Attach the supplying troubador to use their real rank, AA-extended duration and the Precise Note proc at their stats.',
      },
      {
        id: 'confront-fear',
        name: 'Confront Fear',
        sourceClass: 'Dirge (AA)',
        kind: 'permanent',
        duration_s: 0,
        recast_s: 0,
        // Read in-game (2026-10-02): +3.3 Fervor on ONE ally, 98s up /
        // 37.2s recast — permanently maintainable. Census text is
        // era-drifted to 0.0, so no censusBase tier lookup.
        mods: { fervorPct: 3.3 },
        note: 'Single ally (dirge AA): +3.3 Fervor, maintainable full-time on one target.',
      },
      // RAID-WIDE permanents (census target_type='raid', full sweep of
      // all 24 classes at ≤80): the five whose effects help damage.
      // Values below are the Master-tier census numbers — attach the
      // supplying character for their actual rank. The rest of the raid
      // book (Call of Shielding, Noxious/Arcane Symphony, Elemental
      // Concerto, Porcupine, the summoner heart/shard summons) is
      // defensive/utility and deliberately not listed.
      {
        id: 'crusade',
        name: 'Crusade',
        sourceClass: 'Paladin',
        kind: 'permanent',
        duration_s: 0,
        recast_s: 0,
        mods: { wisFlat: 65.3, fervorPct: 1.2 },
        todoValues: true,
        censusBase: 'Crusade',
        priestOnlyMods: ['fervorPct'],
        note: 'Raid-wide WIS and Fervor. The Fervor applies to Priests only.',
      },
      {
        id: 'unholy-strength',
        name: 'Unholy Strength',
        sourceClass: 'Shadowknight',
        kind: 'permanent',
        duration_s: 0,
        recast_s: 0,
        mods: { baseDamagePct: 5 },
        todoValues: true,
        censusBase: 'Unholy Strength',
        note: 'Raid-wide spell damage %.',
      },
      {
        id: 'brutality',
        name: 'Brutality',
        sourceClass: 'Bruiser',
        kind: 'permanent',
        duration_s: 0,
        recast_s: 0,
        mods: { strFlat: 69, agiFlat: 69 },
        todoValues: true,
        censusBase: 'Brutality',
        note: 'Raid-wide STR and AGI.',
      },
      {
        id: 'calm-tranquility',
        name: 'Calm Tranquility',
        sourceClass: 'Monk',
        kind: 'permanent',
        duration_s: 0,
        recast_s: 0,
        mods: { hastePct: 23.8, castSpeedPct: 9.5 },
        todoValues: true,
        censusBase: 'Calm Tranquility',
        note: 'Raid-wide Haste and Casting Speed.',
      },
      {
        id: 'destructive-rage',
        name: 'Destructive Rage',
        sourceClass: 'Berserker',
        kind: 'permanent',
        duration_s: 0,
        recast_s: 0,
        mods: { weaponDamagePct: 3.3 },
        todoValues: true,
        censusBase: 'Destructive Rage',
        note: 'Raid-wide Weapon Damage Bonus — a % on auto-attack swings.',
      },
    ],
  },
]

/** The sheet matching the server's current xpac, else the newest curated
 * sheet (exact=false). Null only when nothing is curated at all. */
export function buffSheetForXpac(currentXpac: string | null): { sheet: RotationBuffSheet; exact: boolean } | null {
  if (ROTATION_BUFF_SHEETS.length === 0) return null
  const v = currentXpac?.trim().toLowerCase()
  const match = v
    ? ROTATION_BUFF_SHEETS.find(
        s => s.xpac.toLowerCase() === v || (s.aliases ?? []).some(a => a.toLowerCase() === v),
      )
    : undefined
  if (match) return { sheet: match, exact: true }
  return { sheet: ROTATION_BUFF_SHEETS[ROTATION_BUFF_SHEETS.length - 1], exact: false }
}
