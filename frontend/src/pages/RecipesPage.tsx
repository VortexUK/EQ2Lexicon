import type { ReactNode } from 'react'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, Card } from '../components/ui'
import { recipeTierColor } from '../rarityColors'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Ingredient {
  description: string
  quantity: number
}

interface RecipeResult {
  id: number
  name: string
  bench: string | null
  bench_label: string | null
  craft_tier: string | null      // T1 … T14
  crafted_tier: string | null    // spell-scroll quality (Expert, etc.)
  primary_comp: string | null
  primary_qty: number | null
  secondary_comps: Ingredient[]
  fuel_comp: string | null
  fuel_qty: number | null
  out_formed_id: number | null
  out_formed_count: number | null
  class_label: string | null
}

interface RecipeSearchResponse {
  results: RecipeResult[]
  total: number
  page: number
  per_page: number
}

interface ShoppingEntry {
  recipeId: number
  recipeName: string
  qty: number              // number of crafting runs
  primary_comp: string | null
  primary_qty: number | null
  secondary_comps: Ingredient[]
  fuel_comp: string | null
  fuel_qty: number | null
}

// ── Constants ─────────────────────────────────────────────────────────────────

// T1 = levels 1-9, T2 = 10-19, … derived from the fuel component prefix
const CRAFT_TIERS = [
  'T1','T2','T3','T4','T5','T6','T7','T8','T9','T10','T11','T12','T13','T14',
]

const CRAFT_TIER_LABELS: Record<string, string> = {
  T1: 'T1  (1–9)',   T2: 'T2  (10–19)', T3: 'T3  (20–29)',
  T4: 'T4  (30–39)', T5: 'T5  (40–49)', T6: 'T6  (50–59)',
  T7: 'T7  (60–69)', T8: 'T8  (70–79)', T9: 'T9  (80–89)',
  T10:'T10 (90–99)', T11:'T11 (100+)',   T12:'T12',
  T13:'T13',         T14:'T14',
}

// Tradeskill class filter — driven by the recipe_classes mapping (a recipe can
// belong to more than one class). Values are the class display names stored in
// recipe_classes. Replaces the old bench-based filter, which merged Armorer +
// Weaponsmith (shared forge) and had no Jeweler.
const CRAFT_CLASS_OPTIONS = [
  { key: '',            label: 'All Artisans' },
  { key: 'Alchemist',   label: 'Alchemist' },
  { key: 'Armorer',     label: 'Armorer' },
  { key: 'Carpenter',   label: 'Carpenter' },
  { key: 'Jeweler',     label: 'Jeweler' },
  { key: 'Provisioner', label: 'Provisioner' },
  { key: 'Sage',        label: 'Sage' },
  { key: 'Tailor',      label: 'Tailor' },
  { key: 'Weaponsmith', label: 'Weaponsmith' },
  { key: 'Woodworker',  label: 'Woodworker' },
]

const CLASS_OPTIONS: { label: string; value: string }[] = [
  { label: 'All Classes',    value: '' },
  { label: '── Fighter ──',  value: '__hdr' },
  { label: '  Guardian',     value: 'guardian' },
  { label: '  Berserker',    value: 'berserker' },
  { label: '  Monk',         value: 'monk' },
  { label: '  Bruiser',      value: 'bruiser' },
  { label: '  Shadowknight', value: 'shadowknight' },
  { label: '  Paladin',      value: 'paladin' },
  { label: '── Priest ──',   value: '__hdr' },
  { label: '  Templar',      value: 'templar' },
  { label: '  Inquisitor',   value: 'inquisitor' },
  { label: '  Warden',       value: 'warden' },
  { label: '  Fury',         value: 'fury' },
  { label: '  Mystic',       value: 'mystic' },
  { label: '  Defiler',      value: 'defiler' },
  { label: '  Channeler',    value: 'channeler' },
  { label: '── Mage ──',     value: '__hdr' },
  { label: '  Wizard',       value: 'wizard' },
  { label: '  Warlock',      value: 'warlock' },
  { label: '  Illusionist',  value: 'illusionist' },
  { label: '  Coercer',      value: 'coercer' },
  { label: '  Conjuror',     value: 'conjuror' },
  { label: '  Necromancer',  value: 'necromancer' },
  { label: '── Scout ──',    value: '__hdr' },
  { label: '  Swashbuckler', value: 'swashbuckler' },
  { label: '  Brigand',      value: 'brigand' },
  { label: '  Troubador',    value: 'troubador' },
  { label: '  Dirge',        value: 'dirge' },
  { label: '  Ranger',       value: 'ranger' },
  { label: '  Assassin',     value: 'assassin' },
  { label: '  Beastlord',    value: 'beastlord' },
]

const CTRL_CLS = 'bg-surface border border-border rounded-[6px] text-text text-[0.88rem] py-[0.35rem] px-[0.6rem] outline-none w-full box-border'

const STORAGE_KEY = 'eq2-shopping-list'

// ── XML download ──────────────────────────────────────────────────────────────

function _xmlEsc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function downloadShoppingListXml(list: ShoppingEntry[], summary: IngredientSummary): void {
  const lines: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<shoppinglist>',
    '  <spells>',
    ...list.map(e => `    <spell Name="${_xmlEsc(e.recipeName)}">${e.qty}</spell>`),
    '  </spells>',
    '  <materials>',
    ...summary.regular.map(m => `    <material Name="${_xmlEsc(m.name)}">${m.total}</material>`),
    '  </materials>',
    '  <fuels>',
    ...summary.fuel.map(f => `    <fuel Name="${_xmlEsc(f.name)}">${f.total}</fuel>`),
    '  </fuels>',
    '</shoppinglist>',
  ]
  const blob = new Blob([lines.join('\n')], { type: 'application/xml' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = 'shopping-list.xml'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── localStorage helpers ───────────────────────────────────────────────────────

function loadList(): ShoppingEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveList(list: ShoppingEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
  } catch { /* storage full / private browsing */ }
}

// ── Ingredient aggregation ────────────────────────────────────────────────────

interface IngredientSummary {
  regular: { name: string; total: number }[]
  fuel:    { name: string; total: number }[]
}

function aggregateList(list: ShoppingEntry[]): IngredientSummary {
  const regular: Record<string, number> = {}
  const fuel:    Record<string, number> = {}

  for (const entry of list) {
    const n = entry.qty
    if (entry.primary_comp && entry.primary_qty) {
      regular[entry.primary_comp] = (regular[entry.primary_comp] ?? 0) + entry.primary_qty * n
    }
    for (const sc of entry.secondary_comps) {
      if (sc.description && sc.quantity) {
        regular[sc.description] = (regular[sc.description] ?? 0) + sc.quantity * n
      }
    }
    if (entry.fuel_comp && entry.fuel_qty) {
      fuel[entry.fuel_comp] = (fuel[entry.fuel_comp] ?? 0) + entry.fuel_qty * n
    }
  }

  return {
    regular: Object.entries(regular)
      .map(([name, total]) => ({ name, total }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    fuel: Object.entries(fuel)
      .map(([name, total]) => ({ name, total }))
      .sort((a, b) => a.name.localeCompare(b.name)),
  }
}

// ── Tier badge colour ─────────────────────────────────────────────────────────

// ── Sub-components ────────────────────────────────────────────────────────────

function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return null
  const colour = recipeTierColor(tier)
  return (
    <span
      className="text-[0.72rem] font-semibold border rounded-sm px-[5px] py-px leading-none whitespace-nowrap"
      style={{ color: colour, borderColor: colour }}
    >
      {tier}
    </span>
  )
}

function IngredientList({ comps, compact }: { comps: Ingredient[]; compact?: boolean }) {
  if (!comps.length) return null
  return (
    <ul className="m-0 pl-[1.1rem] list-disc">
      {comps.map((c, i) => (
        <li key={i} className="text-text-muted leading-[1.5]" style={{ fontSize: compact ? '0.78rem' : '0.83rem' }}>
          {c.description} ×{c.quantity}
        </li>
      ))}
    </ul>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RecipesPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // ── Filter state (URL-synced) ────────────────────────────────────────────────
  const [q,         setQ]         = useState(searchParams.get('q')     ?? '')
  const [tier,      setTier]      = useState(searchParams.get('tier')   ?? '')
  const [craftClass, setCraftClass] = useState(searchParams.get('craft_class') ?? '')
  const [className, setClassName] = useState(searchParams.get('cls')    ?? '')

  // ── Results state ────────────────────────────────────────────────────────────
  const [results,  setResults]  = useState<RecipeResult[]>([])
  const [total,    setTotal]    = useState(0)
  const [page,     setPage]     = useState(1)
  const [perPage,  setPerPage]  = useState(25)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  // ── Shopping list state (localStorage) ───────────────────────────────────────
  const [list,     setList]     = useState<ShoppingEntry[]>(() => loadList())
  const [listOpen, setListOpen] = useState(() => searchParams.get('list') === 'open')
  const [showMats, setShowMats] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem('eq2-shopping-show-mats')
      return raw === null ? true : raw === 'true'
    } catch {
      return true
    }
  })

  // ── URL sync ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const p: Record<string, string> = {}
    if (q)          p.q           = q
    if (tier)       p.tier        = tier
    if (craftClass) p.craft_class = craftClass
    if (className)  p.cls         = className
    setSearchParams(p, { replace: true })
  }, [q, tier, craftClass, className, setSearchParams])

  // ── Persist shopping list ─────────────────────────────────────────────────────
  useEffect(() => { saveList(list) }, [list])
  useEffect(() => {
    try { localStorage.setItem('eq2-shopping-show-mats', String(showMats)) } catch { /* ignore */ }
  }, [showMats])

  // ── Auto-search on mount if URL has params ───────────────────────────────────
  const didAutoSearch = useRef(false)
  useEffect(() => {
    if (didAutoSearch.current) return
    if (searchParams.get('q') || searchParams.get('tier') || searchParams.get('craft_class') || searchParams.get('cls')) {
      didAutoSearch.current = true
      doSearch(1, {
        q:          searchParams.get('q')           ?? '',
        tier:       searchParams.get('tier')        ?? '',
        craftClass: searchParams.get('craft_class') ?? '',
        className:  searchParams.get('cls')         ?? '',
      })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Search function ───────────────────────────────────────────────────────────
  const doSearch = useCallback(async (
    p: number,
    overrides?: { q?: string; tier?: string; craftClass?: string; className?: string }
  ) => {
    const fq     = overrides?.q          ?? q
    const ftier  = overrides?.tier       ?? tier
    const fcraft = overrides?.craftClass ?? craftClass
    const fcls   = overrides?.className   ?? className

    const params = new URLSearchParams()
    if (fq)     params.set('q',    fq)
    if (ftier)  params.set('tier', ftier)
    if (fcraft) params.set('craft_class', fcraft)
    if (fcls)   params.set('class_name', fcls)
    params.set('page', String(p))

    if (!fq && !ftier && !fcraft && !fcls) return

    setLoading(true)
    setError(null)
    try {
      const resp = await fetch(`/api/recipes/search?${params}`, { credentials: 'include' })
      if (!resp.ok) throw new Error(`Server error ${resp.status}`)
      const data: RecipeSearchResponse = await resp.json()
      setResults(data.results)
      setTotal(data.total)
      setPage(data.page)
      setPerPage(data.per_page)
      setSearched(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [q, tier, craftClass, className])

  const handleSearch = useCallback(() => { doSearch(1) }, [doSearch])
  const handlePage   = useCallback((p: number) => { doSearch(p) }, [doSearch])

  // ── Shopping list helpers ─────────────────────────────────────────────────────
  const addToList = useCallback((recipe: RecipeResult) => {
    setList(prev => {
      const idx = prev.findIndex(e => e.recipeId === recipe.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = { ...next[idx], qty: next[idx].qty + 1 }
        return next
      }
      return [...prev, {
        recipeId:       recipe.id,
        recipeName:     recipe.name,
        qty:            1,
        primary_comp:   recipe.primary_comp,
        primary_qty:    recipe.primary_qty,
        secondary_comps:recipe.secondary_comps,
        fuel_comp:      recipe.fuel_comp,
        fuel_qty:       recipe.fuel_qty,
      }]
    })
  }, [])

  const changeQty = useCallback((recipeId: number, delta: number) => {
    setList(prev => {
      const idx = prev.findIndex(e => e.recipeId === recipeId)
      if (idx < 0) return prev
      const newQty = prev[idx].qty + delta
      if (newQty <= 0) {
        return prev.filter((_, i) => i !== idx)
      }
      const next = [...prev]
      next[idx] = { ...next[idx], qty: newQty }
      return next
    })
  }, [])

  const clearList = useCallback(() => { setList([]) }, [])

  const summary = aggregateList(list)
  const totalItems = list.reduce((s, e) => s + e.qty, 0)

  const totalPages = Math.ceil(total / perPage)

  return (
    <main className="max-w-[1100px] mx-auto px-4 py-6">
      <div className="flex items-baseline gap-4 mb-[1.2rem]">
        <h1 className="font-heading text-[1.7rem] text-gold m-0">
          Recipes
        </h1>
        {list.length > 0 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setListOpen(v => !v)}
            className="ml-auto"
          >
            🛒 Shopping List
            <span className="bg-gold text-bg rounded-[50%] w-[18px] h-[18px] inline-flex items-center justify-center text-[0.72rem] font-bold leading-none">
              {totalItems}
            </span>
          </Button>
        )}
      </div>

      <div className="grid gap-5" style={{ gridTemplateColumns: listOpen ? '1fr 340px' : '1fr' }}>

        {/* ── Left column: filters + results ─────────────────────────────────── */}
        <div>
          {/* Filter row */}
          <div className="grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-[0.6rem] items-end mb-4">
            {/* Name */}
            <div>
              <label className="text-xs text-text-muted block mb-[3px]">
                Recipe name
              </label>
              <input
                className={CTRL_CLS}
                placeholder="Search…"
                value={q}
                onChange={e => setQ(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
              />
            </div>

            {/* Tier */}
            <div>
              <label className="text-xs text-text-muted block mb-[3px]">
                Crafting tier
              </label>
              <select className={CTRL_CLS} value={tier} onChange={e => setTier(e.target.value)}>
                <option value="">All tiers</option>
                {CRAFT_TIERS.map(t => (
                  <option key={t} value={t}>{CRAFT_TIER_LABELS[t] ?? t}</option>
                ))}
              </select>
            </div>

            {/* Tradeskill class */}
            <div>
              <label className="text-xs text-text-muted block mb-[3px]">
                Artisan class
              </label>
              <select className={CTRL_CLS} value={craftClass} onChange={e => setCraftClass(e.target.value)}>
                {CRAFT_CLASS_OPTIONS.map(c => (
                  <option key={c.key} value={c.key}>{c.label}</option>
                ))}
              </select>
            </div>

            {/* Adventure class */}
            <div>
              <label className="text-xs text-text-muted block mb-[3px]">
                Adventure class
              </label>
              <select
                className={CTRL_CLS}
                value={className}
                onChange={e => setClassName(e.target.value)}
              >
                {CLASS_OPTIONS.map((opt, i) =>
                  opt.value === '__hdr'
                    ? <option key={i} disabled className="text-text-muted">{opt.label}</option>
                    : <option key={i} value={opt.value}>{opt.label}</option>
                )}
              </select>
            </div>

            {/* Search button */}
            <Button
              variant="primary"
              onClick={handleSearch}
              disabled={loading}
            >
              {loading ? '…' : 'Search'}
            </Button>
          </div>

          {/* Error */}
          {error && (
            <p className="text-danger text-[0.9rem] mt-0 mx-0 mb-3">{error}</p>
          )}

          {/* Results */}
          {searched && !loading && (
            <p className="text-[0.8rem] text-text-muted mt-0 mx-0 mb-[0.6rem]">
              {total === 0 ? 'No results.' : `${total.toLocaleString()} recipe${total !== 1 ? 's' : ''} found`}
            </p>
          )}

          {results.length > 0 && (
            <>
              <div className="flex flex-col gap-2">
                {results.map(recipe => (
                  <RecipeCard
                    key={recipe.id}
                    recipe={recipe}
                    onAdd={() => addToList(recipe)}
                    inList={list.find(e => e.recipeId === recipe.id)?.qty ?? 0}
                    onDec={() => changeQty(recipe.id, -1)}
                  />
                ))}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex gap-[0.4rem] items-center mt-4 flex-wrap">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handlePage(page - 1)}
                    disabled={page <= 1}
                  >← Prev</Button>
                  <span className="text-[0.82rem] text-text-muted px-[0.3rem]">
                    Page {page} / {totalPages}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handlePage(page + 1)}
                    disabled={page >= totalPages}
                  >Next →</Button>
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Right column: shopping list ─────────────────────────────────────── */}
        {listOpen && (
          <Card className="p-4 self-start sticky top-16 max-h-[calc(100vh-5rem)] overflow-y-auto">
            <div className="flex justify-between items-center mb-[0.8rem]">
              <h2 className="m-0 text-base font-heading text-gold">
                Shopping List
              </h2>
              <div className="flex gap-2 items-center">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => downloadShoppingListXml(list, summary)}
                  title="Download as XML"
                  className="bg-none text-gold"
                >
                  ⬇ XML
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={clearList}
                  title="Clear list"
                  className="border-none"
                >
                  Clear
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setListOpen(false)}
                  className="text-base leading-none"
                >
                  ×
                </Button>
              </div>
            </div>

            {/* View options */}
            {list.length > 0 && (
              <label className="flex items-center gap-[0.4rem] text-[0.76rem] text-text-muted cursor-pointer mb-[0.6rem] select-none">
                <input
                  type="checkbox"
                  checked={showMats}
                  onChange={e => setShowMats(e.target.checked)}
                  className="cursor-pointer accent-gold"
                />
                Show materials per spell
              </label>
            )}

            {/* List entries */}
            {list.map(entry => (
              <div key={entry.recipeId} className="border-b border-border pb-[0.55rem] mb-[0.55rem]">
                <div className="flex items-center gap-2" style={{ marginBottom: showMats ? '0.2rem' : 0 }}>
                  <span className="text-[0.85rem] flex-1 leading-[1.3]">{entry.recipeName}</span>
                  <div className="flex items-center gap-1 shrink-0">
                    <QtyBtn onClick={() => changeQty(entry.recipeId, -1)}>−</QtyBtn>
                    <span className="text-[0.82rem] min-w-[20px] text-center text-gold font-semibold">
                      {entry.qty}
                    </span>
                    <QtyBtn onClick={() => changeQty(entry.recipeId, +1)}>+</QtyBtn>
                  </div>
                </div>
                {showMats && <IngredientList comps={buildIngredientList(entry)} compact />}
              </div>
            ))}

            {/* Ingredient Summary */}
            {list.length > 0 && (
              <div className="mt-2">
                <h3 className="text-[0.82rem] text-text-muted mt-0 mx-0 mb-[0.4rem] uppercase tracking-[0.06em]">
                  Ingredient Summary
                </h3>

                {summary.regular.length > 0 && (
                  <>
                    <p className="text-[0.72rem] text-text-muted mt-[0.4rem] mx-0 mb-[0.2rem] font-semibold">Materials</p>
                    {summary.regular.map(row => (
                      <div key={row.name} className="flex justify-between text-[0.8rem] py-px px-0">
                        <span className="text-text">{row.name}</span>
                        <span className="text-gold font-semibold ml-2">×{row.total}</span>
                      </div>
                    ))}
                  </>
                )}

                {summary.fuel.length > 0 && (
                  <>
                    <p className="text-[0.72rem] text-text-muted mt-[0.7rem] mx-0 mb-[0.2rem] font-semibold">
                      Fuel
                    </p>
                    {summary.fuel.map(row => (
                      <div key={row.name} className="flex justify-between text-[0.8rem] py-px px-0">
                        <span className="text-text-muted">{row.name}</span>
                        <span className="text-gold-dim font-semibold ml-2">×{row.total}</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}

            {list.length === 0 && (
              <p className="text-[0.83rem] text-text-muted text-center mt-4">
                Use + on a recipe to add it.
              </p>
            )}
          </Card>
        )}
      </div>

      {/* Floating cart button when panel is closed */}
      {!listOpen && list.length > 0 && (
        <Button
          variant="primary"
          onClick={() => setListOpen(true)}
          title="Open Shopping List"
          className="fixed bottom-6 right-6 rounded-[50%] w-[52px] h-[52px] text-[1.4rem] shadow-[0_4px_16px_rgba(0,0,0,0.5)] z-[100]"
        >
          🛒
          <span className="absolute -top-1 -right-1 bg-danger text-white rounded-[50%] w-5 h-5 flex items-center justify-center text-[0.65rem] font-bold">
            {totalItems}
          </span>
        </Button>
      )}
    </main>
  )
}

// ── RecipeCard ────────────────────────────────────────────────────────────────

function RecipeCard({
  recipe,
  onAdd,
  inList,
  onDec,
}: {
  recipe:  RecipeResult
  onAdd:   () => void
  inList:  number
  onDec:   () => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <Card className="p-0 overflow-hidden">
      {/* Header row */}
      <div
        className="flex items-center gap-[0.6rem] px-3 py-[0.55rem] cursor-pointer select-none"
        onClick={() => setOpen(v => !v)}
      >
        <span
          className="text-[0.7rem] text-text-muted inline-block transition-transform duration-150"
          style={{ transform: `rotate(${open ? 90 : 0}deg)` }}
        >
          ▶
        </span>
        <span className="flex-1 text-[0.92rem] font-medium">{recipe.name}</span>
        {recipe.craft_tier && (
          <span className="text-[0.72rem] text-text-muted whitespace-nowrap">
            {recipe.craft_tier}
          </span>
        )}
        {recipe.crafted_tier && <TierBadge tier={recipe.crafted_tier} />}
        {recipe.bench_label && (
          <span className="text-[0.72rem] text-text-muted whitespace-nowrap">
            {recipe.bench_label}
          </span>
        )}
        {recipe.class_label && (
          <span className="text-[0.72rem] text-rarity-treasured whitespace-nowrap">
            {recipe.class_label}
          </span>
        )}
        {/* +/- controls */}
        <div
          onClick={e => e.stopPropagation()}
          className="flex items-center gap-1 shrink-0"
        >
          {inList > 0 && (
            <>
              <QtyBtn onClick={onDec}>−</QtyBtn>
              <span className="text-[0.82rem] min-w-[20px] text-center text-gold font-semibold">
                {inList}
              </span>
            </>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onAdd}
            title="Add to shopping list"
            className="border border-gold text-gold"
          >
            +
          </Button>
        </div>
      </div>

      {/* Expandable ingredient detail */}
      {open && (
        <div className="border-t border-border pt-[0.6rem] px-3 pb-3 grid grid-cols-2 gap-x-4 gap-y-2">
          {/* Materials */}
          <div>
            <p className="mt-0 mx-0 mb-1 text-[0.72rem] text-text-muted font-semibold uppercase tracking-[0.05em]">
              Materials
            </p>
            {recipe.primary_comp && (
              <div className="text-[0.83rem] text-text">
                {recipe.primary_comp} ×{recipe.primary_qty ?? 1}
              </div>
            )}
            {recipe.secondary_comps.map((sc, i) => (
              <div key={i} className="text-[0.83rem] text-text">
                {sc.description} ×{sc.quantity}
              </div>
            ))}
            {!recipe.primary_comp && !recipe.secondary_comps.length && (
              <span className="text-[0.8rem] text-text-muted">—</span>
            )}
          </div>

          {/* Fuel */}
          <div>
            <p className="mt-0 mx-0 mb-1 text-[0.72rem] text-text-muted font-semibold uppercase tracking-[0.05em]">
              Fuel
            </p>
            {recipe.fuel_comp ? (
              <div className="text-[0.83rem] text-text-muted">
                {recipe.fuel_comp} ×{recipe.fuel_qty ?? 1}
              </div>
            ) : (
              <span className="text-[0.8rem] text-text-muted">—</span>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}

// ── QtyBtn ────────────────────────────────────────────────────────────────────

function QtyBtn({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: 'none',
        border: '1px solid var(--border)',
        borderRadius: 4,
        color: 'var(--text)',
        fontSize: '0.8rem',
        width: 20,
        height: 20,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        lineHeight: 1,
        padding: 0,
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildIngredientList(entry: ShoppingEntry): Ingredient[] {
  const result: Ingredient[] = []
  if (entry.primary_comp && entry.primary_qty) {
    result.push({ description: entry.primary_comp, quantity: entry.primary_qty * entry.qty })
  }
  for (const sc of entry.secondary_comps) {
    if (sc.description && sc.quantity) {
      result.push({ description: sc.description, quantity: sc.quantity * entry.qty })
    }
  }
  return result
}
