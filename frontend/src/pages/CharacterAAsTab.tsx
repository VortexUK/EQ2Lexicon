import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AATree, AATreeData } from '../components/AATree'
import { Card, SectionLabel } from '../components/ui'
import { TabButton } from '../components/ui/TabButton'
import { StatGroup, StatRow } from './CharacterPage'
import { partitionAASpend, TRADESKILL_TYPES } from './aaSpend'
import { mergeParams, safeSetParams } from '../lib/searchParams'

// ── AA types ─────────────────────────────────────────────────────────────────

export interface CharAATree {
  tree_id:     number
  tree_type:   string
  tree_name:   string
  spent:       Record<string, number>   // node_id str → tier
  total_spent: number
}

export interface CharAAProfile {
  name:  string
  trees: CharAATree[]
}

export interface CharAAsResponse {
  character_name: string
  total_spent:    number
  trees:          CharAATree[]
  profiles:       CharAAProfile[]
}

export interface AAConfig {
  xpac:               string
  aa_cap:             number   // adventure AA cap (tradeskill excluded)
  tradeskill_aa_cap:  number   // separate tradeskill pool cap
  unlocked_tree_types: string[]
}

// ── AA data cache + loaders ──────────────────────────────────────────────────
// Module-level: survives re-renders and Vite HMR remounts. Shared with the
// compare page — when two same-subclass characters are compared, each tree
// JSON is fetched once (getTreeData promise cache), the xpac config once per
// session, and a character's AA payload once across the AAs tab and compare.

export interface AACacheEntry {
  charAAs:  CharAAsResponse
  config:   AAConfig
  treeData: Map<number, AATreeData>
}
const aaCache = new Map<string, AACacheEntry>()
const aaInFlight = new Map<string, Promise<AACacheEntry>>()

// /api/aa/config is session-static (like /api/classes) — one fetch, retried
// only after a failure.
let _configPromise: Promise<AAConfig> | null = null

export function getAAConfig(): Promise<AAConfig> {
  if (!_configPromise) {
    _configPromise = fetch('/api/aa/config', { credentials: 'include' })
      .then(r => (r.ok ? (r.json() as Promise<AAConfig>) : Promise.reject(new Error(`Config: HTTP ${r.status}`))))
      .catch(err => {
        _configPromise = null // allow retry on next call
        throw err
      })
  }
  return _configPromise
}

// Per-tree promise cache: a tree_id is fetched at most once per session (tree
// JSON is static reference data). Failures aren't cached — the next caller
// retries.
const _treeCache = new Map<number, Promise<AATreeData | null>>()

export function getTreeData(treeId: number): Promise<AATreeData | null> {
  let p = _treeCache.get(treeId)
  if (!p) {
    p = fetch(`/api/aa/tree/${treeId}`, { credentials: 'include' })
      .then(r => (r.ok ? (r.json() as Promise<AATreeData>) : null))
      .catch(() => null)
      .then(td => {
        if (td === null) _treeCache.delete(treeId)
        return td
      })
    _treeCache.set(treeId, p)
  }
  return p
}

export function getCachedAAData(charName: string): AACacheEntry | undefined {
  return aaCache.get(charName.toLowerCase())
}

/** Load a character's AA payload + config + visible-tree node data.
 * Cache-first with in-flight dedupe; throws on HTTP failure (callers own
 * their error state). The entry's `charAAs.trees` is pre-filtered to the
 * xpac-unlocked tree types. */
export function loadAAData(charName: string): Promise<AACacheEntry> {
  const key = charName.toLowerCase()
  const hit = aaCache.get(key)
  if (hit) return Promise.resolve(hit)
  const pending = aaInFlight.get(key)
  if (pending) return pending

  const promise = (async (): Promise<AACacheEntry> => {
    const [aasRes, config] = await Promise.all([
      fetch(`/api/character/${encodeURIComponent(charName)}/aas`, { credentials: 'include' }),
      getAAConfig(),
    ])
    if (!aasRes.ok) throw new Error(`AAs: HTTP ${aasRes.status}`)
    const charAAs: CharAAsResponse = await aasRes.json()

    // Filter trees to only those unlocked in the current xpac
    const unlocked = new Set(config.unlocked_tree_types)
    const visibleTrees = charAAs.trees.filter(t => unlocked.size === 0 || unlocked.has(t.tree_type))

    // Fetch full node data for each visible tree in parallel (per-tree cached)
    const treeResponses = await Promise.all(visibleTrees.map(t => getTreeData(t.tree_id)))
    const treeData = new Map<number, AATreeData>()
    for (const td of treeResponses) {
      if (td) treeData.set(td.tree_id, td)
    }

    const entry: AACacheEntry = { charAAs: { ...charAAs, trees: visibleTrees }, config, treeData }
    // Only cache COMPLETE entries: if a tree fetch failed transiently, return
    // the partial entry (the UI renders a per-tree fallback) but let the next
    // mount retry instead of pinning the gap in the cache until a full reload.
    if (treeData.size === visibleTrees.length) {
      aaCache.set(key, entry)
    }
    return entry
  })().finally(() => { aaInFlight.delete(key) })

  aaInFlight.set(key, promise)
  return promise
}

// ── Constants ─────────────────────────────────────────────────────────────────

export const TREE_TYPE_LABEL: Record<string, string> = {
  class:              'Class',
  subclass:           'Subclass',
  shadows:            'Shadows',
  heroic:             'Heroic',
  tradeskill:         'Tradeskill',
  tradeskill_general: 'Tradeskill (General)',
  warder:             'Warder',
  prestige:           'Prestige',
  dragon:             'Dragon',
  reign_of_shadows:   'Reign of Shadows',
  far_seas:           'Far Seas',
}

// ── AA Raid Ready card ────────────────────────────────────────────────────────

function AARaidReady({ spent, cap }: { spent: number; cap: number }) {
  if (cap <= 0) return null
  const pct       = Math.min(100, Math.round(spent / cap * 100))
  const raidReady = pct >= 90
  const color     = raidReady ? 'var(--success)' : pct >= 70 ? 'var(--warning)' : 'var(--danger)'

  return (
    <div className="mb-3">
      <SectionLabel>Raid Ready</SectionLabel>
      <div
        className="bg-surface border rounded-sm py-2 px-2.5"
        style={{ borderColor: raidReady ? 'rgba(74,222,128,0.25)' : 'var(--border)' }}
      >
        <div className="flex items-center gap-2.5">
          {/* Percentage */}
          <div
            className="font-heading text-[2rem] font-bold leading-none shrink-0 min-w-[3ch] text-center"
            style={{ color, textShadow: `0 0 20px ${color}55` }}
          >
            {pct}%
          </div>
          {/* Status + detail */}
          <div className="flex-1">
            <div className="text-[0.78rem] font-semibold mb-1" style={{ color: raidReady ? 'var(--success)' : 'var(--danger)' }}>
              {raidReady ? '✓ Raid Ready' : '✗ Not Ready'}
            </div>
            <div className="text-[0.68rem] text-text-muted leading-[1.5]">
              {spent.toLocaleString()} / {cap.toLocaleString()} spent
            </div>
            <div className="text-[0.65rem] text-text-muted opacity-70">
              (90% required)
            </div>
          </div>
        </div>
        {/* Progress bar */}
        <div className="mt-2 h-1 rounded-full bg-border overflow-hidden">
          <div
            className="h-full rounded-full [transition:width_0.3s_ease]"
            style={{ width: `${pct}%`, background: color }}
          />
        </div>
      </div>
    </div>
  )
}

// ── AA progress bar ───────────────────────────────────────────────────────────

function AAProgressBar({ label, value, max, pct }: {
  label: string
  value: number
  max:   number | null
  pct:   number | null
}) {
  const filled  = pct !== null && pct >= 100
  const barColor = filled ? '#22cc22' : 'var(--accent)'
  return (
    <div className="pt-1 pb-[6px]">
      <div className="flex justify-between items-baseline mb-0.5">
        <span className="text-[0.75rem] text-text-muted uppercase tracking-[0.05em]">
          {label}
        </span>
        <span className="text-[0.82rem] font-semibold">
          {value.toLocaleString()}{max !== null ? ` / ${max.toLocaleString()}` : ''}
        </span>
      </div>
      {pct !== null && (
        <>
          <div className="h-[5px] rounded-full bg-border overflow-hidden">
            <div
              className="h-full rounded-full [transition:width_0.3s_ease]"
              style={{ width: `${pct}%`, background: barColor }}
            />
          </div>
          <div className="text-[0.68rem] text-text-muted text-right mt-0.5">
            {pct}%
          </div>
        </>
      )}
    </div>
  )
}

// ── AA Tab ────────────────────────────────────────────────────────────────────

type AATabState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; charAAs: CharAAsResponse; config: AAConfig; treeData: Map<number, AATreeData> }

// 'current' = live AAs; number = index into charAAs.profiles
type ActiveProfile = 'current' | number

function isProfileIndex(p: ActiveProfile): p is number {
  return typeof p === 'number'
}

// The trees shown for a given profile, filtered to the xpac-unlocked types.
export function visibleTreesFor(charAAs: CharAAsResponse, config: AAConfig, profile: ActiveProfile): CharAATree[] {
  const unlocked = new Set(config.unlocked_tree_types)
  const base = isProfileIndex(profile) ? (charAAs.profiles[profile]?.trees ?? []) : charAAs.trees
  return base.filter(t => unlocked.size === 0 || unlocked.has(t.tree_type))
}

// Deep-link resolution: URL ?tree= is a tree_type, ?profile= is a profile name.
function resolveTreeId(want: string | null, trees: CharAATree[]): number | null {
  return want ? (trees.find(t => t.tree_type === want)?.tree_id ?? null) : null
}
function resolveProfile(want: string | null, profiles: CharAAProfile[]): ActiveProfile {
  if (!want || want === 'current') return 'current'
  const idx = profiles.findIndex(p => p.name === want)
  return idx >= 0 ? idx : 'current'
}

export function AAsTab({ charName, aaCount }: { charName: string; aaCount: number }) {
  const cacheKey = charName.toLowerCase()
  const cached   = aaCache.get(cacheKey)

  const [searchParams, setSearchParams] = useSearchParams()
  // Deep-link targets captured once at mount (URL is best-effort mirror after).
  const deepLink = useRef({ tree: searchParams.get('tree'), profile: searchParams.get('profile') })

  const [state, setState] = useState<AATabState>(
    cached ? { status: 'ok', ...cached } : { status: 'loading' }
  )
  const [selectedTreeId, setSelectedTreeId]     = useState<number | null>(
    cached
      ? (resolveTreeId(deepLink.current.tree, cached.charAAs.trees) ?? cached.charAAs.trees[0]?.tree_id ?? null)
      : null
  )
  const [activeProfile, setActiveProfile] = useState<ActiveProfile>(
    cached ? resolveProfile(deepLink.current.profile, cached.charAAs.profiles) : 'current'
  )

  // Mirror active profile + tree to the URL (state is source of truth). The AA
  // tab is the only place this component mounts, so ?profile/?tree only appear
  // there; CharacterView clears them when you leave the tab.
  useEffect(() => {
    if (state.status !== 'ok') return
    const trees = visibleTreesFor(state.charAAs, state.config, activeProfile)
    const tree = trees.find(t => t.tree_id === selectedTreeId)?.tree_type ?? null
    const profile = isProfileIndex(activeProfile) ? (state.charAAs.profiles[activeProfile]?.name ?? null) : null
    safeSetParams(setSearchParams as (...a: unknown[]) => void, [mergeParams({ profile, tree }), { replace: true }])
  }, [state, activeProfile, selectedTreeId, setSearchParams])

  useEffect(() => {
    // Already cached — nothing to fetch
    if (aaCache.has(cacheKey)) return

    let cancelled = false
    loadAAData(charName)
      .then(entry => {
        if (cancelled) return
        setState({ status: 'ok', ...entry })
        // Apply any deep-linked profile/tree now that the data is loaded.
        setActiveProfile(prev =>
          prev === 'current' ? resolveProfile(deepLink.current.profile, entry.charAAs.profiles) : prev
        )
        setSelectedTreeId(
          prev => prev ?? resolveTreeId(deepLink.current.tree, entry.charAAs.trees) ?? entry.charAAs.trees[0]?.tree_id ?? null
        )
      })
      .catch(err => {
        if (!cancelled) setState({ status: 'error', message: String(err) })
      })
    return () => { cancelled = true }
  }, [charName, cacheKey])

  if (state.status === 'loading') {
    return <p className="mt-6 text-text-muted">Loading AA data…</p>
  }
  if (state.status === 'error') {
    return <p className="mt-6 text-danger">Error: {state.message}</p>
  }

  const { charAAs, config, treeData } = state

  // Trees to display for the active profile (current or a saved profile),
  // filtered to the xpac-unlocked types.
  const visibleTrees: CharAATree[] = visibleTreesFor(charAAs, config, activeProfile)

  const activeCt = visibleTrees.find(t => t.tree_id === selectedTreeId) ?? visibleTrees[0]
  const activeTd = activeCt ? treeData.get(activeCt.tree_id) : undefined

  // Tradeskill AAs are a separate pool — split the shown trees so tradeskill
  // never counts against the adventure/expansion cap.
  const { adventure: adventureSpent, tradeskill: tradeskillSpent } = partitionAASpend(visibleTrees)
  const hasTradeskill = visibleTrees.some(t => TRADESKILL_TYPES.has(t.tree_type))
  const tradeskillPct = config.tradeskill_aa_cap > 0
    ? Math.min(100, Math.round((tradeskillSpent / config.tradeskill_aa_cap) * 100))
    : null

  const earnedPct = config.aa_cap > 0
    ? Math.min(100, Math.round((aaCount / config.aa_cap) * 100))
    : null
  const spentPct = aaCount > 0
    ? Math.min(100, Math.round((adventureSpent / aaCount) * 100))
    : null

  return (
    <div className="mt-4 flex flex-col md:flex-row gap-6 items-start">

      {/* ── Left sidebar ── */}
      <div className="w-full md:w-[240px] md:shrink-0">

        {/* Raid Ready (adventure AA — tradeskill is its own pool) */}
        <AARaidReady spent={adventureSpent} cap={config.aa_cap} />

        {/* Profile selector */}
        {charAAs.profiles.length > 0 && (
          <div className="mb-3">
            <SectionLabel className="mb-1">Profile</SectionLabel>
            <div className="flex flex-col gap-[2px]">
              {(['current', ...charAAs.profiles.map((_, i) => i)] as ActiveProfile[]).map(pid => {
                const isActive = activeProfile === pid
                const label    = pid === 'current' ? 'Current' : charAAs.profiles[isProfileIndex(pid) ? pid : 0].name
                return (
                  <button
                    key={String(pid)}
                    onClick={() => setActiveProfile(pid)}
                    className="text-left border rounded-sm cursor-pointer text-[0.78rem] py-1 px-2 overflow-hidden text-ellipsis whitespace-nowrap [transition:background_0.12s,border-color_0.12s]"
                    style={{
                      background: isActive ? 'var(--accent)' : 'var(--surface)',
                      borderColor: isActive ? 'var(--accent)' : 'var(--border)',
                      color: isActive ? '#000' : 'var(--text)',
                      fontWeight: isActive ? 600 : 400,
                    }}
                    title={label}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Expansion */}
        {config.xpac && (
          <StatGroup title="Expansion">
            <div className="py-[3px] text-[0.83rem] text-text">
              {config.xpac}
            </div>
            {config.aa_cap > 0 && (
              <div className="pt-[1px] pb-[3px] text-[0.75rem] text-text-muted">
                {config.aa_cap.toLocaleString()} AA cap
              </div>
            )}
          </StatGroup>
        )}

        {/* Progress — adventure AA (tradeskill counted separately below) */}
        <StatGroup title="Alternate Advancements">
          <AAProgressBar
            label="Earned"
            value={aaCount}
            max={config.aa_cap > 0 ? config.aa_cap : null}
            pct={earnedPct}
          />
          <AAProgressBar
            label="Spent"
            value={adventureSpent}
            max={aaCount}
            pct={spentPct}
          />
        </StatGroup>

        {/* Tradeskill AA — separate pool with its own cap */}
        {hasTradeskill && (
          <StatGroup title="Tradeskill AA">
            <AAProgressBar
              label="Spent"
              value={tradeskillSpent}
              max={config.tradeskill_aa_cap > 0 ? config.tradeskill_aa_cap : null}
              pct={tradeskillPct}
            />
          </StatGroup>
        )}

        {/* Per-tree breakdown */}
        {visibleTrees.length > 0 && (
          <StatGroup title="By Tree">
            {visibleTrees.map(ct => (
              <StatRow
                key={ct.tree_id}
                label={ct.tree_name}
                value={ct.total_spent.toLocaleString()}
              />
            ))}
          </StatGroup>
        )}

      </div>

      {/* ── Right: sub-tabs + tree ── */}
      <div className="flex-1 min-w-0">

        {visibleTrees.length === 0 && (
          <p className="text-text-muted">No AA data available.</p>
        )}

        {visibleTrees.length > 0 && (
          <>
            {/* Tree sub-tabs */}
            <div className="flex flex-wrap gap-[2px] border-b border-border mb-3">
              {visibleTrees.map(ct => {
                const typeLabel = TREE_TYPE_LABEL[ct.tree_type] ?? ct.tree_type
                return (
                  <TabButton
                    key={ct.tree_id}
                    active={ct.tree_id === activeCt?.tree_id}
                    onClick={() => setSelectedTreeId(ct.tree_id)}
                    title={`${typeLabel} · ${ct.total_spent} pts`}
                    className="whitespace-nowrap"
                  >
                    {ct.tree_name}
                  </TabButton>
                )
              })}
            </div>

            {/* Active tree */}
            {activeCt && (
              <div>
                {/* Type label */}
                <div className="mb-1.5 text-[0.72rem] uppercase tracking-[0.06em] text-gold">
                  {TREE_TYPE_LABEL[activeCt.tree_type] ?? activeCt.tree_type}
                </div>

                {/* Tree at 60% of the right column on desktop; full-width with
                    horizontal scroll on narrow viewports so the game-client-
                    replica layout stays pixel-faithful instead of squashing. */}
                <div className="overflow-x-auto md:overflow-visible">
                  <div className="min-w-[420px] md:min-w-0 md:w-[60%]">
                    {activeTd ? (
                      <AATree tree={activeTd} spent={activeCt.spent} />
                    ) : (
                      <Card className="rounded-sm p-4 text-text-muted text-[0.82rem]">
                        Tree data unavailable (tree #{activeCt.tree_id})
                      </Card>
                    )}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

      </div>
    </div>
  )
}
