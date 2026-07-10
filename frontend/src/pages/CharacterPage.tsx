import { useEffect, useState } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import Breadcrumb from '../components/Breadcrumb'
import { Card, SectionLabel } from '../components/ui'
import { TabButton } from '../components/ui/TabButton'
import { ItemTooltip, useItemTooltip, getCachedItem, prefetchItem } from '../components/ItemTooltip'
import { FreshnessBadge } from '../components/FreshnessBadge'
import FavoriteButton from '../components/FavoriteButton'
import { AAsTab } from './CharacterAAsTab'
import { SpellsTab } from './CharacterSpellsTab'
import { useCensusStream } from '../hooks/useCensusStream'
import { mergeParams, safeSetParams } from '../lib/searchParams'
import { fetchCharacter, getCachedCharacter, setCachedCharacter } from '../lib/characterCache'
import { useServer } from '../hooks/useServer'

// ── Types + shared sheet config ──────────────────────────────────────────────
// Types, stat groups, slot layout, and tier styling live in characterSheet.ts —
// shared with the compare page so the two can never drift.

import {
  type Character,
  type EquipmentSlot,
  type Fmt,
  CONSUMABLE_SLOTS,
  LEFT_SLOTS,
  RIGHT_SLOTS,
  STAT_GROUPS,
  WEAPON_SLOTS,
  buildSlotMap,
  fmtStat,
  tierStyle,
} from './characterSheet'

// ── Gear rating ──────────────────────────────────────────────────────────────

interface RatingBand   { label: string; min_below_max: number }
interface RatingConfig {
  bands:              RatingBand[]
  fallback_band:      string
  matrix:             Record<string, Record<string, string>>
  grade_scores:       Record<string, number>
  raid_ready_min_avg: number
}

const DEFAULT_RATING_CONFIG: RatingConfig = {
  bands:              [{ label: 'current', min_below_max: 4 }, { label: 'recent', min_below_max: 10 }],
  fallback_band:      'outdated',
  matrix: {
    fabled:    { current: 'A', recent: 'B', outdated: 'E' },
    legendary: { current: 'B', recent: 'C', outdated: 'F' },
    treasured: { current: 'D', recent: 'E', outdated: 'F' },
  },
  grade_scores:       { A: 10, B: 8, C: 6, D: 4, E: 2, F: 0 },
  raid_ready_min_avg: 5.5,
}

/**
 * Classify an item tier string into one of three groups used by the matrix.
 * Handles compound strings like "Mastercrafted Fabled" (uses the highest tier found).
 */
function ratingTierGroup(tier: string | null): 'fabled' | 'legendary' | 'treasured' | null {
  const t = (tier ?? '').toLowerCase()
  if (t.includes('mythical') || t.includes('fabled')) return 'fabled'
  if (t.includes('legendary') || t.includes('mastercrafted')) return 'legendary'
  if (t.includes('treasured') || t.includes('uncommon') || t.includes('handcrafted') || t.includes('common')) return 'treasured'
  return null
}

/**
 * Score a single equipped item (0–10).
 * Returns null if the item has no tier/level data yet (still loading).
 */
function scoreItem(item: EquipmentSlot, maxLevel: number, cfg: RatingConfig): number | null {
  if (!item.item_id) return null
  const detail = getCachedItem(item.item_id)
  const itemLevel = detail?.item_level ?? null
  const group = ratingTierGroup(item.tier)
  if (group === null || itemLevel === null) return null

  // Determine level band by walking cfg.bands from best to worst
  let band = cfg.fallback_band
  for (const b of cfg.bands) {
    if (itemLevel >= maxLevel - b.min_below_max) { band = b.label; break }
  }

  const gradeLetter = cfg.matrix[group]?.[band]
  if (!gradeLetter) return null
  return cfg.grade_scores[gradeLetter] ?? 0
}

/** Convert a numeric average into a display grade with optional +/− modifier. */
function gradeLabel(avg: number, cfg: RatingConfig): { grade: string; color: string; raidReady: boolean } {
  const raidReady = avg >= cfg.raid_ready_min_avg

  // Half-step display grades derived from the 0–10 score scale
  let grade: string
  if      (avg >= 9.5) grade = 'A'
  else if (avg >= 8.5) grade = 'A−'
  else if (avg >= 7.5) grade = 'B+'
  else if (avg >= 7.0) grade = 'B'
  else if (avg >= 6.5) grade = 'B−'
  else if (avg >= 5.5) grade = 'C+'
  else if (avg >= 5.0) grade = 'C'
  else if (avg >= 4.5) grade = 'C−'
  else if (avg >= 3.0) grade = 'D'
  else if (avg >= 1.0) grade = 'E'
  else                 grade = 'F'

  // Colour echoes the EQ2 quality palette
  const color =
    avg >= 9.0 ? '#e8d5a3' :
    avg >= 7.5 ? '#ff939d' :
    avg >= 7.0 ? '#ffc993' :
    avg >= 5.5 ? '#92d7fd' :
    avg >= 4.5 ? '#a8d4a8' :
                 'var(--danger)'

  return { grade, color, raidReady }
}

const SKIP_GEAR_SLOTS = new Set(['food', 'drink'])

function GearRating({ equipment, ready, maxLevel, ratingConfig, ilvl }: {
  equipment: EquipmentSlot[]
  ready: boolean
  maxLevel: number
  ratingConfig: RatingConfig
  ilvl: number | null
}) {
  const bySlot = buildSlotMap(equipment)

  const scored: number[] = []
  let pending = 0
  for (const [key, item] of bySlot) {
    if (SKIP_GEAR_SLOTS.has(key) || !item.item_id) continue
    const s = scoreItem(item, maxLevel, ratingConfig)
    if (s !== null) scored.push(s)
    else if (!ready) pending++   // still loading
  }

  if (scored.length === 0) {
    return (
      <div className="mb-4">
        <SectionLabel>Raid Ready</SectionLabel>
        <Card className="rounded-sm p-2 text-center text-text-muted text-[0.78rem] italic">
          {ready ? 'No gear data' : 'Loading item data…'}
        </Card>
      </div>
    )
  }

  const avg = scored.reduce((a, b) => a + b, 0) / scored.length
  const { grade, color, raidReady } = gradeLabel(avg, ratingConfig)

  return (
    <div className="mb-4">
      <SectionLabel>Raid Ready</SectionLabel>
      <div
        className="bg-surface border rounded-sm px-2.5 py-2"
        style={{
          borderColor: raidReady ? 'rgba(74,222,128,0.25)' : 'var(--border)',
        }}
      >
        <div className="flex items-center gap-2.5">
          {/* Grade letter */}
          <div
            className="font-heading text-[2.6rem] font-bold leading-none shrink-0 min-w-[2ch] text-center"
            style={{
              color,
              textShadow: `0 0 20px ${color}55`,
            }}
          >
            {grade}
          </div>

          {/* Status + detail */}
          <div className="flex-1">
            <div className="text-[0.78rem] font-semibold mb-1" style={{ color: raidReady ? 'var(--success)' : 'var(--danger)' }}>
              {raidReady ? '✓ Raid Ready' : '✗ Not Ready'}
            </div>
            <div className="text-[0.68rem] text-text-muted leading-[1.5]">
              {scored.length} item{scored.length !== 1 ? 's' : ''} rated
              {pending > 0 && <span className="opacity-60"> · {pending} loading</span>}
            </div>
            <div className="text-[0.65rem] text-text-muted opacity-70">
              (C+ or above = raid ready)
            </div>
          </div>
        </div>

        {/* Average gear item level — under the grade/check, full width */}
        {ilvl != null && (
          <div className="mt-2 pt-2 border-t border-border flex items-baseline justify-between">
            <span className="text-[0.68rem] uppercase tracking-wide text-text-muted">Item Level</span>
            <span className="font-heading text-[1.1rem] font-bold text-gold leading-none">
              {Math.round(ilvl).toLocaleString()}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Paperdoll slot config ────────────────────────────────────────────────────

// Adornment slot colours — matches EQ2 in-game colours
const ADORN_COLOUR: Record<string, string> = {
  White:     '#e8e8e8',
  Yellow:    '#e8c840',
  Red:       '#e05050',
  Green:     '#50c850',
  Blue:      '#5090e8',
  Purple:    '#b060e0',
  Orange:    '#e08830',
  Turquoise: '#30c8c0',
  Black:     '#a0a0a0',
}
function adornColour(color: string) {
  return ADORN_COLOUR[color] ?? '#888'
}

// Adorn name shortening --------------------------------------------------------
// "<Adjective> Adornment of <Name> (<Quality>)"  →  "Adj <Name> (X)"
// Adornment quality → tier letter + colour. Colours reference the canonical
// --rarity-* tokens so adornment rarity matches item/recipe rarity app-wide.
const ADORN_QUALITY_TIER: Record<string, { letter: string; color: string }> = {
  Superior:      { letter: 'F', color: 'var(--rarity-fabled)' },
  Fabled:        { letter: 'F', color: 'var(--rarity-fabled)' },
  Legendary:     { letter: 'L', color: 'var(--rarity-legendary)' },
  Treasured:     { letter: 'T', color: 'var(--rarity-treasured)' },
  Mastercrafted: { letter: 'T', color: 'var(--rarity-treasured)' },
  Uncommon:      { letter: 'U', color: 'var(--rarity-handcrafted)' },
  Common:        { letter: 'C', color: 'var(--text)' },
  Greater:       { letter: 'L', color: 'var(--rarity-legendary)' },
  Lesser:        { letter: 'T', color: 'var(--rarity-treasured)' },
}
const ADORN_RE = /^(\w+)\s+Adornment\s+of\s+(.+?)\s*\((.+?)\)\s*$/i

interface ParsedAdorn { short: string; tierLetter: string; tierColor: string }

function parseAdornName(name: string): ParsedAdorn | null {
  const m = name.match(ADORN_RE)
  if (!m) return null
  const tier = ADORN_QUALITY_TIER[m[3]]
  if (!tier) return null
  return { short: `${m[1].slice(0, 3)} ${m[2]}`, tierLetter: tier.letter, tierColor: tier.color }
}

// ── Stat ↔ item-stat matching ─────────────────────────────────────────────────
//
// Panel labels sometimes differ from the Census stat display_name.
// Each entry maps a lowercased panel label to alternative strings to try.
const STAT_ALIASES: Record<string, string[]> = {
  // Panel "Armor" (armor class) and "Physical Mit" both derive from the
  // "Mitigation" stat on armour pieces.
  'armor':              ['mitigation'],
  'physical mit':       ['mitigation'],
  // Elemental / Noxious / Arcane resistances are a single combined
  // "Resistances" stat on items.
  'elemental mit':      ['resistances', 'resistance'],
  'noxious mit':        ['resistances', 'resistance'],
  'arcane mit':         ['resistances', 'resistance'],
  // STR / AGI / WIS / INT appear on items as the collective "Primary Attributes"
  // stat (which grants the wearer's class-appropriate attribute).  Stamina is
  // a separate stat on items and matches by its own name.
  'strength':           ['primary attributes'],
  'agility':            ['primary attributes'],
  'wisdom':             ['primary attributes'],
  'intelligence':       ['primary attributes'],
  'crit chance':        ['critical chance'],
  'crit bonus':         ['critical bonus'],
  'ability mod':        ['ability modifier'],
  'weapon damage':      ['weapon damage bonus'],
  'ability doublecast': ['ability double cast'],
  'attack speed':       ['haste'],
}

function statMatches(panelLabel: string, itemStatName: string): boolean {
  const label = panelLabel.toLowerCase()
  const stat  = itemStatName.toLowerCase()
  if (label === stat) return true
  if (stat.includes(label) || label.includes(stat)) return true
  return (STAT_ALIASES[label] ?? []).some(a => stat === a || stat.includes(a))
}

// ── Page ─────────────────────────────────────────────────────────────────────

type State =
  | { status: 'loading' }
  | { status: 'ok'; char: Character }
  | { status: 'not_found'; name: string }
  | { status: 'error'; message: string }
  | { status: 'census_unavailable'; name: string }

// Module-level promise-cache for gear_rating config — fetched once, shared
// across navigations. Nulled on failure so the next mount can retry.
let _configPromise: Promise<RatingConfig> | null = null

function getRatingConfig(): Promise<RatingConfig> {
  if (!_configPromise) {
    _configPromise = fetch('/api/config', { credentials: 'include' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => (d?.gear_rating ?? DEFAULT_RATING_CONFIG) as RatingConfig)
      .catch(err => {
        _configPromise = null   // allow retry on next mount
        throw err
      })
  }
  return _configPromise
}

export default function CharacterPage() {
  const { name } = useParams<{ name: string }>()
  const server = useServer()
  const [state, setState] = useState<State>(() => {
    const cached = name ? getCachedCharacter(name) : undefined
    return cached ? { status: 'ok', char: cached } : { status: 'loading' }
  })
  const [ratingConfig, setRatingConfig] = useState<RatingConfig>(DEFAULT_RATING_CONFIG)
  const { subscribe } = useCensusStream()

  // max_level is served by useServer() (from /api/server).
  // Fall back to 50 while the context is still loading.
  const maxLevel = server?.maxLevel ?? 50

  // Fetch gear_rating from /api/config once (promise-cached in module scope).
  // max_level is no longer read from here — it comes from useServer() above.
  useEffect(() => {
    getRatingConfig().then(setRatingConfig).catch(() => { /* render with default config */ })
  }, [])

  useEffect(() => {
    if (!name) return
    // Already have fresh data — don't hit Census again. (fetchCharacter is
    // cache-first + in-flight-deduped; shared with the compare page.)
    if (getCachedCharacter(name)) return
    fetchCharacter(name).then(result => {
      switch (result.status) {
        case 'ok':                  setState({ status: 'ok', char: result.char }); break
        case 'not_found':           setState({ status: 'not_found', name }); break
        case 'census_unavailable':  setState({ status: 'census_unavailable', name }); break
        case 'error':               setState({ status: 'error', message: result.message }); break
      }
    })
  }, [name])

  // SSE live-swap: replace character state when the server pushes a fresh record.
  // Deps use stable primitives (name/world strings + the stable subscribe callback)
  // so this effect never re-runs after the character loads — no render loop risk.
  const charName  = state.status === 'ok' ? state.char.name  : undefined
  const charWorld = state.status === 'ok' ? state.char.world : undefined
  useEffect(() => {
    if (!charName || !charWorld) return
    const key = `${charName.toLowerCase()}:${charWorld.toLowerCase()}`
    return subscribe<Character>(key, (updated) => {
      setCachedCharacter(updated)
      setState({ status: 'ok', char: updated })
    })
  }, [charName, charWorld, subscribe])

  return (
    <main className="max-w-[1280px] my-8 mx-auto px-4">
      <Breadcrumb items={[{ label: 'Characters', to: '/characters' }, { label: name ?? '…' }]} />
      {state.status === 'loading' && <p className="mt-8 text-text-muted">Loading…</p>}
      {state.status === 'not_found' && <p className="mt-8 text-text-muted">Character <strong>{state.name}</strong> not found.</p>}
      {state.status === 'census_unavailable' && (
        <p className="mt-8 text-text-muted">
          <strong>{state.name}</strong> isn't cached yet and Census is currently unavailable. Try again shortly.
        </p>
      )}
      {state.status === 'error' && <p className="mt-8 text-danger">Error: {state.message}</p>}
      {state.status === 'ok' && <CharacterView char={state.char} maxLevel={maxLevel} ratingConfig={ratingConfig} />}
    </main>
  )
}

// ── Character view ────────────────────────────────────────────────────────────

type ActiveTab = 'equipment' | 'aas' | 'spells'

const TABS: readonly ActiveTab[] = ['equipment', 'aas', 'spells']

function CharacterView({ char, maxLevel, ratingConfig }: { char: Character; maxLevel: number; ratingConfig: RatingConfig }) {
  const bySlot = buildSlotMap(char.equipment)
  const { tooltip, showTip, hideTip, moveTip } = useItemTooltip()
  const [hoveredStat, setHoveredStat] = useState<string | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  // Deep-linkable tab: ?tab=aas|spells (equipment is the default, no param).
  const [activeTab, setActiveTab] = useState<ActiveTab>(() => {
    const t = searchParams.get('tab')
    return TABS.includes(t as ActiveTab) ? (t as ActiveTab) : 'equipment'
  })
  // Mirror the tab to the URL (React state is source of truth; URL best-effort).
  // Off the AA tab, drop the AA-only params so links stay clean.
  useEffect(() => {
    const updates: Record<string, string | null> = { tab: activeTab === 'equipment' ? null : activeTab }
    if (activeTab !== 'aas') { updates.profile = null; updates.tree = null }
    safeSetParams(setSearchParams as (...a: unknown[]) => void, [mergeParams(updates), { replace: true }])
  }, [activeTab, setSearchParams])
  // Tracks when background prefetch completes so highlights + gear rating re-evaluate.
  const [itemsReady, setItemsReady] = useState(false)

  // Eagerly fetch stats for every equipped item + adorn so highlights work
  // without the user having to hover each item first.
  useEffect(() => {
    const ids: string[] = []
    for (const slot of char.equipment) {
      if (slot.item_id) ids.push(slot.item_id)
      for (const a of slot.adorn_slots) {
        if (a.adorn_id) ids.push(a.adorn_id)
      }
    }
    if (ids.length === 0) { setItemsReady(true); return }
    Promise.allSettled(ids.map(prefetchItem)).then(() => setItemsReady(true))
  }, [char])

  /** Returns whether this slot's item or adorns contribute to the hovered stat. */
  function getHighlight(item: EquipmentSlot | null): 'direct' | 'adorn' | null {
    if (!hoveredStat || !item) return null
    // Mitigation is a top-level property on ItemDetail, not in stats[].
    // Physical Mit and Armor both derive from it.
    const isMitStat = hoveredStat === 'Physical Mit' || hoveredStat === 'Armor'
    if (item.item_id) {
      const d = getCachedItem(item.item_id)
      if (d) {
        const hasStat = d.stats.some(s => statMatches(hoveredStat, s.display_name))
        const hasMit  = isMitStat && d.mitigation != null && d.mitigation > 0
        if (hasStat || hasMit) return 'direct'
      }
    }
    const adornHit = item.adorn_slots.some(a => {
      if (!a.adorn_id) return false
      const d = getCachedItem(a.adorn_id)
      if (!d) return false
      return d.stats.some(s => statMatches(hoveredStat, s.display_name))
    })
    return adornHit ? 'adorn' : null
  }

  return (
    <div className="mt-6" onMouseMove={moveTip}>
      {/* Full-width general banner */}
      <GeneralBanner char={char} />

      {/* Tab bar */}
      <div className="flex flex-wrap gap-0 border-b border-border mt-4">
        {(['equipment', 'aas', 'spells'] as ActiveTab[]).map(tab => {
          const label = tab === 'equipment' ? 'Equipment & Stats'
                      : tab === 'aas'       ? 'Alternate Advancements'
                      :                       'Spells'
          return (
            <TabButton
              key={tab}
              active={tab === activeTab}
              onClick={() => setActiveTab(tab)}
            >
              {label}
            </TabButton>
          )
        })}
      </div>

      {/* Equipment & Stats tab */}
      {activeTab === 'equipment' && (
        <div className="flex flex-col md:flex-row gap-6 items-start mt-4">
          {/* Left: gear rating + detailed stats */}
          <div className="w-full md:w-[260px] md:shrink-0">
            <GearRating equipment={char.equipment} ready={itemsReady} maxLevel={maxLevel} ratingConfig={ratingConfig} ilvl={char.ilvl} />
            <StatsPanel char={char}
              onStatHover={setHoveredStat}
              onStatLeave={() => setHoveredStat(null)} />
          </div>

          {/* Right: paperdoll */}
          <div className="flex-1 min-w-0">
            <SectionLabel variant="muted">Equipment</SectionLabel>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-y-1 gap-x-3">
              <div className="flex flex-col gap-1">
                {LEFT_SLOTS.map(([label, key]) => {
                  const item = bySlot.get(key) ?? null
                  return <SlotRow key={key} label={label} item={item} iconSide="left" onShow={showTip} onHide={hideTip} highlight={getHighlight(item)} />
                })}
              </div>
              <div className="flex flex-col gap-1">
                {RIGHT_SLOTS.map(([label, key]) => {
                  const item = bySlot.get(key) ?? null
                  return <SlotRow key={key} label={label} item={item} iconSide="right" onShow={showTip} onHide={hideTip} highlight={getHighlight(item)} />
                })}
              </div>
            </div>

            <SectionLabel variant="muted" className="mt-4">Consumables</SectionLabel>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-y-1 gap-x-3">
              {CONSUMABLE_SLOTS.map(([label, key]) => {
                const item = bySlot.get(key) ?? null
                return <SlotRow key={key} label={label} item={item} iconSide="left" onShow={showTip} onHide={hideTip} highlight={getHighlight(item)} />
              })}
            </div>
          </div>
        </div>
      )}

      {/* AAs tab */}
      {activeTab === 'aas' && <AAsTab charName={char.name} aaCount={char.aa_count} />}

      {/* Spells tab */}
      {activeTab === 'spells' && <SpellsTab charName={char.name} />}

      {tooltip && <ItemTooltip state={tooltip} />}
    </div>
  )
}

// ── General banner (full width, above equipment) ──────────────────────────────

// Each column holds a top row and an optional bottom row: [label, value]
type BannerCol = [[string, string], [string, string] | null]

function GeneralBanner({ char }: { char: Character }) {
  const s = char.stats

  const columns: BannerCol[] = [
    [
      ['Level',      `${char.level ?? '—'} ${char.cls ?? ''}`.trim()],
      char.ts_class ? ['Tradeskill', `${char.ts_level ?? '—'} ${char.ts_class}`] : null,
    ],
    [
      ['AAs',    char.aa_count.toLocaleString()],
      char.deity ? ['Deity', char.deity] : null,
    ],
    [
      ['Health', s.health_max  != null ? s.health_max.toLocaleString()  : '—'],
      ['Power',  s.power_max   != null ? s.power_max.toLocaleString()   : '—'],
    ],
    [
      ['Run Speed', s.run_speed     != null ? `${Math.round(s.run_speed)}%`       : '—'],
      ['Status',    s.status_points != null ? s.status_points.toLocaleString()    : '—'],
    ],
  ]

  return (
    <Card className="rounded-sm2 px-4 py-2 flex flex-wrap items-stretch gap-y-2">
      {/* Identity: name + subtitle, separated by a divider */}
      <div className="w-full md:w-auto md:pr-5 md:mr-5 md:border-r border-border flex flex-col justify-center shrink-0">
        <div className="flex items-center gap-2">
          <div
            className="font-heading text-[1.6rem] font-bold leading-[1.2] tracking-[0.04em] inline-block"
            style={{
              background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 40%, var(--gold) 70%, var(--gold-dim) 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}
          >{char.name}</div>
          <FavoriteButton name={char.name} />
        </div>
        <div className="text-text-muted text-[0.82rem] mt-0.5">
          {[char.world, char.race, char.gender].filter(Boolean).join(' · ')}
        </div>
        <FreshnessBadge stale={char.stale} />
        {char.guild_name && (
          <Link
            to={`/guild/${encodeURIComponent(char.guild_name)}`}
            className="inline-block mt-1 text-[0.82rem] no-underline font-medium text-gold"
          >
            ⚔ {char.guild_name}
          </Link>
        )}
        <Link
          to={`/compare?a=${encodeURIComponent(char.name)}`}
          className="inline-block mt-1 text-[0.82rem] no-underline font-medium text-gold-dim hover:text-gold"
        >
          ⚖ Compare
        </Link>
      </div>

      {/* Stat columns, each divided */}
      {columns.map(([top, bottom], i) => (
        <div
          key={i}
          className={`flex-1 pl-4 flex flex-col justify-center gap-1 ${i < columns.length - 1 ? 'pr-4 border-r border-border' : ''}`}
        >
          <BannerStat label={top[0]} value={top[1]} />
          {bottom && <BannerStat label={bottom[0]} value={bottom[1]} />}
        </div>
      ))}
    </Card>
  )
}

function BannerStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-baseline gap-2">
      <span className="text-[0.72rem] uppercase tracking-[0.06em] text-text-muted whitespace-nowrap">{label}</span>
      <span className="text-[0.9rem] font-semibold whitespace-nowrap">{value}</span>
    </div>
  )
}

// ── Stats panel (left of paperdoll, no General group) ─────────────────────────

function StatsPanel({ char, onStatHover, onStatLeave }: {
  char: Character
  onStatHover: (label: string) => void
  onStatLeave: () => void
}) {
  const s = char.stats
  // Convenience: create hover/leave props for a given label
  const h = (label: string) => ({ onHover: () => onStatHover(label), onLeave: onStatLeave })

  return (
    <div>
      {/* Data-driven from the shared STAT_GROUPS config (characterSheet.ts) —
          hover labels are the row labels, so STAT_ALIASES matching is unchanged. */}
      {STAT_GROUPS.map(group => (
        <StatGroup key={group.title} title={group.title}>
          {group.rows.map(row => (
            <StatRow key={row.label} label={row.label} value={s[row.key]} fmt={row.fmt} {...h(row.label)} />
          ))}
        </StatGroup>
      ))}

      <StatGroup title="Weapon">
        {WEAPON_SLOTS.map(w => {
          const min = s[w.min], max = s[w.max], delay = s[w.delay]
          if (min == null || max == null) return null
          return (
            <StatRow
              key={w.label}
              label={w.label}
              value={`${min.toLocaleString()} – ${max.toLocaleString()}  (${delay?.toFixed(2)}s)`}
            />
          )
        })}
      </StatGroup>
    </div>
  )
}

// ── Stat display helpers ──────────────────────────────────────────────────────

export function StatRow({ label, value, fmt: format, onHover, onLeave }: {
  label: string
  value: number | string | null | undefined
  fmt?: Fmt
  onHover?: () => void
  onLeave?: () => void
}) {
  if (value === null || value === undefined) return null
  const display = typeof value === 'number' ? fmtStat(value, format) : value
  return (
    <div
      className="flex justify-between items-baseline py-[2px] border-b border-border"
      style={{ cursor: onHover ? 'default' : undefined }}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
    >
      <span className="text-text-muted text-[0.78rem] pr-2">{label}</span>
      <span className="text-[0.85rem] font-medium text-right">{display}</span>
    </div>
  )
}

export function StatGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <SectionLabel>{title}</SectionLabel>
      <Card className="rounded-sm px-2 py-1">
        {children}
      </Card>
    </div>
  )
}

// ── Paperdoll helpers ─────────────────────────────────────────────────────────

function SlotRow({ label, item, iconSide, onShow, onHide, highlight }: {
  label: string
  item: EquipmentSlot | null
  iconSide: 'left' | 'right'
  onShow: (itemId: string, e: React.MouseEvent, adorns?: { color: string; bonus: number }[]) => void
  onHide: () => void
  highlight: 'direct' | 'adorn' | null
}) {
  const url = item?.icon_id ? `/icons/${item.icon_id}.png` : null
  const hasAdorns = (item?.adorn_slots.length ?? 0) > 0

  const iconEl = (
    <div
      className={iconBoxClass}
      style={{ backgroundImage: `url('/slot-empty-blue.png')`, backgroundSize: 'cover', backgroundPosition: 'center' }}
    >
      {url && <img src={url} alt={item?.name ?? ''} className="w-10 h-10 block" onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />}
    </div>
  )
  const textEl = (
    <div className="flex-1 min-w-0 flex flex-col justify-center gap-[2px]">
      <div className="overflow-hidden text-ellipsis whitespace-nowrap leading-[1.2]">
        <span className="text-[0.78rem] text-text-muted font-medium">{label} – </span>
        {item
          ? <span className="font-medium text-[0.88rem]" style={tierStyle(item.tier)}>{item.name}</span>
          : <span className="text-border text-[0.82rem] italic">Empty</span>}
      </div>
      {hasAdorns && (
        <div className="flex flex-wrap gap-y-0.5 gap-x-1 mt-px">
          {item!.adorn_slots.map((a, i) => {
            const parsed = a.adorn_name ? parseAdornName(a.adorn_name) : null
            return (
              <span
                key={i}
                data-adorn-id={a.adorn_id ?? undefined}
                className="text-[0.62rem] leading-none px-1 py-px rounded-sm border whitespace-nowrap overflow-hidden text-ellipsis max-w-[150px]"
                style={{
                  borderColor: adornColour(a.color),
                  color: a.adorn_name ? adornColour(a.color) : 'var(--text-muted)',
                  fontStyle: a.adorn_name ? 'normal' : 'italic',
                  opacity: a.adorn_name ? 1 : 0.6,
                  cursor: a.adorn_id ? 'default' : undefined,
                }}
              >
                {parsed ? (
                  <>{parsed.short} <span style={{ color: parsed.tierColor }}>({parsed.tierLetter})</span></>
                ) : (
                  a.adorn_name ?? 'Empty'
                )}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
  const hlBg     = highlight === 'direct' ? 'rgba(34,255,34,0.13)'
                 : highlight === 'adorn'  ? 'rgba(34,255,34,0.05)'
                 : undefined
  const hlBorder = highlight === 'direct' ? 'rgba(34,255,34,0.50)'
                 : highlight === 'adorn'  ? 'rgba(34,255,34,0.22)'
                 : undefined

  // Shared show handler: extracted so onMouseOver AND onClick can both fire it.
  // onClick gives touch users a tap-to-open path since touch doesn't fire hover.
  const showHandler = item?.item_id ? (e: React.MouseEvent) => {
    const adornEl = (e.target as HTMLElement).closest('[data-adorn-id]')
    if (adornEl) {
      const adornId = adornEl.getAttribute('data-adorn-id')
      if (adornId) { onShow(adornId, e); return }
    }
    onShow(item.item_id!, e, item.adorn_slots.map(a => ({ color: a.color, bonus: a.ilvl_bonus })))
  } : undefined

  return (
    <div
      className="flex items-center gap-2 border rounded-sm px-[6px] py-1 min-w-0 h-auto min-h-[50px] transition-[background,border-color] duration-[120ms] ease"
      style={{
        flexDirection: iconSide === 'left' ? 'row' : 'row-reverse',
        background:   hlBg     ?? 'var(--surface)',
        borderColor:  hlBorder ?? 'var(--border)',
      }}
      onMouseOver={showHandler}
      onClick={showHandler}
      onMouseLeave={item?.item_id ? onHide : undefined}
    >
      {iconEl}{textEl}
    </div>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

const iconBoxClass = 'w-10 h-10 shrink-0 rounded-sm flex items-center justify-center overflow-hidden'
