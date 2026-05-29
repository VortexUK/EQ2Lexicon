import { Button, Card } from '../../components/ui'
import type { ShoppingEntry, IngredientSummary, Ingredient } from './types'
import { buildIngredientList, downloadShoppingListXml } from './types'
import { QtyBtn } from './QtyBtn'

// ── IngredientList ────────────────────────────────────────────────────────────

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

// ── ShoppingListPanel ─────────────────────────────────────────────────────────

interface ShoppingListPanelProps {
  list:             ShoppingEntry[]
  summary:          IngredientSummary
  showMats:         boolean
  onShowMatsChange: (v: boolean) => void
  onChangeQty:      (recipeId: number, delta: number) => void
  onClear:          () => void
}

export function ShoppingListPanel({
  list,
  summary,
  showMats,
  onShowMatsChange,
  onChangeQty,
  onClear,
}: ShoppingListPanelProps) {
  return (
    <Card className="p-4 self-start sticky top-16 max-h-[calc(100vh-5rem)] overflow-y-auto">
      <div className="flex justify-between items-center mb-3">
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
            onClick={onClear}
            title="Clear list"
            className="border-none"
          >
            Clear
          </Button>
        </div>
      </div>

      {/* View options */}
      {list.length > 0 && (
        <label className="flex items-center gap-1.5 text-[0.76rem] text-text-muted cursor-pointer mb-2.5 select-none">
          <input
            type="checkbox"
            checked={showMats}
            onChange={e => onShowMatsChange(e.target.checked)}
            className="cursor-pointer accent-gold"
          />
          Show materials per spell
        </label>
      )}

      {/* List entries */}
      {list.map(entry => (
        <div key={entry.recipeId} className="border-b border-border pb-2 mb-2">
          <div className="flex items-center gap-2" style={{ marginBottom: showMats ? '0.2rem' : 0 }}>
            <span className="text-[0.85rem] flex-1 leading-[1.3]">{entry.recipeName}</span>
            <div className="flex items-center gap-1 shrink-0">
              <QtyBtn onClick={() => onChangeQty(entry.recipeId, -1)}>−</QtyBtn>
              <span className="text-[0.82rem] min-w-[20px] text-center text-gold font-semibold">
                {entry.qty}
              </span>
              <QtyBtn onClick={() => onChangeQty(entry.recipeId, +1)}>+</QtyBtn>
            </div>
          </div>
          {showMats && <IngredientList comps={buildIngredientList(entry)} compact />}
        </div>
      ))}

      {/* Ingredient Summary */}
      {list.length > 0 && (
        <div className="mt-2">
          <h3 className="text-[0.82rem] text-text-muted mt-0 mx-0 mb-1.5 uppercase tracking-[0.06em]">
            Ingredient Summary
          </h3>

          {summary.regular.length > 0 && (
            <>
              <p className="text-[0.72rem] text-text-muted mt-1.5 mx-0 mb-1 font-semibold">Materials</p>
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
              <p className="text-[0.72rem] text-text-muted mt-3 mx-0 mb-1 font-semibold">
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
  )
}
