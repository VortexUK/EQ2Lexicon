import type { CSSProperties, ReactNode } from 'react'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import BackLink from '../components/BackLink'

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

const BENCH_OPTIONS = [
  { key: '',                  label: 'All Artisans' },
  { key: 'work_bench',        label: 'Carpenter' },
  { key: 'work_desk',         label: 'Sage' },
  { key: 'chemistry_table',   label: 'Alchemist' },
  { key: 'forge',             label: 'Armorer / Weaponsmith' },
  { key: 'woodworking_table', label: 'Woodworker' },
  { key: 'sewing_table',      label: 'Tailor' },
  { key: 'stove and keg',     label: 'Provisioner' },
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

const CTRL: CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  color: 'var(--text)',
  fontSize: '0.88rem',
  padding: '0.35rem 0.6rem',
  outline: 'none',
  width: '100%',
  boxSizing: 'border-box',
}

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

const TIER_COLOUR: Record<string, string> = {
  Apprentice:   '#9a9a9a',
  Journeyman:   '#beff93',
  Adept:        '#93d9ff',
  Expert:       '#ffc993',
  Master:       '#ff99ff',
  Grandmaster:  '#ffe566',
  Ancient:      '#ff6666',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return null
  const colour = TIER_COLOUR[tier] ?? 'var(--text-muted)'
  return (
    <span style={{
      fontSize: '0.72rem',
      fontWeight: 600,
      color: colour,
      border: `1px solid ${colour}`,
      borderRadius: 4,
      padding: '1px 5px',
      lineHeight: 1,
      whiteSpace: 'nowrap',
    }}>
      {tier}
    </span>
  )
}

function IngredientList({ comps, compact }: { comps: Ingredient[]; compact?: boolean }) {
  if (!comps.length) return null
  return (
    <ul style={{ margin: 0, padding: '0 0 0 1.1rem', listStyle: 'disc' }}>
      {comps.map((c, i) => (
        <li key={i} style={{ fontSize: compact ? '0.78rem' : '0.83rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
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
  const [bench,     setBench]     = useState(searchParams.get('bench')  ?? '')
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

  // ── URL sync ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const p: Record<string, string> = {}
    if (q)         p.q     = q
    if (tier)      p.tier  = tier
    if (bench)     p.bench = bench
    if (className) p.cls   = className
    setSearchParams(p, { replace: true })
  }, [q, tier, bench, className, setSearchParams])

  // ── Persist shopping list ─────────────────────────────────────────────────────
  useEffect(() => { saveList(list) }, [list])

  // ── Auto-search on mount if URL has params ───────────────────────────────────
  const didAutoSearch = useRef(false)
  useEffect(() => {
    if (didAutoSearch.current) return
    if (searchParams.get('q') || searchParams.get('tier') || searchParams.get('bench') || searchParams.get('cls')) {
      didAutoSearch.current = true
      doSearch(1, {
        q:         searchParams.get('q')     ?? '',
        tier:      searchParams.get('tier')   ?? '',
        bench:     searchParams.get('bench')  ?? '',
        className: searchParams.get('cls')    ?? '',
      })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Search function ───────────────────────────────────────────────────────────
  const doSearch = useCallback(async (
    p: number,
    overrides?: { q?: string; tier?: string; bench?: string; className?: string }
  ) => {
    const fq    = overrides?.q         ?? q
    const ftier = overrides?.tier      ?? tier
    const fbench= overrides?.bench     ?? bench
    const fcls  = overrides?.className ?? className

    const params = new URLSearchParams()
    if (fq)    params.set('q',    fq)
    if (ftier) params.set('tier', ftier)
    if (fbench)params.set('bench',fbench)
    if (fcls)  params.set('class_name', fcls)
    params.set('page', String(p))

    if (!fq && !ftier && !fbench && !fcls) return

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
  }, [q, tier, bench, className])

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
    <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
      <BackLink />
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '1rem', marginBottom: '1.2rem' }}>
        <h1 style={{ fontFamily: "'Cinzel', serif", fontSize: '1.7rem', color: '#c8a96e', margin: 0 }}>
          Recipes
        </h1>
        {list.length > 0 && (
          <button
            onClick={() => setListOpen(v => !v)}
            style={{
              marginLeft: 'auto',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              color: '#c8a96e',
              fontSize: '0.85rem',
              padding: '0.3rem 0.8rem',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
            }}
          >
            🛒 Shopping List
            <span style={{
              background: '#c8a96e',
              color: '#0f1117',
              borderRadius: '50%',
              width: 18,
              height: 18,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '0.72rem',
              fontWeight: 700,
              lineHeight: 1,
            }}>
              {totalItems}
            </span>
          </button>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: listOpen ? '1fr 340px' : '1fr', gap: '1.25rem' }}>

        {/* ── Left column: filters + results ─────────────────────────────────── */}
        <div>
          {/* Filter row */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr 1fr auto',
            gap: '0.6rem',
            alignItems: 'end',
            marginBottom: '1rem',
          }}>
            {/* Name */}
            <div>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>
                Recipe name
              </label>
              <input
                style={CTRL}
                placeholder="Search…"
                value={q}
                onChange={e => setQ(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSearch()}
              />
            </div>

            {/* Tier */}
            <div>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>
                Crafting tier
              </label>
              <select style={CTRL} value={tier} onChange={e => setTier(e.target.value)}>
                <option value="">All tiers</option>
                {CRAFT_TIERS.map(t => (
                  <option key={t} value={t}>{CRAFT_TIER_LABELS[t] ?? t}</option>
                ))}
              </select>
            </div>

            {/* Bench */}
            <div>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>
                Artisan class
              </label>
              <select style={CTRL} value={bench} onChange={e => setBench(e.target.value)}>
                {BENCH_OPTIONS.map(b => (
                  <option key={b.key} value={b.key}>{b.label}</option>
                ))}
              </select>
            </div>

            {/* Adventure class */}
            <div>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>
                Adventure class
              </label>
              <select
                style={CTRL}
                value={className}
                onChange={e => setClassName(e.target.value)}
              >
                {CLASS_OPTIONS.map((opt, i) =>
                  opt.value === '__hdr'
                    ? <option key={i} disabled style={{ color: 'var(--text-muted)' }}>{opt.label}</option>
                    : <option key={i} value={opt.value}>{opt.label}</option>
                )}
              </select>
            </div>

            {/* Search button */}
            <button
              onClick={handleSearch}
              disabled={loading}
              style={{
                background: '#c8a96e',
                color: '#0f1117',
                border: 'none',
                borderRadius: 6,
                fontWeight: 700,
                fontSize: '0.88rem',
                padding: '0.38rem 1.1rem',
                cursor: loading ? 'wait' : 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {loading ? '…' : 'Search'}
            </button>
          </div>

          {/* Error */}
          {error && (
            <p style={{ color: '#f87171', fontSize: '0.9rem', margin: '0 0 0.75rem' }}>{error}</p>
          )}

          {/* Results */}
          {searched && !loading && (
            <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: '0 0 0.6rem' }}>
              {total === 0 ? 'No results.' : `${total.toLocaleString()} recipe${total !== 1 ? 's' : ''} found`}
            </p>
          )}

          {results.length > 0 && (
            <>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
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
                <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', marginTop: '1rem', flexWrap: 'wrap' }}>
                  <button
                    onClick={() => handlePage(page - 1)}
                    disabled={page <= 1}
                    style={pagerBtn(page <= 1)}
                  >← Prev</button>
                  <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)', padding: '0 0.3rem' }}>
                    Page {page} / {totalPages}
                  </span>
                  <button
                    onClick={() => handlePage(page + 1)}
                    disabled={page >= totalPages}
                    style={pagerBtn(page >= totalPages)}
                  >Next →</button>
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Right column: shopping list ─────────────────────────────────────── */}
        {listOpen && (
          <div style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '1rem',
            alignSelf: 'start',
            position: 'sticky',
            top: '4rem',
            maxHeight: 'calc(100vh - 5rem)',
            overflowY: 'auto',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.8rem' }}>
              <h2 style={{ margin: 0, fontSize: '1rem', fontFamily: "'Cinzel', serif", color: '#c8a96e' }}>
                Shopping List
              </h2>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <button
                  onClick={() => downloadShoppingListXml(list, summary)}
                  title="Download as XML"
                  style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 4, color: '#c8a96e', cursor: 'pointer', fontSize: '0.75rem', padding: '2px 7px', lineHeight: 1.5 }}
                >
                  ⬇ XML
                </button>
                <button
                  onClick={clearList}
                  title="Clear list"
                  style={{ background: 'none', border: 'none', color: '#f87171', cursor: 'pointer', fontSize: '0.8rem', padding: '0 0.2rem' }}
                >
                  Clear
                </button>
                <button
                  onClick={() => setListOpen(false)}
                  style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '1rem', lineHeight: 1 }}
                >
                  ×
                </button>
              </div>
            </div>

            {/* List entries */}
            {list.map(entry => (
              <div key={entry.recipeId} style={{
                borderBottom: '1px solid var(--border)',
                paddingBottom: '0.55rem',
                marginBottom: '0.55rem',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem' }}>
                  <span style={{ fontSize: '0.85rem', flex: 1, lineHeight: 1.3 }}>{entry.recipeName}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', flexShrink: 0 }}>
                    <QtyBtn onClick={() => changeQty(entry.recipeId, -1)}>−</QtyBtn>
                    <span style={{ fontSize: '0.82rem', minWidth: 20, textAlign: 'center', color: '#c8a96e', fontWeight: 600 }}>
                      {entry.qty}
                    </span>
                    <QtyBtn onClick={() => changeQty(entry.recipeId, +1)}>+</QtyBtn>
                  </div>
                </div>
                <IngredientList comps={buildIngredientList(entry)} compact />
              </div>
            ))}

            {/* Ingredient Summary */}
            {list.length > 0 && (
              <div style={{ marginTop: '0.5rem' }}>
                <h3 style={{ fontSize: '0.82rem', color: 'var(--text-muted)', margin: '0 0 0.4rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Ingredient Summary
                </h3>

                {summary.regular.length > 0 && (
                  <>
                    <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', margin: '0.4rem 0 0.2rem', fontWeight: 600 }}>Materials</p>
                    {summary.regular.map(row => (
                      <div key={row.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', padding: '1px 0' }}>
                        <span style={{ color: 'var(--text)' }}>{row.name}</span>
                        <span style={{ color: '#c8a96e', fontWeight: 600, marginLeft: '0.5rem' }}>×{row.total}</span>
                      </div>
                    ))}
                  </>
                )}

                {summary.fuel.length > 0 && (
                  <>
                    <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', margin: '0.7rem 0 0.2rem', fontWeight: 600 }}>
                      Fuel
                    </p>
                    {summary.fuel.map(row => (
                      <div key={row.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', padding: '1px 0' }}>
                        <span style={{ color: 'var(--text-muted)' }}>{row.name}</span>
                        <span style={{ color: '#9a7d4a', fontWeight: 600, marginLeft: '0.5rem' }}>×{row.total}</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}

            {list.length === 0 && (
              <p style={{ fontSize: '0.83rem', color: 'var(--text-muted)', textAlign: 'center', marginTop: '1rem' }}>
                Use + on a recipe to add it.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Floating cart button when panel is closed */}
      {!listOpen && list.length > 0 && (
        <button
          onClick={() => setListOpen(true)}
          title="Open Shopping List"
          style={{
            position: 'fixed',
            bottom: '1.5rem',
            right: '1.5rem',
            background: '#c8a96e',
            color: '#0f1117',
            border: 'none',
            borderRadius: '50%',
            width: 52,
            height: 52,
            fontSize: '1.4rem',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
            zIndex: 100,
          }}
        >
          🛒
          <span style={{
            position: 'absolute',
            top: -4,
            right: -4,
            background: '#f87171',
            color: '#fff',
            borderRadius: '50%',
            width: 20,
            height: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '0.65rem',
            fontWeight: 700,
          }}>
            {totalItems}
          </span>
        </button>
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
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      overflow: 'hidden',
    }}>
      {/* Header row */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.6rem',
        padding: '0.55rem 0.75rem',
        cursor: 'pointer',
        userSelect: 'none',
      }}
        onClick={() => setOpen(v => !v)}
      >
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', transform: `rotate(${open ? 90 : 0}deg)`, display: 'inline-block', transition: 'transform 0.15s' }}>
          ▶
        </span>
        <span style={{ flex: 1, fontSize: '0.92rem', fontWeight: 500 }}>{recipe.name}</span>
        {recipe.craft_tier && (
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
            {recipe.craft_tier}
          </span>
        )}
        {recipe.crafted_tier && <TierBadge tier={recipe.crafted_tier} />}
        {recipe.bench_label && (
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
            {recipe.bench_label}
          </span>
        )}
        {recipe.class_label && (
          <span style={{ fontSize: '0.72rem', color: '#93d9ff', whiteSpace: 'nowrap' }}>
            {recipe.class_label}
          </span>
        )}
        {/* +/- controls */}
        <div
          onClick={e => e.stopPropagation()}
          style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', flexShrink: 0 }}
        >
          {inList > 0 && (
            <>
              <QtyBtn onClick={onDec}>−</QtyBtn>
              <span style={{ fontSize: '0.82rem', minWidth: 20, textAlign: 'center', color: '#c8a96e', fontWeight: 600 }}>
                {inList}
              </span>
            </>
          )}
          <button
            onClick={onAdd}
            title="Add to shopping list"
            style={{
              background: 'none',
              border: '1px solid #c8a96e',
              borderRadius: 4,
              color: '#c8a96e',
              fontSize: '0.78rem',
              padding: '1px 7px',
              cursor: 'pointer',
              lineHeight: 1.5,
            }}
          >
            +
          </button>
        </div>
      </div>

      {/* Expandable ingredient detail */}
      {open && (
        <div style={{
          borderTop: '1px solid var(--border)',
          padding: '0.6rem 0.75rem 0.75rem',
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '0.5rem 1rem',
        }}>
          {/* Materials */}
          <div>
            <p style={{ margin: '0 0 0.25rem', fontSize: '0.72rem', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Materials
            </p>
            {recipe.primary_comp && (
              <div style={{ fontSize: '0.83rem', color: 'var(--text)' }}>
                {recipe.primary_comp} ×{recipe.primary_qty ?? 1}
              </div>
            )}
            {recipe.secondary_comps.map((sc, i) => (
              <div key={i} style={{ fontSize: '0.83rem', color: 'var(--text)' }}>
                {sc.description} ×{sc.quantity}
              </div>
            ))}
            {!recipe.primary_comp && !recipe.secondary_comps.length && (
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>—</span>
            )}
          </div>

          {/* Fuel */}
          <div>
            <p style={{ margin: '0 0 0.25rem', fontSize: '0.72rem', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Fuel
            </p>
            {recipe.fuel_comp ? (
              <div style={{ fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                {recipe.fuel_comp} ×{recipe.fuel_qty ?? 1}
              </div>
            ) : (
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>—</span>
            )}
          </div>
        </div>
      )}
    </div>
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

function pagerBtn(disabled: boolean): CSSProperties {
  return {
    background: disabled ? 'var(--surface)' : 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 5,
    color: disabled ? 'var(--text-muted)' : 'var(--text)',
    padding: '0.3rem 0.75rem',
    fontSize: '0.82rem',
    cursor: disabled ? 'default' : 'pointer',
    opacity: disabled ? 0.45 : 1,
  }
}
