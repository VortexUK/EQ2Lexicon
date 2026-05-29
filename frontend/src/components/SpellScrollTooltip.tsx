/**
 * Hover tooltip for spell-tier pip icons.
 *
 * SpellTierPip — drop-in replacement for the <img> tier pip.
 *   On hover (150 ms delay) it opens a portal tooltip showing the spell-scroll
 *   item and, for Journeyman / Expert, the crafting recipe with a ✦ CRAFTABLE
 *   badge.
 */
import { type MouseEvent, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  type ItemDetail,
  BORDER_OUTER,
  BORDER_INNER,
  qualityStyle,
  TooltipContent,
  Section,
  prefetchItem,
  getCachedItem,
} from './ItemTooltip'
import { useTooltipPosition } from '../hooks/useTooltipPosition'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ScrollIngredient {
  description: string
  quantity: number
}

interface ScrollRecipe {
  primary_comp: string | null
  primary_qty: number | null
  secondary_comps: ScrollIngredient[]
  fuel_comp: string | null
  fuel_qty: number | null
}

interface SpellScrollInfo {
  item_id: number | null
  craftable: boolean
  recipe: ScrollRecipe | null
}

// ── Module-level cache (persists across re-renders / navigation) ──────────────

const _scrollCache = new Map<string, SpellScrollInfo>()

// ── Style constants ───────────────────────────────────────────────────────────

const TIP_W       = 360
const C_CRAFTABLE = '#84cc16'
const C_GOLD      = '#e6e970'
const C_BODY      = '#c7cfc7'
const C_MUTED     = '#888'

// ── Recipe ingredients list ───────────────────────────────────────────────────

function RecipeSection({ recipe }: { recipe: ScrollRecipe }) {
  return (
    <div style={{ fontSize: '0.8rem', color: C_BODY }}>
      {recipe.primary_comp && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
          <span>{recipe.primary_comp}</span>
          <span style={{ color: C_GOLD, paddingLeft: 8 }}>{recipe.primary_qty ?? 1}</span>
        </div>
      )}
      {recipe.secondary_comps.map((c, i) => (
        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
          <span>{c.description}</span>
          <span style={{ color: C_GOLD, paddingLeft: 8 }}>{c.quantity}</span>
        </div>
      ))}
      {recipe.fuel_comp && (
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          marginTop: 3, opacity: 0.7,
        }}>
          <span style={{ fontStyle: 'italic' }}>{recipe.fuel_comp}</span>
          <span style={{ color: C_GOLD, paddingLeft: 8 }}>{recipe.fuel_qty ?? 1}</span>
        </div>
      )}
    </div>
  )
}

// ── Portal tooltip ────────────────────────────────────────────────────────────

function SpellScrollTooltipPortal({
  spellName, tier, x, y,
}: {
  spellName: string
  tier: string
  x: number
  y: number
}) {
  const cacheKey = `${spellName}|${tier}`

  const [info, setInfo]       = useState<SpellScrollInfo | null>(_scrollCache.get(cacheKey) ?? null)
  const [item, setItem]       = useState<ItemDetail | null>(null)
  const [loading, setLoading] = useState(!_scrollCache.has(cacheKey))

  // ── Fetch spell-scroll info (and item) ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false

    async function load() {
      let resolved: SpellScrollInfo

      if (_scrollCache.has(cacheKey)) {
        resolved = _scrollCache.get(cacheKey)!
        setInfo(resolved)
      } else {
        try {
          const res = await fetch(
            `/api/spell-scroll?name=${encodeURIComponent(spellName)}&tier=${encodeURIComponent(tier)}`
          )
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          resolved = await res.json() as SpellScrollInfo
          _scrollCache.set(cacheKey, resolved)
          if (!cancelled) setInfo(resolved)
        } catch {
          if (!cancelled) setLoading(false)
          return
        }
      }

      // Fetch full item detail if we have an ID
      if (resolved.item_id != null) {
        const id = String(resolved.item_id)
        const cached = getCachedItem(id)
        if (cached) {
          if (!cancelled) setItem(cached)
        } else {
          await prefetchItem(id)
          if (!cancelled) setItem(getCachedItem(id) ?? null)
        }
      }

      if (!cancelled) setLoading(false)
    }

    load()
    return () => { cancelled = true }
  }, [cacheKey, spellName, tier])

  const { ref, position } = useTooltipPosition({ x, y, width: TIP_W, marginX: 16, marginY: 8 })
  const qs = item ? qualityStyle(item.quality) : null

  return createPortal(
    <div ref={ref} style={{
      position: 'fixed', left: position.left, top: position.top,
      width: TIP_W, zIndex: 9999,
      pointerEvents: 'none', userSelect: 'none',
      fontFamily: '"Times New Roman", Times, serif',
      background: '#0a0a0e',
      border: `2px solid ${BORDER_OUTER}`,
      boxShadow: `inset 0 0 0 1px ${BORDER_INNER}, 0 8px 32px rgba(0,0,0,0.9)`,
      borderRadius: 2,
      padding: 6,
    }}>
      <div style={{ border: `1px solid ${BORDER_INNER}`, padding: '8px 10px' }}>

        {/* Loading */}
        {loading && (
          <div style={{ color: C_MUTED, fontSize: '0.82rem' }}>Loading…</div>
        )}

        {/* No item found, not craftable either */}
        {!loading && !item && !info?.craftable && (
          <div style={{ color: C_MUTED, fontSize: '0.82rem' }}>
            {spellName} ({tier})
          </div>
        )}

        {/* No item in DB but craftable — show recipe only */}
        {!loading && !item && info?.craftable && (
          <div>
            <div style={{
              color: '#e8e8e8', fontWeight: 'bold',
              fontSize: '0.97rem', marginBottom: 4,
            }}>
              {spellName} ({tier})
            </div>
            <div style={{
              color: C_CRAFTABLE, fontWeight: 'bold',
              fontSize: '0.8rem', letterSpacing: '0.06em', marginBottom: 6,
            }}>
              ✦ CRAFTABLE
            </div>
            {info.recipe && <RecipeSection recipe={info.recipe} />}
          </div>
        )}

        {/* Full item tooltip + optional craftable section */}
        {!loading && item && qs && (
          <>
            <TooltipContent item={item} qs={qs} />
            {info?.craftable && (
              <Section>
                <div style={{
                  color: C_CRAFTABLE, fontWeight: 'bold',
                  fontSize: '0.8rem', letterSpacing: '0.06em', marginBottom: 6,
                }}>
                  ✦ CRAFTABLE
                </div>
                {info.recipe
                  ? <RecipeSection recipe={info.recipe} />
                  : <div style={{ color: C_MUTED, fontSize: '0.78rem' }}>Recipe not found</div>
                }
              </Section>
            )}
          </>
        )}

      </div>
    </div>,
    document.body,
  )
}

// ── Public component ──────────────────────────────────────────────────────────

/**
 * Drop-in replacement for a spell-tier-pip <img>.
 * Shows a spell-scroll tooltip on hover (150 ms delay).
 */
export function SpellTierPip({
  src, tier, spellName,
}: {
  src: string
  tier: string
  spellName: string
}) {
  const [showTooltip, setShowTooltip] = useState(false)
  const [mousePos, setMousePos]       = useState({ x: 0, y: 0 })
  const timerRef                      = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Clear pending timer if the component unmounts (e.g., page navigation)
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  function handleMouseEnter(e: MouseEvent<HTMLImageElement>) {
    setMousePos({ x: e.clientX, y: e.clientY })
    timerRef.current = setTimeout(() => setShowTooltip(true), 150)
  }

  function handleMouseMove(e: MouseEvent<HTMLImageElement>) {
    setMousePos({ x: e.clientX, y: e.clientY })
  }

  function handleMouseLeave() {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setShowTooltip(false)
  }

  // Touch path: tap opens immediately (no 150ms hover delay).
  function handleClick(e: MouseEvent<HTMLImageElement>) {
    setMousePos({ x: e.clientX, y: e.clientY })
    setShowTooltip(true)
  }

  return (
    <>
      <img
        src={src}
        alt={tier}
        title={tier}
        style={{ width: 14, height: 14 }}
        onMouseEnter={handleMouseEnter}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
      />
      {showTooltip && (
        <SpellScrollTooltipPortal
          spellName={spellName}
          tier={tier}
          x={mousePos.x}
          y={mousePos.y}
        />
      )}
    </>
  )
}
