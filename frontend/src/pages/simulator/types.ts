// Rotation simulator types — mirrors /api/character/{name}/rotation-data
// plus the engine's config/result shapes. Kept React-free so the engine
// and its tests import from here without pulling components in.

export interface DamageComponent {
  kind: 'hit' | 'dot'
  min_dmg: number
  max_dmg: number
  school: string
  target_scope: 'single' | 'encounter' | 'aoe'
  /** Raw condition text ("If target is undead") — null = unconditional.
   * Conditional damage only counts when the sim target toggles it on. */
  condition: string | null
  interval_s: number | null
  duration_s: number | null
  duration_estimated: boolean
  /** Census unscaled-tooltip disease: the damage text reads "1 - 2" on a
   * lv71 spell. Number carried as-is; the UI badges it as bad data. */
  suspect_low_value: boolean
  /** Generated from an "Applies X … every N seconds" pulse wrapper. */
  from_pulse?: boolean
  /** Lifeburn's per-HP mechanic: per application = per_hp_rate ×
   * hp_fraction × caster max health — FLAT, outside the coefficient
   * chain (min/max_dmg are 0 on such components). */
  per_hp_rate?: number | null
  /** Fraction of max health consumed per application ("roughly 25%" per
   * tick — user-observed estimate pending a log). */
  hp_fraction?: number | null
  /** Auto-scaled class-granted ranks (Wrath): the tooltip is the BARE
   * chain — no ability mod, no school flat, no ½-flat constant. */
  no_flat_mod?: boolean
}

/** Attack-driven proc damage ("On any combat or spell hit this spell has
 * a 50% chance to cast Bolt of Power…"). */
export interface Proc {
  trigger: 'any_hit' | 'melee_hit' | 'ability_cast' | 'spell_cast' | 'when_damaged' | 'target_cast' | string
  chance_pct: number
  name: string
  /** Rate-limited procs ("Triggers about 3.0 times per minute") — when
   * set, the rate replaces the trigger-event count. */
  per_minute: number | null
  /** "Grants a total of N triggers" — a per-application budget: the
   * payload fires exactly N times per cast of the carrying ability
   * (Slothful Spirit's Sloth's Habitat ×3). */
  trigger_count?: number | null
  components: DamageComponent[]
}

export interface RotationAbility {
  name: string
  base_name: string
  crc: number | null
  tier_name: string
  level: number
  spell_type: 'spells' | 'arts' | string
  beneficial: boolean
  cast_secs: number
  recast_secs: number
  recovery_secs: number
  target_type: string | null
  icon_id: number | null
  icon_backdrop: number | null
  duration_s: number | null
  power_cost: number | null
  components: DamageComponent[]
  procs: Proc[]
  effect_lines: string[]
  has_unparsed_damage: boolean
  source: 'spell' | 'aa' | string
  rank: number | null
  /** Self-target pulse with no duration ("Until Cancelled" — Exorcise):
   * kept up permanently; modeled as a continuous pulse stream in the
   * maintained section, never cast in rotation. */
  maintained?: boolean
  /** For buff-granted proc passives: the SUPPLIER's stat snapshot —
   * PotM's Precise Note hits with the bard's chain, not the player's.
   * Absent ⇒ the player's stats scale the proc (own AAs, own gear). */
  proc_stats?: Partial<SimStats>
  /** Stat mods parsed from the effect text — set on OWN castable temp
   * buffs: casting one opens a window with these effects. */
  mods?: Record<string, number>
  /** Per-ability multiplier from the character's own Enhance AAs
   * ("Increases damage by 5%.") — multiplies the BASE-CHAIN part only,
   * not the flat ability-mod/school-flat part (Soulrot VII + Lifeburn
   * cross-validated). */
  dmg_mod_pct?: number
  /** "Increases overtime damage by N%." — DOT components only. */
  dot_dmg_mod_pct?: number
  /** "Improves the Crit Bonus by N%." — per-ability crit bonus; dealt
   * damage only (tooltips exclude crit). */
  crit_bonus_pct?: number
  /** Pre-AA base timings (set only when an AA cut changed them): cast
   * and reuse NEVER drop below HALF the ORIGINAL base — a 5s cast with a
   * −1s AA and +100% cast speed still floors at 2.5s. */
  orig_cast_secs?: number | null
  orig_recast_secs?: number | null
  /** The character's own AA lines applied to this ability. */
  aa_adjustments?: string[]
}

/** One entry of a class's group/raid/ally buff book
 * (GET /api/simulator/class-buffs?cls=). */
export interface ClassBuff {
  name: string
  base_name: string
  tier_name: string
  level: number
  target_scope: 'group' | 'raid' | 'ally' | string
  icon_id: number | null
  icon_backdrop: number | null
  /** Modelable stat mods parsed from the effect text (BuffMods keys). */
  mods: Record<string, number>
  procs: Proc[]
  effect_lines: string[]
  /** Set ⇒ a temp buff the group member rotates (windows from
   * duration/recast); null/long ⇒ permanent while they're in group. */
  duration_s: number | null
  recast_s: number
  /** AA effect lines that adjusted this buff's reuse/duration (the
   * member's Enhance:/Focus: nodes) — already applied to the values. */
  aa_adjustments?: string[]
  /** AA-granted buffs only: spent rank + the node's max. tier_name is
   * blank on these — the UI shows "4/5" (nothing for 1-rank nodes). */
  rank?: number | null
  max_rank?: number | null
}

/** A specific group member's buff book at THEIR owned spell ranks
 * (GET /api/simulator/character-buffs?name=). */
export interface GroupMemberBook {
  character_name: string
  cls: string | null
  level: number | null
  buffs: ClassBuff[]
  /** The member's proc-scaling stat snapshot (SimStats-shaped keys) —
   * buff-granted proc damage scales off the SUPPLIER, not the player. */
  stats: Partial<SimStats>
}

export interface DerivedSource {
  kind: 'item' | 'set' | 'aa' | string
  name: string
  detail: string
}

/** A temp stat buff granted by a worn item's proc (Plasma Boost):
 * rate + duration drive deterministic windows; never always-on. */
export interface ItemProcBuff {
  name: string
  item: string
  duration_s: number
  per_minute: number
  chance_pct: number
  trigger: string
  mods: Record<string, number>
}

/** Hidden modifiers auto-derived from worn gear + adorns + active set
 * bonuses + class-tree AAs — none appear in census sheet stats. */
export interface DerivedModifiers {
  base_damage_bonus_pct: number
  cast_speed_bonus_pct: number
  reuse_bonus_pct: number
  /** SCOPED worn-item timing cuts ("Reduces reuse time of hostile
   * spells by 1 percent"): applied per ability by its beneficial flag —
   * a hostile-only cut never speeds a beneficial cast. */
  hostile_cast_pct: number
  hostile_reuse_pct: number
  beneficial_cast_pct: number
  beneficial_reuse_pct: number
  /** School-specific flat damage from gear, keyed by lowercased school
   * (Spooky Bone Hoop → { disease: 30 }). Applied like ability mod but
   * only to matching-school components. */
  school_damage_flat: Record<string, number>
  sources: DerivedSource[]
  proc_buffs: ItemProcBuff[]
}

export interface CharacterRotationData {
  character_name: string
  cls: string | null
  level: number | null
  abilities: RotationAbility[]
  /** Always-on proc passives (AA innates + active set-bonus procs). */
  passives: RotationAbility[]
  derived: DerivedModifiers
}

/** The subset of CharacterStats the engine reads (all optional — a stat
 * the sheet lacks contributes zero). */
export interface SimStats {
  /** The class's primary attribute value (WIS priest / INT mage / AGI
   * scout / STR fighter), resolved by the caller — feeds the
   * level-capped primary-stat damage bonus. */
  primary_stat?: number | null
  /** WHICH attribute primary_stat is — lets buff windows apply only the
   * pertinent flat attribute add (AGI on a templar does nothing). */
  primary_attr?: 'str' | 'agi' | 'wis' | 'int'
  potency?: number | null
  fervor?: number | null
  /** Gear/AA "increases base damage" percentages (e.g. choker +25%,
   * Pact of the Faithful +10%) — additive with the primary-stat bonus.
   * Hidden from census sheets; user-entered. */
  base_damage_bonus_pct?: number | null
  /** School-specific FLAT damage from gear ("Increases disease damage
   * done by spells by up to 30." — Spooky Bone Hoop), keyed by
   * lowercased school. Behaves like ability mod but ONLY on components
   * whose school matches — worthless without spells of that school. */
  school_damage_flat?: Record<string, number> | null
  /** Caster max health — drives Lifeburn's per-HP components. */
  max_health?: number | null
  /** Scoped worn-item timing cuts — added to casting/reuse speed only
   * for abilities of the matching polarity (see DerivedModifiers). */
  hostile_cast_pct?: number | null
  hostile_reuse_pct?: number | null
  beneficial_cast_pct?: number | null
  beneficial_reuse_pct?: number | null
  crit_chance?: number | null
  crit_bonus?: number | null
  ability_mod?: number | null
  ability_doublecast?: number | null
  casting_speed?: number | null
  reuse_speed?: number | null
  recovery_speed?: number | null
  dps?: number | null
  attack_speed?: number | null
  double_attack?: number | null
  flurry?: number | null
  primary_min?: number | null
  primary_max?: number | null
  primary_delay?: number | null
  secondary_min?: number | null
  secondary_max?: number | null
  secondary_delay?: number | null
}

/** Stat deltas a buff applies while its window is active. All additive
 * onto the sheet stats except dmgPct, which multiplies ability damage. */
export interface BuffMods {
  castSpeedPct?: number
  reuseSpeedPct?: number
  recoverySpeedPct?: number
  dmgPct?: number
  critChancePct?: number
  critBonusPct?: number
  hastePct?: number
  dpsModPct?: number
  doubleAttackPct?: number
  /** Ally/group doublecast grants (Conjuror's Unabate). */
  doublecastPct?: number
  abilityModFlat?: number
  potencyPct?: number
  fervorPct?: number
  /** Weapon Damage Bonus (Berserker's raid-wide Destructive Rage) —
   * a PERCENT like base damage but for auto-attack swings only. */
  weaponDamagePct?: number
  /** Gear-proc temp bonus: "+N% base damage" while the window is up
   * (Plasma Boost) — additive into base_damage_bonus_pct. */
  baseDamagePct?: number
  /** Flat attribute adds — only the character's PRIMARY attribute does
   * anything (applyMods maps "<primary_attr>Flat" onto primary_stat). */
  strFlat?: number
  agiFlat?: number
  wisFlat?: number
  intFlat?: number
}

/** One active buff window on the timeline. Windows with the same buffId
 * never stack — overlap merges (two troubs' Jester's Cap = one cap). */
export interface BuffWindow {
  buffId: string
  start: number
  end: number
  mods: BuffMods
}

/** UI config: an external buff with how many providers rotate it. */
export interface ExternalBuffConfig {
  buffId: string
  providers: number
  /** Optional user overrides of the data-file timing (census-drifted). */
  duration_s?: number
  recast_s?: number
  /** The CHARACTER supplying this buff (raid-wide myth buffs come from
   * bards OUTSIDE the group) — their real rank/AA timing/procs/stats
   * replace the sheet's curated estimates. */
  supplier?: string
}

/** A raid-buff supplier resolution: the named character's owned rank of
 * the sheet buff (when found), plus their proc-scaling stats. */
export interface SuppliedBuffInfo {
  supplier: string
  status: 'loading' | 'failed' | 'no-spell' | 'ok'
  entry?: ClassBuff
  stats?: Partial<SimStats>
}

/** What the rotation is hitting. */
export interface SimTarget {
  /** Total mobs stacked on the target's position (≥ 1). */
  count: number
  /** True when the extra mobs are one linked encounter — 'encounter'-scope
   * components then hit all of them; otherwise they hit only the target. */
  encounter: boolean
  /** Condition strings toggled ON (e.g. "If target is undead"). Conditional
   * components whose condition is not listed here deal no damage. */
  activeConditions: string[]
}

export interface SimConfig {
  /** Priority-ordered ability names (base_name keys into `abilities`). */
  rotation: string[]
  abilities: Record<string, RotationAbility>
  stats: SimStats
  fightDurationS: number
  autoAttack: boolean
  /** Results-row label for the auto-attack stream ("Auto-attack (melee)"). */
  autoAttackLabel?: string
  /** Defaults to a lone unconditional boss dummy. */
  target?: SimTarget
  /** base_names whose DoT is HELD: the ability is not re-cast (even when
   * its recast is up) until the live application's final tick has landed,
   * so re-casting never clips ticks. Off = re-cast freely by priority. */
  dotRefreshHold?: string[]
  /** Precomputed external buff windows (see buffs.buildExternalWindows). */
  buffWindows?: BuffWindow[]
  /** Enabled always-on proc passives (their procs become damage streams).
   * Maintained toggles (Exorcise) ride along here as pulse streams. */
  passives?: RotationAbility[]
  /** Per-passive proc-chance corrections (keyed by passive NAME) — census
   * effect text can be stale vs live (Bolt of Power reads 50% at rank 10
   * but fires on every attack in the log). */
  procChanceOverrides?: Record<string, number>
  /** Hits per minute landing on whatever carries the character's
   * damage-shield buffs (Divine Light on the tank) — drives
   * 'when_damaged' proc streams. 0/absent = those procs contribute
   * nothing. */
  incomingHitsPerMinute?: number
  /** Curated mods for the character's OWN temp buffs, keyed by base_name.
   * Casting a beneficial ability opens a window at cast end for the
   * ability's duration; without an entry here the window has no mods
   * (visible on the timeline, zero effect). */
  selfBuffMods?: Record<string, BuffMods>
  /** Per-base_name damage calibration multipliers (default 1). */
  calibration?: Record<string, number>
}

export interface TimelineSegment {
  t: number
  dur: number
  ability: string // base_name; '' for idle
  /** Buff ids active at the cast start (cast segments only). */
  buffs?: string[]
}

export interface AbilityBreakdown {
  ability: string
  casts: number
  damage: number
  pct: number
  avgPerCast: number
  clippedDotTicks: number
  /** Display name when the row isn't keyed by a rotation ability
   * (passive proc streams). */
  label?: string
  /** True for passive proc rows: `casts` is the expected proc count. */
  isProc?: boolean
}

export interface SimResult {
  totalDamage: number
  dps: number
  perAbility: AbilityBreakdown[]
  autoAttackDamage: number
  timeline: TimelineSegment[]
  idlePct: number
  /** buffId → % of the fight the buff was up (externals + self temps). */
  buffUptimes: Record<string, number>
  /** Every buff window that existed during the sim (externals + self
   * temps cast in rotation) — the timeline shades where they're active. */
  buffWindows: BuffWindow[]
}
