// Ability Rotation Simulator — pick a character, build a priority-ordered
// rotation from their real abilities (spells.db timings + parsed damage),
// and simulate a boss-dummy fight with their sheet stats. v1 scope:
// single target, AA/ascension abilities excluded, power ignored,
// proc/conditional damage badged rather than modeled, auto-attack as a
// uniform stream.

import { useEffect, useMemo, useState } from 'react'

import { Card, SectionLabel } from '../components/ui'
import { useFetch } from '../hooks/useFetch'
import type { Character } from './characterSheet'
import { simulate } from './simulator/engine'
import ResultsPanel from './simulator/ResultsPanel'
import RotationBuilder from './simulator/RotationBuilder'
import SimCharacterPicker from './simulator/SimCharacterPicker'
import type { CharacterRotationData, RotationAbility, SimResult, SimTarget } from './simulator/types'

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
  const [autoAttack, setAutoAttack] = useState(true)
  const [autoAttackTouched, setAutoAttackTouched] = useState(false)
  const [targetCount, setTargetCount] = useState(1)
  const [encounter, setEncounter] = useState(true)
  const [activeConditions, setActiveConditions] = useState<string[]>([])

  const enc = selectedName ? encodeURIComponent(selectedName) : null
  const char = useFetch<Character>(enc ? `/api/character/${enc}` : null)
  const rot = useFetch<CharacterRotationData>(enc ? `/api/character/${enc}/rotation-data` : null)

  // Stale-guard: only trust payloads that match the current selection.
  const charData = char.data && selectedName && char.data.name.toLowerCase() === selectedName.toLowerCase() ? char.data : null
  const rotData = rot.data && selectedName && rot.data.character_name.toLowerCase() === selectedName.toLowerCase() ? rot.data : null

  const abilities = useMemo(() => {
    const map: Record<string, RotationAbility> = {}
    for (const a of rotData?.abilities ?? []) map[a.base_name] = a
    return map
  }, [rotData])

  // Every condition string appearing on the loaded abilities — the target
  // panel offers these as toggles (data-driven; nothing hardcoded).
  const availableConditions = useMemo(() => {
    const seen = new Set<string>()
    for (const a of rotData?.abilities ?? []) {
      for (const c of a.components) if (c.condition) seen.add(c.condition)
    }
    return [...seen].sort()
  }, [rotData])

  // New character → fresh rotation + target and re-derive the auto-attack
  // default.
  useEffect(() => {
    setRotation([])
    setDotHold([])
    setAutoAttackTouched(false)
    setActiveConditions([])
    setTargetCount(1)
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

  // Default auto-attack ON for arts-majority (melee) classes, OFF for
  // caster classes — the user's explicit toggle always wins.
  useEffect(() => {
    if (!rotData || autoAttackTouched) return
    const arts = rotData.abilities.filter(a => a.spell_type === 'arts').length
    setAutoAttack(arts >= rotData.abilities.length - arts)
  }, [rotData, autoAttackTouched])

  const target: SimTarget = useMemo(
    () => ({ count: targetCount, encounter, activeConditions }),
    [targetCount, encounter, activeConditions],
  )

  const result: SimResult | null = useMemo(() => {
    if (!charData || rotation.length === 0) return null
    return simulate({
      rotation,
      abilities,
      stats: charData.stats,
      fightDurationS: fightDuration,
      autoAttack,
      target,
      dotRefreshHold: dotHold,
    })
  }, [charData, abilities, rotation, fightDuration, autoAttack, target, dotHold])

  const loading = char.loading || rot.loading
  const error = char.error || rot.error

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
            <label className="flex items-center gap-2 text-[0.85rem] cursor-pointer">
              <input
                type="checkbox"
                checked={autoAttack}
                onChange={e => { setAutoAttack(e.target.checked); setAutoAttackTouched(true) }}
                className="accent-[var(--color-gold)]"
              />
              Auto-attack
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
            AA abilities excluded, power ignored; proc damage is badged, not modeled. AoE hits all
            targets; encounter abilities need the linked-encounter flag. Timings and damage come
            from spell data and your character sheet stats.
          </p>
        </Card>
      </div>

      {selectedName && !loading && rotData && (
        <RotationBuilder
          abilities={abilities}
          rotation={rotation}
          dotHold={dotHold}
          onChange={handleRotationChange}
          onToggleDotHold={(name, hold) =>
            setDotHold(prev => (hold ? [...prev, name] : prev.filter(n => n !== name)))
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
