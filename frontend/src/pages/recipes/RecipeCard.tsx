import { useState } from 'react'
import { Button, Card } from '../../components/ui'
import { recipeTierColor } from '../../rarityColors'
import type { RecipeResult } from './types'
import { QtyBtn } from './QtyBtn'

// ── TierBadge ─────────────────────────────────────────────────────────────────

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

// ── RecipeCard ────────────────────────────────────────────────────────────────

interface RecipeCardProps {
  recipe: RecipeResult
  onAdd:  () => void
  inList: number
  onDec:  () => void
}

export function RecipeCard({ recipe, onAdd, inList, onDec }: RecipeCardProps) {
  const [open, setOpen] = useState(false)

  return (
    <Card className="p-0 overflow-hidden">
      {/* Header row */}
      <div
        className="flex items-center gap-2.5 px-3 py-2 cursor-pointer select-none"
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
        {recipe.craft_classes.length > 0 && (
          <span className="text-[0.72rem] text-text-muted whitespace-nowrap">
            {recipe.craft_classes.length >= 9 ? 'All artisans' : recipe.craft_classes.join(' / ')}
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
