import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { StatGroup } from './CharacterPage'
import { Button, Card, SectionLabel } from '../components/ui'
import { SpellTierPip } from '../components/SpellScrollTooltip'
import { itemRarityColor } from '../rarityColors'
import {
  type SpellEntry,
  type CharacterSpellsData,
  type Ingredient,
  type UpgradeMaterialsData,
  spellsCache,
  materialsCache,
  SPELL_TIER_ORDER,
  SPELL_TIER_ICON,
  SPELL_TIER_COLOURS,
} from '../spellConstants'

// ── Shopping-list types (mirrors RecipesPage.tsx) ─────────────────────────────

const SHOPPING_KEY = 'eq2-shopping-list'

interface ShoppingIngredient { description: string; quantity: number }

interface ShoppingEntry {
  recipeId:        number
  recipeName:      string
  qty:             number
  primary_comp:    string | null
  primary_qty:     number | null
  secondary_comps: ShoppingIngredient[]
  fuel_comp:       string | null
  fuel_qty:        number | null
}

interface UpgradeRecipe {
  id:              number
  name:            string
  primary_comp:    string | null
  primary_qty:     number | null
  secondary_comps: ShoppingIngredient[]
  fuel_comp:       string | null
  fuel_qty:        number | null
}

// ── Spell table styles ────────────────────────────────────────────────────────

const SPELL_TH_CLS = 'py-1.5 px-2.5 text-[0.7rem] uppercase tracking-[0.05em] text-text-muted font-semibold whitespace-nowrap text-left'
const SPELL_TD_CLS = 'py-1.5 px-2.5 text-[0.88rem] whitespace-nowrap'

// ── Spell Raid Ready card ─────────────────────────────────────────────────────

function SpellRaidReady({ expertOrBetter, totalSpells }: { expertOrBetter: number; totalSpells: number }) {
  if (totalSpells === 0) return null
  const pct       = Math.min(100, Math.round(expertOrBetter / totalSpells * 100))
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
          <div
            className="font-heading text-[2rem] font-bold leading-none shrink-0 min-w-[3ch] text-center"
            style={{ color, textShadow: `0 0 20px ${color}55` }}
          >
            {pct}%
          </div>
          <div className="flex-1">
            <div className="text-[0.78rem] font-semibold mb-1" style={{ color: raidReady ? 'var(--success)' : 'var(--danger)' }}>
              {raidReady ? '✓ Raid Ready' : '✗ Not Ready'}
            </div>
            <div className="text-[0.68rem] text-text-muted leading-[1.5]">
              {expertOrBetter} / {totalSpells} at Expert+
            </div>
            <div className="text-[0.65rem] text-text-muted opacity-70">
              (90% required)
            </div>
          </div>
        </div>
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

// ── Spell progress bar ────────────────────────────────────────────────────────

function SpellProgressBar({ label, subtitle, value, total, pct, color }: {
  label:    string
  subtitle: string
  value:    number
  total:    number
  pct:      number
  color:    string
}) {
  const clamped = Math.min(100, pct)
  const done    = clamped >= 100
  return (
    <div className="pt-[5px] pb-[7px]">
      <div className="flex justify-between items-baseline mb-0.5">
        <span className="text-[0.78rem] font-semibold" style={{ color: done ? color : 'var(--text)' }}>{label}</span>
        <span className="text-[0.78rem] text-text-muted">{value}/{total}</span>
      </div>
      <div className="h-[6px] rounded-full bg-border overflow-hidden mb-0.5">
        <div className="h-full rounded-full [transition:width_0.3s]" style={{ width: `${clamped}%`, background: color }} />
      </div>
      <div className="flex justify-between items-baseline">
        <span className="text-[0.68rem] text-text-muted">{subtitle}</span>
        <span className="text-[0.75rem] font-bold" style={{ color: done ? color : 'var(--text-muted)' }}>
          {Math.round(pct)}%
        </span>
      </div>
    </div>
  )
}

// ── Upgrade materials section ─────────────────────────────────────────────────

const CAT_COLOUR: Record<string, string> = {
  primary:   'var(--gold)',   // gold  — the key component
  secondary: '#94a3b8',   // slate — stackable mats
  fuel:      '#64748b',   // muted — bulk fuel
}

// Tooltip width is fixed at 220px; we flip to the left side of the row when
// the right side would clip the viewport. Margin is 8px (ml-2 / mr-2).
const _INGREDIENT_TOOLTIP_W = 220
const _INGREDIENT_TOOLTIP_MARGIN = 8
const _INGREDIENT_TOOLTIP_VIEWPORT_PAD = 8

function IngredientTooltip({ ing }: { ing: Ingredient }) {
  const tierColour = itemRarityColor(ing.tier, 'var(--text)')
  // DOM-anchored position: default `left-full top-0 ml-2` puts the 220px box
  // to the right of the row. On narrow viewports (mobile) that clips the
  // right edge, so measure-and-flip to `right-full top-0 mr-2` (left side).
  // useLayoutEffect runs synchronously after mount so the flip happens before
  // paint — no visible jump on the first render.
  const ref = useRef<HTMLDivElement | null>(null)
  const [flipLeft, setFlipLeft] = useState(false)
  useLayoutEffect(() => {
    if (!ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const viewportRight = window.innerWidth - _INGREDIENT_TOOLTIP_VIEWPORT_PAD
    // Would the right edge clip? If yes, flip — but only if there's enough room
    // on the left side to fit the tooltip there (otherwise stay where we are).
    const wouldClipRight = rect.right > viewportRight
    const leftRoomAvailable =
      rect.left - _INGREDIENT_TOOLTIP_W - _INGREDIENT_TOOLTIP_MARGIN >= _INGREDIENT_TOOLTIP_VIEWPORT_PAD
    if (wouldClipRight && leftRoomAvailable) setFlipLeft(true)
  }, [])
  return (
    <div
      ref={ref}
      className={`absolute z-[9999] top-0 w-[220px] bg-surface border border-border rounded-md py-2.5 px-3 pointer-events-none ${
        flipLeft ? 'right-full mr-2' : 'left-full ml-2'
      }`}
      style={{ boxShadow: '0 4px 16px rgba(0,0,0,0.5)' }}
    >
      {/* Header: icon + name */}
      <div className="flex items-center gap-2 mb-1.5">
        {ing.icon_id ? (
          <img
            src={`/icons/${ing.icon_id}.png`}
            alt=""
            width={32} height={32}
            className="rounded-sm border border-border shrink-0"
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <div className="w-8 h-8 rounded-sm bg-border shrink-0" />
        )}
        <span className="text-[0.85rem] font-semibold leading-[1.3]" style={{ color: tierColour }}>
          {ing.name}
        </span>
      </div>
      {/* Tier badge */}
      {ing.tier && (
        <div className="text-[0.7rem]" style={{ color: tierColour, marginBottom: ing.description ? 4 : 0 }}>
          {ing.tier}
        </div>
      )}
      {/* Description */}
      {ing.description && (
        <div className="text-[0.72rem] text-text-muted leading-[1.4] max-h-20 overflow-hidden">
          {ing.description}
        </div>
      )}
    </div>
  )
}

function IngredientRow({ ing }: { ing: Ingredient }) {
  const [hovered, setHovered] = useState(false)
  const catColour = CAT_COLOUR[ing.category] ?? 'var(--text)'

  return (
    <div
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        className="flex items-center gap-[5px] py-[3px] border-b border-border"
        style={{ cursor: ing.item_id ? 'default' : undefined }}
      >
        {/* Icon */}
        <div className="w-5 h-5 shrink-0">
          {ing.icon_id ? (
            <img
              src={`/icons/${ing.icon_id}.png`}
              alt=""
              width={20} height={20}
              className="rounded-sm block"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <div className="w-5 h-5 rounded-sm bg-border" />
          )}
        </div>
        {/* Name */}
        <span className="text-[0.76rem] flex-1 overflow-hidden text-ellipsis whitespace-nowrap" style={{ color: catColour }}>
          {ing.name}
        </span>
        {/* Quantity */}
        <span className="text-[0.82rem] font-semibold shrink-0" style={{ color: catColour }}>
          {ing.quantity.toLocaleString()}
        </span>
      </div>
      {hovered && <IngredientTooltip ing={ing} />}
    </div>
  )
}

function MaterialsSection({ charName }: { charName: string }) {
  const cacheKey = charName.toLowerCase()
  const cached   = materialsCache.get(cacheKey)
  const navigate = useNavigate()

  const [data, setData]             = useState<UpgradeMaterialsData | null>(cached ?? null)
  const [loading, setLoading]       = useState(!cached)
  const [error, setError]           = useState<string | null>(null)
  const [addingToList, setAdding]   = useState(false)
  const [addError, setAddError]     = useState<string | null>(null)

  useEffect(() => {
    if (materialsCache.has(cacheKey)) return
    let cancelled = false
    fetch(`/api/character/${encodeURIComponent(charName)}/upgrade-materials`)
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<UpgradeMaterialsData>
      })
      .then(d => {
        if (cancelled) return
        materialsCache.set(cacheKey, d)
        setData(d)
        setLoading(false)
      })
      .catch(err => {
        if (!cancelled) {
          setError(String(err))
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [charName, cacheKey])

  if (loading) return (
    <StatGroup title="Upgrade Materials">
      <p className="text-[0.78rem] text-text-muted my-1">Loading…</p>
    </StatGroup>
  )
  if (error || !data) return null
  if (data.spells_needing_upgrade === 0) return (
    <StatGroup title="Upgrade Materials">
      <p className="text-[0.78rem] my-1" style={{ color: 'var(--success)' }}>
        All spells at Expert or better ✓
      </p>
    </StatGroup>
  )

  const missing = data.spells_needing_upgrade - data.spells_with_recipe

  async function handleAddToList() {
    setAdding(true)
    setAddError(null)
    try {
      const resp = await fetch(`/api/character/${encodeURIComponent(charName)}/upgrade-recipes`)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const json: { results: UpgradeRecipe[] } = await resp.json()
      const entries: ShoppingEntry[] = json.results.map(r => ({
        recipeId:        r.id,
        recipeName:      r.name,
        qty:             1,
        primary_comp:    r.primary_comp,
        primary_qty:     r.primary_qty,
        secondary_comps: r.secondary_comps,
        fuel_comp:       r.fuel_comp,
        fuel_qty:        r.fuel_qty,
      }))
      try { localStorage.setItem(SHOPPING_KEY, JSON.stringify(entries)) } catch { /* full */ }
      navigate('/recipes?list=open')
    } catch (err) {
      setAddError(String(err))
    } finally {
      setAdding(false)
    }
  }

  return (
    <StatGroup title="Upgrade Materials">
      <div className="text-[0.72rem] text-text-muted mb-1.5 leading-[1.4]">
        Expert recipes for{' '}
        <span className="text-text font-semibold">{data.spells_with_recipe}</span>
        {' '}of{' '}
        <span className="text-text font-semibold">{data.spells_needing_upgrade}</span>
        {' '}upgradeable spells
        {missing > 0 && <span style={{ color: '#f97316' }}> ({missing} no recipe)</span>}
      </div>

      {(() => {
        // Group by crafting tier: tier_num = floor(item_level / 10) + 1
        // Items with no item_level fall into tier 0 (shown last as "Unknown")
        const tierOf = (ing: Ingredient) =>
          ing.item_level != null ? Math.floor(ing.item_level / 10) + 1 : 0

        const groups = new Map<number, Ingredient[]>()
        for (const ing of data.ingredients) {
          const t = tierOf(ing)
          if (!groups.has(t)) groups.set(t, [])
          groups.get(t)!.push(ing)
        }

        // Sort groups: highest tier first; tier 0 (unknown) last
        const sortedTiers = [...groups.keys()].sort((a, b) =>
          a === 0 ? 1 : b === 0 ? -1 : b - a
        )

        return sortedTiers.map(t => (
          <div key={t} className="mb-1.5">
            <div className="text-[0.65rem] font-bold tracking-[0.07em] uppercase text-text-muted pt-0.5 pb-0.5 border-b border-border mb-px">
              {t === 0 ? 'Unknown' : `T${t}`}
            </div>
            {groups.get(t)!.map(ing => (
              <IngredientRow key={ing.name} ing={ing} />
            ))}
          </div>
        ))
      })()}

      {/* Add-to-shopping-list button */}
      {data.spells_with_recipe > 0 && (
        <div className="mt-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleAddToList}
            disabled={addingToList}
            className="w-full"
          >
            {addingToList ? '⏳ Adding…' : '🛒 Add upgrades to shopping list'}
          </Button>
          {addError && (
            <div className="text-[0.68rem] text-danger mt-1">
              Error: {addError}
            </div>
          )}
        </div>
      )}
    </StatGroup>
  )
}

// ── Spells tab ────────────────────────────────────────────────────────────────

type SpellsTabState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; data: CharacterSpellsData }

export function SpellsTab({ charName }: { charName: string }) {
  const cacheKey = charName.toLowerCase()
  const cached   = spellsCache.get(cacheKey)

  const [state, setState]         = useState<SpellsTabState>(
    cached ? { status: 'ok', data: cached } : { status: 'loading' }
  )
  const [search, setSearch]       = useState('')
  const [tierFilter, setTierFilter] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (spellsCache.has(cacheKey)) return
    let cancelled = false
    fetch(`/api/character/${encodeURIComponent(charName)}/spells`)
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<CharacterSpellsData>
      })
      .then(data => {
        if (cancelled) return
        spellsCache.set(cacheKey, data)
        setState({ status: 'ok', data })
      })
      .catch(err => { if (!cancelled) setState({ status: 'error', message: String(err) }) })
    return () => { cancelled = true }
  }, [charName, cacheKey])

  if (state.status === 'loading') {
    return <p className="mt-6 text-text-muted">Loading spell data…</p>
  }
  if (state.status === 'error') {
    return <p className="mt-6 text-danger">Error: {state.message}</p>
  }

  const { data } = state
  const totalSpells    = data.spells.length
  const expertOrBetter = (data.tier_counts['Expert'] ?? 0) + (data.tier_counts['Master'] ?? 0) + (data.tier_counts['Grandmaster'] ?? 0)
  const masterOrBetter = (data.tier_counts['Master'] ?? 0) + (data.tier_counts['Grandmaster'] ?? 0)
  const masteredPct    = totalSpells > 0 ? masterOrBetter / totalSpells * 100 : 0

  // Filter the list
  const q = search.trim().toLowerCase()
  const filtered = data.spells.filter(s => {
    if (tierFilter.size > 0 && !tierFilter.has(s.tier)) return false
    if (q) return s.name.toLowerCase().includes(q)
    return true
  })

  function toggleTier(tier: string) {
    setTierFilter(prev => {
      const next = new Set(prev)
      if (next.has(tier)) next.delete(tier)
      else next.add(tier)
      return next
    })
  }

  return (
    <div className="mt-4 flex flex-col md:flex-row gap-6 items-start">

      {/* ── Left sidebar ── */}
      <div className="w-full md:w-[240px] md:shrink-0">
        <SpellRaidReady expertOrBetter={expertOrBetter} totalSpells={totalSpells} />

        <StatGroup title="By Tier">
          {SPELL_TIER_ORDER.map(tier => {
            const count    = data.tier_counts[tier] ?? 0
            if (count === 0) return null
            const tc       = SPELL_TIER_COLOURS[tier]
            const isActive = tierFilter.has(tier)
            return (
              <div
                key={tier}
                onClick={() => toggleTier(tier)}
                className="flex justify-between items-baseline py-[3px] border-b border-border cursor-pointer [transition:opacity_0.12s]"
                style={{ opacity: tierFilter.size > 0 && !isActive ? 0.35 : 1 }}
              >
                <span className="text-[0.78rem]" style={{ color: tc?.text ?? 'var(--text)', fontWeight: isActive ? 700 : 400 }}>
                  {tier}
                </span>
                <span
                  className="text-[0.85rem] font-semibold rounded-sm px-1"
                  style={{
                    color: tc?.text ?? 'var(--text)',
                    background: isActive ? (tc?.bg ?? 'transparent') : 'transparent',
                  }}
                >
                  {count}
                </span>
              </div>
            )
          })}
          {/* Total row */}
          <div className="flex justify-between pt-1 pb-px mt-0.5">
            <span className="text-[0.75rem] text-text-muted uppercase tracking-[0.04em]">Total</span>
            <span className="text-[0.9rem] font-semibold">{totalSpells}</span>
          </div>
        </StatGroup>

        {/* Mastery progress */}
        <StatGroup title="Mastery">
          <SpellProgressBar
            label="Fully Mastered"
            subtitle="Master or better"
            value={masterOrBetter}
            total={totalSpells}
            pct={masteredPct}
            color="var(--success)"
          />
        </StatGroup>

        <MaterialsSection charName={charName} />

        {tierFilter.size > 0 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setTierFilter(new Set())}
            className="w-full mt-1"
          >
            Clear tier filter
          </Button>
        )}
      </div>

      {/* ── Right: spell list (2 columns) ── */}
      <div className="flex-1 min-w-0">
        <input
          type="text"
          placeholder="Search spells…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="mb-3 w-full md:w-[260px] box-border"
        />

        {filtered.length === 0 ? (
          <Card className="rounded-md p-6 text-text-muted text-center text-[0.88rem]">
            No spells match your filter.
          </Card>
        ) : (() => {
          const mid = Math.ceil(filtered.length / 2)
          const cols = [filtered.slice(0, mid), filtered.slice(mid)]

          const renderTable = (rows: SpellEntry[]) => (
            <Card className="rounded-md p-0 overflow-hidden flex-1 min-w-0">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b-2 border-border bg-surface-raised">
                    <th className={`${SPELL_TH_CLS} w-9 text-right`}>Lvl</th>
                    <th className={SPELL_TH_CLS}>Name</th>
                    <th className={`${SPELL_TH_CLS} text-right pr-2`}>Tier</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s, i) => (
                    <tr key={i} className="border-b border-border">
                      <td className={`${SPELL_TD_CLS} text-right text-text-muted text-[0.8rem] w-9`}>
                        {s.level}
                      </td>
                      <td className={`${SPELL_TD_CLS} font-medium`}>
                        <div className="flex items-center gap-[5px]">
                          {(s.icon_id != null || s.icon_backdrop != null) && (
                            <div className="relative w-[18px] h-[18px] shrink-0">
                              {s.icon_backdrop != null && s.icon_backdrop > 0 && (
                                <img
                                  src={`/spell-icons/${s.icon_backdrop}.png`}
                                  alt=""
                                  className="absolute inset-0 w-[18px] h-[18px]"
                                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                                />
                              )}
                              {s.icon_id != null && s.icon_id > 0 && (
                                <img
                                  src={`/spell-icons/${s.icon_id}.png`}
                                  alt=""
                                  className="absolute inset-0 w-[18px] h-[18px]"
                                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                                />
                              )}
                            </div>
                          )}
                          <span className="text-[0.82rem]">{s.name}</span>
                        </div>
                      </td>
                      <td className={`${SPELL_TD_CLS} text-right pr-2`}>
                        <div className="flex items-center justify-end gap-px">
                          {SPELL_TIER_ORDER.map(t => {
                            const base = SPELL_TIER_ICON[t]
                            const filename = t === s.tier ? `${base}-lit.png` : `${base}.png`
                            return (
                              <SpellTierPip
                                key={t}
                                src={`/spell-icons/${filename}`}
                                tier={t}
                                spellName={s.name}
                              />
                            )
                          })}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )

          return (
            <div className="flex flex-col md:flex-row gap-3">
              {cols.map((col, ci) => <React.Fragment key={ci}>{renderTable(col)}</React.Fragment>)}
            </div>
          )
        })()}
      </div>
    </div>
  )
}
