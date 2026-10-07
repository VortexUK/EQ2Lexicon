// Ability Rotation Simulator — pick a character, build a priority-ordered
// rotation from their real abilities (the spells catalogue timings + parsed damage),
// and simulate a boss-dummy fight with their sheet stats. v1 scope:
// single target, AA/ascension abilities excluded, power ignored,
// proc/conditional damage badged rather than modeled, auto-attack as a
// uniform stream.

import { useEffect, useMemo, useState } from 'react'

import { Card, SectionLabel } from '../components/ui'
import { buffSheetForXpac } from '../data/rotationBuffs'
import { fmtNum } from '../formatters'
import { useFetch } from '../hooks/useFetch'
import { useServer } from '../hooks/useServer'
import { useClasses } from '../useClasses'
import type { Character } from './characterSheet'
import { applyMods, buildExternalWindows, buffUptimes } from './simulator/buffs'
import type { ExternalBuffDef } from './simulator/buffs'
import BuffsPanel from './simulator/BuffsPanel'
import { computeCalibration } from './simulator/calibration'
import type { ObservedHits } from './simulator/calibration'
import CalibrationPanel from './simulator/CalibrationPanel'
import DerivedPanel from './simulator/DerivedPanel'
import { simulate } from './simulator/engine'
import { autoAttackDps, autoSwingRate, PERK_BENEFICIAL_DURATION_MULT } from './simulator/formulas'
import GroupMakeupPanel, { isTempBuff } from './simulator/GroupMakeupPanel'
import PassivesPanel from './simulator/PassivesPanel'
import { loadSimState, saveSimState } from './simulator/persistence'
import ResultsPanel from './simulator/ResultsPanel'
import RotationBuilder from './simulator/RotationBuilder'
import SimCharacterPicker from './simulator/SimCharacterPicker'
import SuggestOrder from './simulator/SuggestOrder'
import type {
  BuffMods,
  BuffWindow,
  CharacterRotationData,
  ClassBuff,
  ExternalBuffConfig,
  GroupMemberBook,
  RotationAbility,
  SimConfig,
  SimResult,
  SimStats,
  SimTarget,
  SuppliedBuffInfo,
} from './simulator/types'

const MIN_FIGHT_S = 10
const MAX_FIGHT_S = 900
const DEFAULT_FIGHT_S = 180

const NUM_INPUT_CLASS =
  'py-1.5 px-2.5 rounded-sm2 border border-border bg-surface-raised text-text text-[0.9rem] w-24 [color-scheme:dark]'

export default function SimulatorPage() {
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [rotation, setRotation] = useState<string[]>([])
  const [dotHold, setDotHold] = useState<string[]>([])
  /** Temp-buff timing sliders: earliest FIRST cast per ability. */
  const [firstCastAt, setFirstCastAt] = useState<Record<string, number>>({})
  const [fightDuration, setFightDuration] = useState(DEFAULT_FIGHT_S)
  const [autoAttackMode, setAutoAttackMode] = useState<'melee' | 'ranged' | 'off'>('melee')
  const [autoAttackTouched, setAutoAttackTouched] = useState(false)
  const [targetCount, setTargetCount] = useState(1)
  const [encounter, setEncounter] = useState(true)
  /** Hits/min on whatever carries the damage-shield buffs (Divine Light
   * on the tank) — drives 'when_damaged' proc streams. 0 = off. */
  const [incomingHitsPerMinute, setIncomingHitsPerMinute] = useState(0)
  /** EQ2 membership Perks: +20% beneficial spell duration on every
   * temp-buff window. Default ON — most raiders run with perks. */
  const [perksOn, setPerksOn] = useState(true)
  const [activeConditions, setActiveConditions] = useState<string[]>([])
  const [externalBuffs, setExternalBuffs] = useState<ExternalBuffConfig[]>([])
  const [permanentBuffs, setPermanentBuffs] = useState<string[]>([])
  /** Assumed spell tier per raid-wide permanent (default Expert). */
  const [permanentTiers, setPermanentTiers] = useState<Record<string, string>>({})
  /** buffId → that line's era tier rows (parsed mods per tier). */
  const [permTierData, setPermTierData] = useState<Record<string, ClassBuff[]>>({})
  const [disabledPassives, setDisabledPassives] = useState<string[]>([])
  /** Proc-chance corrections keyed by passive NAME — census effect text
   * can be stale vs live (Bolt of Power reads 50% at rank 10 but fires
   * on every attack in the log). */
  const [procChanceOverrides, setProcChanceOverrides] = useState<Record<string, number>>({})
  /** Group member CHARACTER names — each one's buff book is fetched at
   * the ranks that character actually owns. */
  const [groupMembers, setGroupMembers] = useState<string[]>([])
  const [groupBuffs, setGroupBuffs] = useState<string[]>([])
  /** Timing offsets for ticked group-member TEMP buffs (base_name). */
  const [groupBuffStartAt, setGroupBuffStartAt] = useState<Record<string, number>>({})
  /** name → their buff book; undefined = loading, null = lookup failed. */
  const [memberBooks, setMemberBooks] = useState<Record<string, GroupMemberBook | null | undefined>>({})
  const [observed, setObserved] = useState<ObservedHits>({})
  /** The character whose saved state is applied — gates persistence so a
   * character switch never saves the old character's state under the new key. */
  const [loadedFor, setLoadedFor] = useState<string | null>(null)
  const { classes } = useClasses()

  const enc = selectedName ? encodeURIComponent(selectedName) : null
  const char = useFetch<Character>(enc ? `/api/character/${enc}` : null)
  const rot = useFetch<CharacterRotationData>(enc ? `/api/character/${enc}/rotation-data` : null)

  // Stale-guard: only trust payloads that match the current selection.
  const charData = char.data && selectedName && char.data.name.toLowerCase() === selectedName.toLowerCase() ? char.data : null
  const rotData = rot.data && selectedName && rot.data.character_name.toLowerCase() === selectedName.toLowerCase() ? rot.data : null

  const perkMult = perksOn ? PERK_BENEFICIAL_DURATION_MULT : 1

  const abilities = useMemo(() => {
    const map: Record<string, RotationAbility> = {}
    for (const a of rotData?.abilities ?? []) {
      if (a.maintained) continue
      // Perks stretch BENEFICIAL durations (own temp-buff windows);
      // hostile durations (dot windows) are untouched.
      map[a.base_name] = a.beneficial && a.duration_s ? { ...a, duration_s: a.duration_s * perkMult } : a
    }
    return map
  }, [rotData, perkMult])

  // Maintained toggles ("Until Cancelled" self pulses — Exorcise): not
  // cast in rotation; they join the passives section as pulse streams.
  const maintainedAbilities = useMemo(
    () => (rotData?.abilities ?? []).filter(a => a.maintained),
    [rotData],
  )
  const allPassives = useMemo(
    () => [...maintainedAbilities, ...(rotData?.passives ?? [])],
    [maintainedAbilities, rotData],
  )

  // Every condition string appearing on the loaded abilities — the target
  // panel offers these as toggles (data-driven; nothing hardcoded).
  const availableConditions = useMemo(() => {
    const seen = new Set<string>()
    for (const a of rotData?.abilities ?? []) {
      for (const c of a.components) if (c.condition) seen.add(c.condition)
    }
    return [...seen].sort()
  }, [rotData])

  // New character → restore their saved simulator state, else start fresh.
  useEffect(() => {
    const saved = selectedName ? loadSimState(selectedName) : null
    if (saved) {
      setRotation(saved.rotation)
      setDotHold(saved.dotHold ?? [])
      setFirstCastAt(saved.firstCastAt ?? {})
      setExternalBuffs(saved.externalBuffs ?? [])
      setPermanentBuffs(saved.permanentBuffs ?? [])
      setPermanentTiers(saved.permanentTiers ?? {})
      setDisabledPassives(saved.disabledPassives ?? [])
      setProcChanceOverrides(saved.procChanceOverrides ?? {})
      setGroupMembers(saved.groupMembers ?? [])
      setGroupBuffs(saved.groupBuffs ?? [])
      setGroupBuffStartAt(saved.groupBuffStartAt ?? {})
      setObserved(saved.observed ?? {})
      setFightDuration(saved.fightDuration ?? DEFAULT_FIGHT_S)
      setIncomingHitsPerMinute(saved.incomingHitsPerMinute ?? 0)
      setPerksOn(saved.perks ?? true)
      if (saved.autoAttackMode) {
        setAutoAttackMode(saved.autoAttackMode)
        setAutoAttackTouched(true) // the saved choice wins over the class default
      } else if (saved.autoAttack === false) {
        setAutoAttackMode('off')
        setAutoAttackTouched(true)
      } else {
        setAutoAttackTouched(false) // legacy true → re-derive the class default
      }
      setTargetCount(saved.target?.count ?? 1)
      setEncounter(saved.target?.encounter ?? true)
      setActiveConditions(saved.target?.activeConditions ?? [])
    } else {
      setRotation([])
      setDotHold([])
      setFirstCastAt({})
      setExternalBuffs([])
      setPermanentBuffs([])
      setPermanentTiers({})
      setDisabledPassives([])
      setProcChanceOverrides({})
      setGroupMembers([])
      setGroupBuffs([])
      setGroupBuffStartAt({})
      setObserved({})
      setFightDuration(DEFAULT_FIGHT_S)
      setIncomingHitsPerMinute(0)
      setPerksOn(true)
      setAutoAttackTouched(false)
      setActiveConditions([])
      setTargetCount(1)
    }
    setLoadedFor(selectedName)
  }, [selectedName])

  // DoT abilities default to "hold until last tick" when added (the
  // tick-maximising choice); removal drops the hold flag too.
  const handleRotationChange = (next: string[]) => {
    setDotHold(prev => {
      const kept = prev.filter(n => next.includes(n))
      const added = next.filter(
        n => !rotation.includes(n) && abilities[n]?.components.some(c => c.kind === 'dot'),
      )
      return [...kept, ...added.filter(n => !kept.includes(n))]
    })
    setRotation(next)
  }

  // Default auto-attack source by archetype: casters/priests wand
  // (ranged slot) when they have one, melee otherwise — the user's
  // explicit choice always wins.
  useEffect(() => {
    if (!charData || autoAttackTouched) return
    const archetype = (classes.find(c => c.name === charData.cls)?.archetype ?? '').toLowerCase()
    const caster = archetype.startsWith('priest') || archetype.startsWith('mage')
    const hasRanged = charData.stats.ranged_min != null && charData.stats.ranged_delay != null
    setAutoAttackMode(caster && hasRanged ? 'ranged' : 'melee')
  }, [charData, autoAttackTouched, classes])

  const target: SimTarget = useMemo(
    () => ({ count: targetCount, encounter, activeConditions }),
    [targetCount, encounter, activeConditions],
  )

  // Persist per character on every change (cheap; localStorage is a
  // best-effort per-viewer convenience). Gated on loadedFor so a
  // character switch never writes stale state under the new key.
  useEffect(() => {
    if (!selectedName || loadedFor !== selectedName) return
    saveSimState(selectedName, {
      rotation,
      dotHold,
      firstCastAt,
      externalBuffs,
      permanentBuffs,
      permanentTiers,
      disabledPassives,
      procChanceOverrides,
      groupMembers,
      groupBuffs,
      groupBuffStartAt,
      observed,
      fightDuration,
      incomingHitsPerMinute,
      perks: perksOn,
      autoAttack: autoAttackMode !== 'off',
      autoAttackMode,
      target,
    })
  }, [selectedName, loadedFor, rotation, dotHold, firstCastAt, externalBuffs, permanentBuffs, permanentTiers, disabledPassives, procChanceOverrides, groupMembers, groupBuffs, groupBuffStartAt, observed, fightDuration, incomingHitsPerMinute, perksOn, autoAttackMode, target])

  // Fetch each needed character's buff book once (kept across simmed
  // characters — the book belongs to that character, not the sim
  // target): group members plus any raid-buff SUPPLIERS (the bards
  // outside the group who rotate PotM/CoB onto the raid).
  const neededBooks = useMemo(() => {
    const names = new Set(groupMembers)
    for (const c of externalBuffs) if (c.supplier) names.add(c.supplier)
    return [...names]
  }, [groupMembers, externalBuffs])
  useEffect(() => {
    for (const name of neededBooks) {
      if (memberBooks[name] !== undefined) continue
      setMemberBooks(prev => (prev[name] !== undefined ? prev : { ...prev, [name]: undefined }))
      fetch(`/api/simulator/character-buffs?name=${encodeURIComponent(name)}`, { credentials: 'include' })
        .then(r => (r.ok ? r.json() : null))
        .then((data: GroupMemberBook | null) => setMemberBooks(prev => ({ ...prev, [name]: data })))
        .catch(() => setMemberBooks(prev => ({ ...prev, [name]: null })))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- memberBooks guarded inside
  }, [neededBooks])

  const server = useServer()
  const buffSheet = useMemo(() => buffSheetForXpac(server?.currentXpac ?? null), [server?.currentXpac])

  // Buffs the Raid buffs panel owns (CoB, PotM, Jester's Cap…): hidden
  // from the member books and ignored even if a stale save ticked them —
  // configuring them twice would double-count.
  const raidSheetBaseNames = useMemo(
    () => (buffSheet?.sheet.buffs ?? []).map(b => b.censusBase).filter((x): x is string => !!x),
    [buffSheet],
  )


  // The ticked group buffs, resolved against the loaded books. Ticks are
  // keyed "member::base_name", so WHICH member supplies a buff is
  // explicit — PotM's proc damage scales off the supplying bard's stats.
  // Same buff ticked under two members: the first member wins (buffs
  // don't stack). Removing a member silently drops their ticked buffs.
  const enabledGroupBuffObjs = useMemo(() => {
    const out: { buff: ClassBuff; member: string }[] = []
    const seen = new Set<string>()
    for (const name of groupMembers) {
      for (const b of memberBooks[name]?.buffs ?? []) {
        if (raidSheetBaseNames.includes(b.base_name)) continue
        if (groupBuffs.includes(`${name}::${b.base_name}`) && !seen.has(b.base_name)) {
          seen.add(b.base_name)
          out.push({ buff: b, member: name })
        }
      }
    }
    return out
  }, [groupMembers, memberBooks, groupBuffs, raidSheetBaseNames])

  // Mods from ALWAYS-ON ticked buffs (permanent sheet buffs + non-temp
  // group-member buffs) — shown as "+x" deltas on the Adjusted-stats and
  // Auto-attack cards. Temps act as timed windows in the sim and are
  // deliberately excluded here (a flat +x would overstate them).
  // Era tier rows for each raid-wide permanent (Adept/Expert/Master…):
  // fetched once per sheet; the dropdown's selected tier drives the mods.
  useEffect(() => {
    const perms = (buffSheet?.sheet.buffs ?? []).filter(b => b.kind === 'permanent' && b.censusBase)
    if (perms.length === 0) return
    let cancelled = false
    for (const b of perms) {
      if (permTierData[b.id]) continue
      fetch(`/api/simulator/buff-tiers?name=${encodeURIComponent(b.censusBase as string)}`, { credentials: 'include' })
        .then(res => (res.ok ? (res.json() as Promise<ClassBuff[]>) : []))
        .then(rows => {
          if (!cancelled && Array.isArray(rows)) setPermTierData(prev => ({ ...prev, [b.id]: rows }))
        })
        .catch(() => {})
    }
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- permTierData is a fill-once cache
  }, [buffSheet])

  // Some buff mods are archetype-scoped: Crusade's Fervor lands on
  // PRIESTS only — a scout ticking it gets the WIS line's nothing-burger
  // AND no fervor, so the keys are stripped rather than silently applied.
  const isPriest = useMemo(() => {
    const archetype = (classes.find(c => c.name === charData?.cls)?.archetype ?? '').toLowerCase()
    return archetype.startsWith('priest')
  }, [classes, charData?.cls])

  /** A permanent's mods at the SELECTED tier (default Expert): the tier
   * row's parsed values when available, else the curated estimates —
   * with priest-only keys stripped for non-priest characters. */
  const permanentMods = (b: { id: string; mods: BuffMods; priestOnlyMods?: (keyof BuffMods)[] }): BuffMods => {
    const rows = permTierData[b.id]
    const want = permanentTiers[b.id] ?? 'Expert'
    const row = rows && rows.length > 0 ? (rows.find(r => r.tier_name === want) ?? rows[rows.length - 1]) : undefined
    let mods: BuffMods = row && Object.keys(row.mods).length > 0 ? (row.mods as BuffMods) : b.mods
    if (!isPriest && b.priestOnlyMods?.length) {
      mods = { ...mods }
      for (const k of b.priestOnlyMods) delete mods[k]
    }
    return mods
  }

  const permTierOptions = useMemo(() => {
    const out: Record<string, string[]> = {}
    for (const [id, rows] of Object.entries(permTierData)) out[id] = rows.map(r => r.tier_name)
    return out
  }, [permTierData])

  const alwaysOnMods: BuffMods = useMemo(() => {
    const sum: Record<string, number> = {}
    const add = (m: Record<string, number | undefined>) => {
      for (const [k, v] of Object.entries(m)) if (v) sum[k] = (sum[k] ?? 0) + v
    }
    for (const b of buffSheet?.sheet.buffs ?? []) {
      if (b.kind === 'permanent' && permanentBuffs.includes(b.id)) add(permanentMods(b) as Record<string, number | undefined>)
    }
    for (const { buff } of enabledGroupBuffObjs) {
      if (!isTempBuff(buff)) add(buff.mods)
    }
    return sum as BuffMods
    // eslint-disable-next-line react-hooks/exhaustive-deps -- permanentMods reads permTierData/permanentTiers
  }, [buffSheet, permanentBuffs, enabledGroupBuffObjs, permTierData, permanentTiers])

  // Supplier resolution: a sheet buff with an attached supplier CHARACTER
  // takes its timing/mods/procs from that character's owned rank (their
  // book entry, AA-adjusted) and its proc damage from their stats. The
  // "supplied" map feeds the defs, the proc passives, and the panel.
  const suppliedExternal = useMemo(() => {
    const out: Record<string, SuppliedBuffInfo> = {}
    for (const cfg of externalBuffs) {
      if (!cfg.supplier) continue
      const def = buffSheet?.sheet.buffs.find(b => b.id === cfg.buffId)
      if (!def?.censusBase) continue
      const book = memberBooks[cfg.supplier]
      const entry = book ? book.buffs.find(x => x.base_name === def.censusBase) : undefined
      out[cfg.buffId] = {
        supplier: cfg.supplier,
        status: book === undefined ? 'loading' : book === null ? 'failed' : entry ? 'ok' : 'no-spell',
        entry,
        stats: book ? book.stats : undefined,
      }
    }
    return out
  }, [externalBuffs, buffSheet, memberBooks])

  const buffDefs = useMemo(() => {
    // Perks apply to the default (census/curated) durations; a manually
    // entered per-config duration is taken literally.
    const perked = (d: number) => d * perkMult
    const out: Record<string, ExternalBuffDef> = {}
    for (const b of buffSheet?.sheet.buffs ?? []) {
      const sup = suppliedExternal[b.id]
      out[b.id] =
        sup?.status === 'ok' && sup.entry
          ? {
              duration_s: perked(sup.entry.duration_s ?? b.duration_s),
              recast_s: sup.entry.recast_s || b.recast_s,
              // The supplier's parsed mods when any; the curated estimates
              // otherwise (census text can be era-drifted to zeros).
              mods: Object.keys(sup.entry.mods).length > 0 ? sup.entry.mods : b.mods,
            }
          : { duration_s: perked(b.duration_s), recast_s: b.recast_s, mods: b.mods }
    }
    return out
  }, [buffSheet, suppliedExternal, perkMult])

  const buffWindows = useMemo(() => {
    const out: BuffWindow[] = buildExternalWindows(externalBuffs, buffDefs, fightDuration)
    // Enabled permanent raid-sheet buffs cover the whole fight.
    for (const b of (buffSheet?.sheet.buffs ?? []).filter(x => x.kind === 'permanent' && permanentBuffs.includes(x.id))) {
      out.push({ buffId: b.id, start: 0, end: fightDuration, mods: permanentMods(b) })
    }
    // Ticked group-make-up buffs: permanents always-on; temps rotate on
    // their real duration/recast (one provider — the group member).
    for (const { buff: b } of enabledGroupBuffObjs) {
      if (!isTempBuff(b)) {
        if (Object.keys(b.mods).length > 0) {
          out.push({ buffId: b.base_name, start: 0, end: fightDuration, mods: b.mods })
        }
        continue
      }
      const dur = (b.duration_s as number) * perkMult
      const recast = Math.max(b.recast_s, dur)
      const offset = Math.max(0, groupBuffStartAt[b.base_name] ?? 0)
      for (let s = offset; s < fightDuration; s += recast) {
        out.push({ buffId: b.base_name, start: s, end: Math.min(s + dur, fightDuration), mods: b.mods })
      }
    }
    // Worn-item rate procs (Wand of Crystallized Plasma → Plasma Boost):
    // deterministic windows at the rated cadence — the stat bonus applies
    // only INSIDE a window, never folded into the always-on modifiers.
    for (const pb of rotData?.derived?.proc_buffs ?? []) {
      if (Object.keys(pb.mods).length === 0 || pb.per_minute <= 0 || pb.duration_s <= 0) continue
      const period = 60 / pb.per_minute
      for (let s = 0; s < fightDuration; s += period) {
        out.push({ buffId: pb.name, start: s, end: Math.min(s + pb.duration_s, fightDuration), mods: pb.mods })
      }
    }
    return out
  // eslint-disable-next-line react-hooks/exhaustive-deps -- permanentMods reads permTierData/permanentTiers
  }, [externalBuffs, buffDefs, fightDuration, buffSheet, permanentBuffs, enabledGroupBuffObjs, groupBuffStartAt, rotData?.derived, permTierData, permanentTiers, perkMult])
  const externalUptimes = useMemo(() => buffUptimes(buffWindows, fightDuration), [buffWindows, fightDuration])

  // Hidden bonuses auto-derived from gear/adorns/sets/AAs — applied
  // directly (detection has proven reliable; the override UI is gone).
  const derived = rotData?.derived
  const effBaseDamage = derived?.base_damage_bonus_pct ?? 0
  const effCastSpeed = derived?.cast_speed_bonus_pct ?? 0
  const effReuse = derived?.reuse_bonus_pct ?? 0

  // Sheet stats + the class's resolved primary attribute (WIS priest /
  // INT mage / AGI scout / STR fighter) + the hidden bonuses — the stats
  // the sim ACTUALLY uses ("corrected stats").
  const simStats: SimStats = useMemo(() => {
    const s = charData?.stats
    if (!s) return {}
    const archetype = (classes.find(c => c.name === charData.cls)?.archetype ?? '').toLowerCase()
    const attr = archetype.startsWith('priest')
      ? ('wis' as const)
      : archetype.startsWith('mage')
        ? ('int' as const)
        : archetype.startsWith('scout')
          ? ('agi' as const)
          : archetype.startsWith('fighter')
            ? ('str' as const)
            : undefined
    const key = attr === 'wis' ? s.wis_eff : attr === 'int' ? s.int_eff : attr === 'agi' ? s.agi_eff : attr === 'str' ? s.str_eff : null
    // Auto-attack weapon per the chosen mode. VALIDATED (wand log
    // session): the sheet weapon min/max/delay are fully COOKED — the
    // game folds STR/DPS-stat/haste in — so dps/attack_speed are zeroed
    // here and only buff-window deltas apply on top.
    const weapon =
      autoAttackMode === 'ranged'
        ? {
            primary_min: s.ranged_min,
            primary_max: s.ranged_max,
            primary_delay: s.ranged_delay,
            secondary_min: null,
            secondary_max: null,
            secondary_delay: null,
          }
        : {}
    return {
      ...s,
      ...weapon,
      dps: 0,
      attack_speed: 0,
      primary_stat: key,
      primary_attr: attr,
      base_damage_bonus_pct: effBaseDamage,
      school_damage_flat: derived?.school_damage_flat ?? {},
      max_health: s.health_max,
      hostile_cast_pct: derived?.hostile_cast_pct ?? 0,
      hostile_reuse_pct: derived?.hostile_reuse_pct ?? 0,
      beneficial_cast_pct: derived?.beneficial_cast_pct ?? 0,
      beneficial_reuse_pct: derived?.beneficial_reuse_pct ?? 0,
      casting_speed: (s.casting_speed ?? 0) + effCastSpeed,
      reuse_speed: (s.reuse_speed ?? 0) + effReuse,
    }
  }, [charData, classes, effBaseDamage, effCastSpeed, effReuse, autoAttackMode, derived])

  // Stats WITH ticked always-on buffs applied — what the ability
  // palette, hover tooltips and passives display: ticking Crusade or a
  // member's Fearless Morale moves the shown damage/timings immediately,
  // matching what the in-game tooltip would read under those buffs.
  // Calibration deliberately stays on the UNBUFFED baseline — observed
  // values are read solo on the dummy, and factors must not drift when
  // buffs are ticked.
  const buffedStats = useMemo(() => applyMods(simStats, alwaysOnMods), [simStats, alwaysOnMods])

  const calibration = useMemo(
    () => {
      // Maintained toggles calibrate against their tooltips too — fold
      // them into the lookup map so their factors reach the engine.
      const all = { ...abilities }
      for (const a of maintainedAbilities) all[a.base_name] = a
      return computeCalibration(observed, all, simStats)
    },
    [observed, abilities, maintainedAbilities, simStats],
  )

  // Expected auto-attack output per weapon mode (cooked-sheet model,
  // crit included) — shown beside the selector so the choice is informed.
  const autoExpectations = useMemo(() => {
    const s = charData?.stats
    if (!s) return null
    const base: SimStats = { ...simStats }
    const forMode = (mode: 'melee' | 'ranged'): { dps: number; perSwing: number } | null => {
      const stats: SimStats =
        mode === 'ranged'
          ? {
              ...base,
              primary_min: s.ranged_min,
              primary_max: s.ranged_max,
              primary_delay: s.ranged_delay,
              secondary_min: null,
              secondary_max: null,
              secondary_delay: null,
            }
          : {
              ...base,
              primary_min: s.primary_min,
              primary_max: s.primary_max,
              primary_delay: s.primary_delay,
              secondary_min: s.secondary_min,
              secondary_max: s.secondary_max,
              secondary_delay: s.secondary_delay,
            }
      // Ticked always-on buffs reach the auto stream (haste, DPS mod,
      // Destructive Rage's weapon damage %) — same mods the engine uses.
      const buffed = applyMods(stats, alwaysOnMods)
      const dps = autoAttackDps(buffed)
      if (dps <= 0) return null
      const rate = autoSwingRate(buffed)
      return { dps, perSwing: rate > 0 ? dps / rate : 0 }
    }
    return { melee: forMode('melee'), ranged: forMode('ranged') }
  }, [charData, simStats, alwaysOnMods])

  const activePassives = useMemo(
    () => allPassives.filter(p => !disabledPassives.includes(p.base_name)),
    [allPassives, disabledPassives],
  )

  // Ticked group buffs with procs feed the same proc engine as the AA
  // passives. Temps only proc while their window is up — approximated by
  // scaling the chance by the buff's uptime fraction.
  const groupProcPassives: RotationAbility[] = useMemo(
    () =>
      enabledGroupBuffObjs
        .filter(({ buff }) => buff.procs.length > 0)
        .map(({ buff: b, member }) => {
          const frac = isTempBuff(b) ? Math.min(1, (b.duration_s as number) / Math.max(b.recast_s, b.duration_s as number)) : 1
          return {
            name: `${b.name} (${member})`,
            base_name: b.base_name,
            crc: null,
            tier_name: b.tier_name,
            level: b.level,
            spell_type: 'buff',
            beneficial: true,
            cast_secs: 0,
            recast_secs: 0,
            recovery_secs: 0,
            target_type: null,
            icon_id: b.icon_id,
            icon_backdrop: b.icon_backdrop,
            duration_s: b.duration_s,
            power_cost: null,
            components: [],
            procs: b.procs.map(p => ({ ...p, chance_pct: p.chance_pct * frac })),
            effect_lines: b.effect_lines,
            has_unparsed_damage: false,
            source: 'group',
            rank: null,
            // Buff-granted proc damage scales off the SUPPLIER's stats.
            proc_stats: memberBooks[member]?.stats,
          }
        }),
    [enabledGroupBuffObjs, memberBooks],
  )

  // Supplier-bound raid buffs with proc payloads (PotM's Precise Note,
  // CoB's Blade Chime) become proc streams at the SUPPLIER's stats,
  // scaled by the buff's uptime fraction. Skipped when the same buff is
  // already ticked under a group member (no double counting).
  const externalProcPassives: RotationAbility[] = useMemo(() => {
    const groupBases = new Set(enabledGroupBuffObjs.map(({ buff }) => buff.base_name))
    const out: RotationAbility[] = []
    for (const cfg of externalBuffs) {
      const sup = suppliedExternal[cfg.buffId]
      if (sup?.status !== 'ok' || !sup.entry || cfg.providers <= 0) continue
      const entry = sup.entry
      if (entry.procs.length === 0 || groupBases.has(entry.base_name)) continue
      const def = buffDefs[cfg.buffId]
      const dur = cfg.duration_s ?? def.duration_s
      const recast = cfg.recast_s ?? def.recast_s
      const frac = Math.min(1, (cfg.providers * dur) / Math.max(recast, dur))
      out.push({
        name: `${entry.name} (${sup.supplier})`,
        base_name: entry.base_name,
        crc: null,
        tier_name: entry.tier_name,
        level: entry.level,
        spell_type: 'buff',
        beneficial: true,
        cast_secs: 0,
        recast_secs: 0,
        recovery_secs: 0,
        target_type: null,
        icon_id: entry.icon_id,
        icon_backdrop: entry.icon_backdrop,
        duration_s: dur,
        power_cost: null,
        components: [],
        procs: entry.procs.map(p => ({ ...p, chance_pct: p.chance_pct * frac })),
        effect_lines: entry.effect_lines,
        has_unparsed_damage: false,
        source: 'raid',
        rank: null,
        proc_stats: sup.stats,
      })
    }
    return out
  }, [externalBuffs, suppliedExternal, buffDefs, enabledGroupBuffObjs])

  const simConfig: SimConfig | null = useMemo(() => {
    if (!charData || rotation.length === 0) return null
    return {
      rotation,
      abilities,
      stats: simStats,
      fightDurationS: fightDuration,
      autoAttack: autoAttackMode !== 'off',
      autoAttackLabel: `Auto-attack (${autoAttackMode === 'ranged' ? 'ranged' : 'melee'})`,
      target,
      dotRefreshHold: dotHold,
      firstCastAt,
      buffWindows,
      passives: [...activePassives, ...groupProcPassives, ...externalProcPassives],
      procChanceOverrides,
      incomingHitsPerMinute,
      calibration: calibration.factors,
    }
  }, [charData, simStats, abilities, rotation, fightDuration, autoAttackMode, target, dotHold, firstCastAt, buffWindows, activePassives, groupProcPassives, externalProcPassives, procChanceOverrides, incomingHitsPerMinute, calibration])

  const result: SimResult | null = useMemo(() => (simConfig ? simulate(simConfig) : null), [simConfig])

  const loading = char.loading || rot.loading
  const error = char.error || rot.error

  // Own temp buffs cast in the rotation (windows not from the external sheet).
  const selfUptimes: Record<string, number> = {}
  for (const [id, pct] of Object.entries(result?.buffUptimes ?? {})) {
    if (!(id in buffDefs)) selfUptimes[abilities[id]?.name ?? id] = pct
  }

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="font-heading text-gold text-[1.5rem] m-0">Rotation Simulator</h1>
        <span className="text-text-muted text-[0.88rem]">theorycraft your priority order</span>
      </div>

      <div className="grid gap-4 md:grid-cols-[minmax(280px,1fr)_minmax(280px,1fr)]">
        <SimCharacterPicker
          chosen={selectedName ? {
            name: charData?.name ?? selectedName,
            cls: charData?.cls ?? null,
            level: charData?.level ?? null,
          } : null}
          loading={loading}
          error={error}
          onSelect={setSelectedName}
          onClear={() => setSelectedName(null)}
        />

        <Card className="rounded-sm px-4 py-3">
          <SectionLabel>Fight</SectionLabel>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mt-2">
            <label className="flex items-center gap-2 text-[0.85rem]">
              Duration
              <input
                type="number"
                min={MIN_FIGHT_S}
                max={MAX_FIGHT_S}
                value={fightDuration}
                onChange={e => {
                  const v = Number(e.target.value)
                  if (Number.isFinite(v)) setFightDuration(Math.min(Math.max(Math.round(v), MIN_FIGHT_S), MAX_FIGHT_S))
                }}
                className={NUM_INPUT_CLASS}
                aria-label="Fight duration in seconds"
              />
              <span className="text-text-muted text-[0.8rem]">seconds</span>
            </label>
            <label className="flex items-center gap-2 text-[0.85rem]">
              Targets
              <input
                type="number"
                min={1}
                max={12}
                value={targetCount}
                onChange={e => {
                  const v = Number(e.target.value)
                  if (Number.isFinite(v)) setTargetCount(Math.min(Math.max(Math.round(v), 1), 12))
                }}
                className={`${NUM_INPUT_CLASS} w-16`}
                aria-label="Number of targets"
              />
            </label>
            {targetCount > 1 && (
              <label className="flex items-center gap-2 text-[0.85rem] cursor-pointer">
                <input
                  type="checkbox"
                  checked={encounter}
                  onChange={e => setEncounter(e.target.checked)}
                  className="accent-[var(--color-gold)]"
                />
                Linked encounter
                <span className="text-text-muted text-[0.72rem]">(green AE abilities hit all)</span>
              </label>
            )}
            <label
              className="flex items-center gap-2 text-[0.85rem]"
              title="Hits per minute landing on whoever carries your damage-shield buffs (Divine Light on the tank) — drives 'when damaged' procs like Shock of Light. 0 = off."
            >
              Incoming hits
              <input
                type="number"
                min={0}
                max={120}
                value={incomingHitsPerMinute}
                onChange={e => {
                  const v = Number(e.target.value)
                  if (Number.isFinite(v)) setIncomingHitsPerMinute(Math.min(Math.max(Math.round(v), 0), 120))
                }}
                className={`${NUM_INPUT_CLASS} w-16`}
                aria-label="Incoming hits per minute on damage-shield targets"
              />
              <span className="text-text-muted text-[0.8rem]">/min</span>
            </label>
            <label
              className="flex items-center gap-2 text-[0.85rem] cursor-pointer"
              title="EQ2 membership Perks grant +20% beneficial spell duration — stretches every temp-buff window (own, group and raid buffs). Turn off for accounts without perks active."
            >
              <input
                type="checkbox"
                checked={perksOn}
                onChange={e => setPerksOn(e.target.checked)}
                className="accent-[var(--color-gold)]"
              />
              Perks
              <span className="text-text-muted text-[0.72rem]">(+20% buff duration)</span>
            </label>
          </div>
          {availableConditions.length > 0 && (
            <div className="mt-3">
              <div className="text-[0.72rem] text-text-muted uppercase tracking-wide mb-1.5">Target properties</div>
              <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                {availableConditions.map(cond => (
                  <label key={cond} className="flex items-center gap-1.5 text-[0.8rem] cursor-pointer">
                    <input
                      type="checkbox"
                      checked={activeConditions.includes(cond)}
                      onChange={e =>
                        setActiveConditions(prev =>
                          e.target.checked ? [...prev, cond] : prev.filter(c => c !== cond),
                        )
                      }
                      className="accent-[var(--color-gold)]"
                    />
                    {cond.replace(/^If /, '')}
                  </label>
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>

      {selectedName && !loading && rotData && charData && (
        <DerivedPanel
          derived={rotData.derived}
          stats={simStats}
          sheet={charData.stats}
          buffMods={alwaysOnMods}
          primaryLabel={
            (() => {
              const a = (classes.find(c => c.name === charData.cls)?.archetype ?? '').toLowerCase()
              return a.startsWith('priest') ? 'WIS' : a.startsWith('mage') ? 'INT' : a.startsWith('scout') ? 'AGI' : a.startsWith('fighter') ? 'STR' : 'Primary'
            })()
          }
        />
      )}

      {selectedName && !loading && rotData && (
        <RotationBuilder
          abilities={abilities}
          charLevel={charData?.level ?? undefined}
          rotation={rotation}
          dotHold={dotHold}
          stats={buffedStats}
          autoAttackSlot={
            <Card className="rounded-sm px-4 py-3">
              <SectionLabel>Auto-attack</SectionLabel>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-2">
                <select
                  value={autoAttackMode}
                  onChange={e => {
                    setAutoAttackMode(e.target.value as 'melee' | 'ranged' | 'off')
                    setAutoAttackTouched(true)
                  }}
                  className="py-1.5 px-2 rounded-sm2 border border-border bg-surface-raised text-text text-[0.85rem] [color-scheme:dark]"
                  aria-label="Auto-attack weapon"
                >
                  <option value="melee">Melee</option>
                  <option value="ranged">Ranged / wand</option>
                  <option value="off">Off</option>
                </select>
                {autoExpectations && (
                  <span
                    className="text-[0.78rem] text-text-muted flex flex-col gap-0.5"
                    title="Expected auto-attack output per weapon (cooked sheet values, crit included)"
                  >
                    {(['melee', 'ranged'] as const).map(m => {
                      const e = autoExpectations[m]
                      return (
                        <span key={m} className={autoAttackMode === m ? 'text-gold' : undefined}>
                          {m === 'melee' ? 'melee' : 'ranged'}{' '}
                          {e ? `~${fmtNum(Math.round(e.dps))} dps (${fmtNum(Math.round(e.perSwing))}/swing)` : '—'}
                        </span>
                      )
                    })}
                  </span>
                )}
              </div>
              {charData && (
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1.5 text-[0.75rem] text-text-muted">
                  {(
                    [
                      ['DPS', charData.stats.dps, alwaysOnMods.dpsModPct],
                      ['Haste', charData.stats.attack_speed, alwaysOnMods.hastePct],
                      ['Multi attack', charData.stats.double_attack, alwaysOnMods.doubleAttackPct],
                      ['Flurry', charData.stats.flurry, undefined],
                    ] as const
                  ).map(([label, base, delta]) => (
                    <span key={label}>
                      {label} <span className="text-text font-medium">{(base ?? 0).toFixed(1)}</span>
                      {delta ? <span className="text-success"> +{delta.toFixed(1)}</span> : null}
                    </span>
                  ))}
                </div>
              )}
            </Card>
          }
          suggestSlot={<SuggestOrder simConfig={simConfig} onApply={handleRotationChange} />}
          onChange={handleRotationChange}
          onToggleDotHold={(name, hold) =>
            setDotHold(prev => (hold ? [...prev, name] : prev.filter(n => n !== name)))
          }
        />
      )}

      {selectedName && !loading && rotData && buffSheet && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-4">
            <BuffsPanel
              sheet={buffSheet.sheet}
              exact={buffSheet.exact}
              configs={externalBuffs}
              permanentEnabled={permanentBuffs}
              uptimes={externalUptimes}
              selfUptimes={selfUptimes}
              supplied={suppliedExternal}
              tierOptions={permTierOptions}
              tierSelected={permanentTiers}
              onSetTier={(id, tier) => setPermanentTiers(prev => ({ ...prev, [id]: tier }))}
              onChange={setExternalBuffs}
              onSetSupplier={(id, name) =>
                setExternalBuffs(prev => {
                  const existing = prev.find(c => c.buffId === id)
                  if (existing) {
                    return prev.map(c =>
                      c.buffId === id
                        ? { ...c, supplier: name ?? undefined, providers: name && c.providers <= 0 ? 1 : c.providers }
                        : c,
                    )
                  }
                  return name ? [...prev, { buffId: id, providers: 1, supplier: name }] : prev
                })
              }
              onTogglePermanent={(id, on) =>
                setPermanentBuffs(prev => (on ? [...prev, id] : prev.filter(x => x !== id)))
              }
            />
            <PassivesPanel
              passives={allPassives}
              disabled={disabledPassives}
              result={result}
              chanceOverrides={procChanceOverrides}
              stats={buffedStats}
              onToggle={(name, on) =>
                setDisabledPassives(prev => (on ? prev.filter(x => x !== name) : [...prev, name]))
              }
              onChanceChange={(name, pct) =>
                setProcChanceOverrides(prev => {
                  if (pct == null) {
                    const { [name]: _drop, ...rest } = prev
                    return rest
                  }
                  return { ...prev, [name]: pct }
                })
              }
            />
          </div>
          <CalibrationPanel
            rotation={rotation}
            abilities={abilities}
            stats={simStats}
            observed={observed}
            calibration={calibration}
            extraAbilities={maintainedAbilities}
            onChange={setObserved}
          />
        </div>
      )}

      {selectedName && !loading && rotData && (
        <GroupMakeupPanel
          members={groupMembers}
          books={memberBooks}
          enabled={groupBuffs}
          excludeName={selectedName}
          hiddenBaseNames={raidSheetBaseNames}
          onAddMember={name =>
            setGroupMembers(prev => (prev.some(m => m.toLowerCase() === name.toLowerCase()) ? prev : [...prev, name]))
          }
          onRemoveMember={name => setGroupMembers(prev => prev.filter(m => m !== name))}
          onToggle={(name, on) =>
            setGroupBuffs(prev => (on ? [...prev, name] : prev.filter(n => n !== name)))
          }
        />
      )}

      {selectedName && (
        <ResultsPanel
          result={result}
          fightDurationS={fightDuration}
          rotation={rotation}
          abilities={abilities}
          timedExternals={[
            ...(buffSheet?.sheet.buffs ?? [])
              .filter(b => b.toggle && (externalBuffs.find(c => c.buffId === b.id)?.providers ?? 0) > 0)
              .map(b => ({ id: b.id, name: b.name, startAt: externalBuffs.find(c => c.buffId === b.id)?.startAt ?? 0 })),
            ...enabledGroupBuffObjs
              .filter(({ buff }) => isTempBuff(buff) && Object.keys(buff.mods).length > 0)
              .map(({ buff, member }) => ({
                id: `group::${buff.base_name}`,
                name: `${buff.name} (${member})`,
                startAt: groupBuffStartAt[buff.base_name] ?? 0,
              })),
          ]}
          onExternalStartAt={(id, t) =>
            id.startsWith('group::')
              ? setGroupBuffStartAt(prev => ({ ...prev, [id.slice(7)]: t }))
              : setExternalBuffs(prev => prev.map(c => (c.buffId === id ? { ...c, startAt: t } : c)))
          }
          firstCastAt={firstCastAt}
          onFirstCastAt={(name, t) =>
            setFirstCastAt(prev => {
              if (t <= 0) {
                const next = { ...prev }
                delete next[name]
                return next
              }
              return { ...prev, [name]: t }
            })
          }
        />
      )}

      {!selectedName && (
        <Card className="rounded-sm px-4 py-4">
          <p className="text-[0.85rem] text-text-muted m-0">
            Pick a character to load their abilities, then drag together a priority list — the
            simulator casts the highest-priority ready ability, fills gaps with the rest, and
            shows where your damage comes from.
          </p>
        </Card>
      )}
    </main>
  )
}
