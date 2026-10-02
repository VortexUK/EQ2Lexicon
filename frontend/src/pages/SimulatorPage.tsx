// Ability Rotation Simulator — pick a character, build a priority-ordered
// rotation from their real abilities (spells.db timings + parsed damage),
// and simulate a boss-dummy fight with their sheet stats. v1 scope:
// single target, AA/ascension abilities excluded, power ignored,
// proc/conditional damage badged rather than modeled, auto-attack as a
// uniform stream.

import { useEffect, useMemo, useState } from 'react'

import { Card, SectionLabel } from '../components/ui'
import { buffSheetForXpac } from '../data/rotationBuffs'
import { useFetch } from '../hooks/useFetch'
import { useServer } from '../hooks/useServer'
import { useClasses } from '../useClasses'
import type { Character } from './characterSheet'
import { buildExternalWindows, buffUptimes } from './simulator/buffs'
import type { ExternalBuffDef } from './simulator/buffs'
import BuffsPanel from './simulator/BuffsPanel'
import { computeCalibration } from './simulator/calibration'
import type { ObservedHits } from './simulator/calibration'
import CalibrationPanel from './simulator/CalibrationPanel'
import DerivedPanel from './simulator/DerivedPanel'
import { simulate } from './simulator/engine'
import GroupMakeupPanel, { isTempBuff } from './simulator/GroupMakeupPanel'
import PassivesPanel from './simulator/PassivesPanel'
import { loadSimState, saveSimState } from './simulator/persistence'
import ResultsPanel from './simulator/ResultsPanel'
import RotationBuilder from './simulator/RotationBuilder'
import SimCharacterPicker from './simulator/SimCharacterPicker'
import SuggestOrder from './simulator/SuggestOrder'
import type {
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
  const [fightDuration, setFightDuration] = useState(DEFAULT_FIGHT_S)
  const [autoAttackMode, setAutoAttackMode] = useState<'melee' | 'ranged' | 'off'>('melee')
  const [autoAttackTouched, setAutoAttackTouched] = useState(false)
  const [targetCount, setTargetCount] = useState(1)
  const [encounter, setEncounter] = useState(true)
  const [activeConditions, setActiveConditions] = useState<string[]>([])
  const [externalBuffs, setExternalBuffs] = useState<ExternalBuffConfig[]>([])
  const [permanentBuffs, setPermanentBuffs] = useState<string[]>([])
  const [disabledPassives, setDisabledPassives] = useState<string[]>([])
  /** Proc-chance corrections keyed by passive NAME — census effect text
   * can be stale vs live (Bolt of Power reads 50% at rank 10 but fires
   * on every attack in the log). */
  const [procChanceOverrides, setProcChanceOverrides] = useState<Record<string, number>>({})
  /** Group member CHARACTER names — each one's buff book is fetched at
   * the ranks that character actually owns. */
  const [groupMembers, setGroupMembers] = useState<string[]>([])
  const [groupBuffs, setGroupBuffs] = useState<string[]>([])
  /** User corrections to the auto-derived hidden bonuses (base damage %,
   * cast speed %, reuse %) — null trusts the derived value. */
  const [overrides, setOverrides] = useState<{ base: number | null; cast: number | null; reuse: number | null }>({
    base: null,
    cast: null,
    reuse: null,
  })
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

  const abilities = useMemo(() => {
    const map: Record<string, RotationAbility> = {}
    for (const a of rotData?.abilities ?? []) if (!a.maintained) map[a.base_name] = a
    return map
  }, [rotData])

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
      setExternalBuffs(saved.externalBuffs ?? [])
      setPermanentBuffs(saved.permanentBuffs ?? [])
      setDisabledPassives(saved.disabledPassives ?? [])
      setProcChanceOverrides(saved.procChanceOverrides ?? {})
      setGroupMembers(saved.groupMembers ?? [])
      setGroupBuffs(saved.groupBuffs ?? [])
      setOverrides(
        saved.overrides ?? {
          // Legacy manual values become corrections; 0 meant "unset".
          base: saved.baseDamageBonusPct || null,
          cast: saved.hiddenCastSpeedPct || null,
          reuse: null,
        },
      )
      setObserved(saved.observed ?? {})
      setFightDuration(saved.fightDuration ?? DEFAULT_FIGHT_S)
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
      setExternalBuffs([])
      setPermanentBuffs([])
      setDisabledPassives([])
      setProcChanceOverrides({})
      setGroupMembers([])
      setGroupBuffs([])
      setOverrides({ base: null, cast: null, reuse: null })
      setObserved({})
      setFightDuration(DEFAULT_FIGHT_S)
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
      externalBuffs,
      permanentBuffs,
      disabledPassives,
      procChanceOverrides,
      groupMembers,
      groupBuffs,
      overrides,
      observed,
      fightDuration,
      autoAttack: autoAttackMode !== 'off',
      autoAttackMode,
      target,
    })
  }, [selectedName, loadedFor, rotation, dotHold, externalBuffs, permanentBuffs, disabledPassives, procChanceOverrides, groupMembers, groupBuffs, overrides, observed, fightDuration, autoAttackMode, target])

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
        if (groupBuffs.includes(`${name}::${b.base_name}`) && !seen.has(b.base_name)) {
          seen.add(b.base_name)
          out.push({ buff: b, member: name })
        }
      }
    }
    return out
  }, [groupMembers, memberBooks, groupBuffs])

  const server = useServer()
  const buffSheet = useMemo(() => buffSheetForXpac(server?.currentXpac ?? null), [server?.currentXpac])

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
    const out: Record<string, ExternalBuffDef> = {}
    for (const b of buffSheet?.sheet.buffs ?? []) {
      const sup = suppliedExternal[b.id]
      out[b.id] =
        sup?.status === 'ok' && sup.entry
          ? {
              duration_s: sup.entry.duration_s ?? b.duration_s,
              recast_s: sup.entry.recast_s || b.recast_s,
              // The supplier's parsed mods when any; the curated estimates
              // otherwise (census text can be era-drifted to zeros).
              mods: Object.keys(sup.entry.mods).length > 0 ? sup.entry.mods : b.mods,
            }
          : { duration_s: b.duration_s, recast_s: b.recast_s, mods: b.mods }
    }
    return out
  }, [buffSheet, suppliedExternal])

  const buffWindows = useMemo(() => {
    const out: BuffWindow[] = buildExternalWindows(externalBuffs, buffDefs, fightDuration)
    // Enabled permanent raid-sheet buffs cover the whole fight.
    for (const b of (buffSheet?.sheet.buffs ?? []).filter(x => x.kind === 'permanent' && permanentBuffs.includes(x.id))) {
      out.push({ buffId: b.id, start: 0, end: fightDuration, mods: b.mods })
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
      const dur = b.duration_s as number
      const recast = Math.max(b.recast_s, dur)
      for (let s = 0; s < fightDuration; s += recast) {
        out.push({ buffId: b.base_name, start: s, end: Math.min(s + dur, fightDuration), mods: b.mods })
      }
    }
    return out
  }, [externalBuffs, buffDefs, fightDuration, buffSheet, permanentBuffs, enabledGroupBuffObjs])
  const externalUptimes = useMemo(() => buffUptimes(buffWindows, fightDuration), [buffWindows, fightDuration])

  // Effective hidden bonuses: the user's correction wins, else the
  // auto-derived value from gear/adorns/sets/AAs.
  const derived = rotData?.derived
  const effBaseDamage = overrides.base ?? derived?.base_damage_bonus_pct ?? 0
  const effCastSpeed = overrides.cast ?? derived?.cast_speed_bonus_pct ?? 0
  const effReuse = overrides.reuse ?? derived?.reuse_bonus_pct ?? 0

  // Sheet stats + the class's resolved primary attribute (WIS priest /
  // INT mage / AGI scout / STR fighter) + the hidden bonuses — the stats
  // the sim ACTUALLY uses ("corrected stats").
  const simStats: SimStats = useMemo(() => {
    const s = charData?.stats
    if (!s) return {}
    const archetype = (classes.find(c => c.name === charData.cls)?.archetype ?? '').toLowerCase()
    const key = archetype.startsWith('priest')
      ? s.wis_eff
      : archetype.startsWith('mage')
        ? s.int_eff
        : archetype.startsWith('scout')
          ? s.agi_eff
          : archetype.startsWith('fighter')
            ? s.str_eff
            : null
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
      base_damage_bonus_pct: effBaseDamage,
      casting_speed: (s.casting_speed ?? 0) + effCastSpeed,
      reuse_speed: (s.reuse_speed ?? 0) + effReuse,
    }
  }, [charData, classes, effBaseDamage, effCastSpeed, effReuse, autoAttackMode])

  const calibration = useMemo(
    () => computeCalibration(observed, abilities, simStats),
    [observed, abilities, simStats],
  )

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
      target,
      dotRefreshHold: dotHold,
      buffWindows,
      passives: [...activePassives, ...groupProcPassives, ...externalProcPassives],
      procChanceOverrides,
      calibration: calibration.factors,
    }
  }, [charData, simStats, abilities, rotation, fightDuration, autoAttackMode, target, dotHold, buffWindows, activePassives, groupProcPassives, externalProcPassives, procChanceOverrides, calibration])

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
              Auto-attack
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
          <p className="text-[0.72rem] text-text-muted mt-3 mb-0 leading-relaxed">
            Damage model validated against in-game tooltips (Divine Strike VII blind test: 0.15%).
            Power ignored; unmodeled proc riders badged. AoE hits all targets; encounter abilities
            need the linked-encounter flag.
          </p>
        </Card>
      </div>

      {selectedName && !loading && rotData && charData && (
        <DerivedPanel
          derived={rotData.derived}
          overrides={overrides}
          stats={simStats}
          sheet={charData.stats}
          primaryLabel={
            (() => {
              const a = (classes.find(c => c.name === charData.cls)?.archetype ?? '').toLowerCase()
              return a.startsWith('priest') ? 'WIS' : a.startsWith('mage') ? 'INT' : a.startsWith('scout') ? 'AGI' : a.startsWith('fighter') ? 'STR' : 'Primary'
            })()
          }
          onOverride={setOverrides}
        />
      )}

      {selectedName && !loading && rotData && (
        <RotationBuilder
          abilities={abilities}
          rotation={rotation}
          dotHold={dotHold}
          stats={simStats}
          onChange={handleRotationChange}
          onToggleDotHold={(name, hold) =>
            setDotHold(prev => (hold ? [...prev, name] : prev.filter(n => n !== name)))
          }
        />
      )}

      {selectedName && !loading && rotData && (
        <SuggestOrder simConfig={simConfig} abilities={abilities} onApply={handleRotationChange} />
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
