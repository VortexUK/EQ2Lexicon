import { useState, useEffect, useCallback, useRef } from 'react'
import { useLazyFetch } from '../hooks/useFetch'
import { useSearchParams } from 'react-router-dom'
import { Button } from '../components/ui'
import { FilterDropdown, groupedFromHeaders } from '../components/FilterDropdown'
import {
  CRAFT_TIERS, CRAFT_TIER_LABELS,
  PRIMARY_CRAFT_CLASSES, SECONDARY_CRAFT_CLASSES,
  CLASS_OPTIONS, CTRL_CLS,
  STORAGE_KEY,
  loadList, saveList, aggregateList,
  type RecipeResult, type RecipeSearchResponse, type ShoppingEntry,
} from './recipes/types'
import { RecipeCard } from './recipes/RecipeCard'
import { ShoppingListPanel } from './recipes/ShoppingListPanel'

export default function RecipesPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // ── Filter state (URL-synced) ────────────────────────────────────────────────
  const [q,          setQ]          = useState(() => searchParams.get('q')           ?? '')
  const [tier,       setTier]       = useState(() => searchParams.get('tier')        ?? '')
  const [craftClass, setCraftClass] = useState(() => searchParams.get('craft_class') ?? '')
  const [className,  setClassName]  = useState(() => searchParams.get('cls')         ?? '')

  // ── Results state ────────────────────────────────────────────────────────────
  const {
    data: searchResult,
    loading,
    error,
    run: runSearch,
  } = useLazyFetch<RecipeSearchResponse>()
  const results  = searchResult?.results  ?? []
  const total    = searchResult?.total    ?? 0
  const page     = searchResult?.page     ?? 1
  const perPage  = searchResult?.per_page ?? 25
  const searched = searchResult !== null

  // ── Shopping list state (localStorage) ───────────────────────────────────────
  const [list,     setList]     = useState<ShoppingEntry[]>(() => loadList())
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
  const doSearch = useCallback((
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

    runSearch(`/api/recipes/search?${params}`)
  }, [q, tier, craftClass, className, runSearch])


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
        recipeId:        recipe.id,
        recipeName:      recipe.name,
        qty:             1,
        primary_comp:    recipe.primary_comp,
        primary_qty:     recipe.primary_qty,
        secondary_comps: recipe.secondary_comps,
        fuel_comp:       recipe.fuel_comp,
        fuel_qty:        recipe.fuel_qty,
      }]
    })
  }, [])

  const changeQty = useCallback((recipeId: number, delta: number) => {
    setList(prev => {
      const idx = prev.findIndex(e => e.recipeId === recipeId)
      if (idx < 0) return prev
      const newQty = prev[idx].qty + delta
      if (newQty <= 0) return prev.filter((_, i) => i !== idx)
      const next = [...prev]
      next[idx] = { ...next[idx], qty: newQty }
      return next
    })
  }, [])

  const clearList = useCallback(() => { setList([]) }, [])

  const summary    = aggregateList(list)
  const totalPages = Math.ceil(total / perPage)

  return (
    <main className="max-w-[1100px] mx-auto px-4 py-6">
      <div className="flex items-baseline gap-4 mb-[1.2rem]">
        <h1 className="font-heading text-[1.7rem] text-gold m-0">
          Recipes
        </h1>
      </div>

      <div className="grid gap-5 grid-cols-1 md:[grid-template-columns:1fr_340px]">

        {/* ── Left column: filters + results ─────────────────────────────────── */}
        <div>
          {/* Filter row */}
          <div className="flex flex-wrap gap-2.5 items-end mb-4 [&>div]:flex-1 [&>div]:min-w-[140px]">
            {/* Name */}
            <div>
              <label className="text-xs text-text-muted block mb-0.5">
                Recipe name
              </label>
              <input
                className={CTRL_CLS}
                placeholder="Search…"
                value={q}
                onChange={e => setQ(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doSearch(1)}
              />
            </div>

            {/* Tier */}
            <div>
              <label className="text-xs text-text-muted block mb-0.5">
                Crafting tier
              </label>
              <FilterDropdown
                standalone
                className="w-full justify-between"
                value={tier}
                placeholder="All tiers"
                options={[
                  { value: '', label: 'All tiers' },
                  ...CRAFT_TIERS.map(t => ({ value: t, label: CRAFT_TIER_LABELS[t] ?? t })),
                ]}
                onChange={setTier}
              />
            </div>

            {/* Tradeskill class */}
            <div>
              <label className="text-xs text-text-muted block mb-0.5">
                Artisan class
              </label>
              <FilterDropdown
                standalone
                className="w-full justify-between"
                value={craftClass}
                placeholder="All Artisans"
                options={[
                  { value: '', label: 'All Artisans' },
                  ...PRIMARY_CRAFT_CLASSES.map(c => ({ value: c, label: c, group: 'Primary Tradeskills' })),
                  ...SECONDARY_CRAFT_CLASSES.map(c => ({ value: c, label: c, group: 'Secondary Tradeskills' })),
                ]}
                onChange={setCraftClass}
              />
            </div>

            {/* Adventure class */}
            <div>
              <label className="text-xs text-text-muted block mb-0.5">
                Adventure class
              </label>
              <FilterDropdown
                standalone
                className="w-full justify-between"
                value={className}
                placeholder="All Classes"
                options={groupedFromHeaders(CLASS_OPTIONS)}
                onChange={setClassName}
              />
            </div>

            {/* Search button */}
            <Button
              variant="primary"
              onClick={() => doSearch(1)}
              disabled={loading}
            >
              {loading ? '…' : 'Search'}
            </Button>
          </div>

          {/* Error */}
          {error && (
            <p className="text-danger text-[0.9rem] mt-0 mx-0 mb-3">{error}</p>
          )}

          {/* Results count */}
          {searched && !loading && (
            <p className="text-[0.8rem] text-text-muted mt-0 mx-0 mb-2.5">
              {total === 0 ? 'No results.' : `${total.toLocaleString()} recipe${total !== 1 ? 's' : ''} found`}
            </p>
          )}

          {/* Recipe cards */}
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
                <div className="flex gap-1.5 items-center mt-4 flex-wrap">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => doSearch(page - 1)}
                    disabled={page <= 1}
                  >← Prev</Button>
                  <span className="text-[0.82rem] text-text-muted px-1">
                    Page {page} / {totalPages}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => doSearch(page + 1)}
                    disabled={page >= totalPages}
                  >Next →</Button>
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Right column: shopping list ─────────────────────────────────────── */}
        <ShoppingListPanel
          list={list}
          summary={summary}
          showMats={showMats}
          onShowMatsChange={setShowMats}
          onChangeQty={changeQty}
          onClear={clearList}
        />
      </div>
    </main>
  )
}

// Re-export STORAGE_KEY so any other module that needs it can find it here
// (keeps the import path stable if external code ever referenced it from this file)
export { STORAGE_KEY }
