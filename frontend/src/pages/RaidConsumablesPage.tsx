// Raid Food & Drink — the current tier's curated consumables cheat-sheet
// (stat drinks/food + the 30-minute combat infusions/reductions), with the
// real item tooltips on hover. The sheet follows the server's current
// expansion (data/raidConsumables.ts); when the server rolls to a new xpac
// the page switches the moment that tier's sheet is curated.

import type { MouseEvent } from 'react'
import { Link } from 'react-router-dom'

import { ItemTooltip, prefetchItem, useItemTooltip } from '../components/ItemTooltip'
import { Card, SectionLabel } from '../components/ui'
import { CONSUMABLE_SHEETS, sheetForXpac, type ConsumableSection } from '../data/raidConsumables'
import { useServer } from '../hooks/useServer'

function SectionCard({
  section,
  onShowTip,
  onHideTip,
}: {
  section: ConsumableSection
  onShowTip: (itemId: string, e: MouseEvent) => void
  onHideTip: () => void
}) {
  return (
    <Card className="p-4 flex flex-col gap-2">
      <SectionLabel>{section.title}</SectionLabel>
      {section.blurb && <p className="text-[0.78rem] text-text-muted -mt-1">{section.blurb}</p>}
      <ul className="flex flex-col m-0 p-0 list-none">
        {section.entries.map(entry => (
          <li
            key={entry.itemId}
            className="flex items-baseline justify-between gap-3 py-1 border-b border-border/40 last:border-b-0"
          >
            <Link
              to={`/item/${entry.itemId}`}
              className="text-gold hover:text-gold-bright no-underline text-[0.9rem]"
              onMouseEnter={e => {
                prefetchItem(entry.itemId)
                onShowTip(entry.itemId, e)
              }}
              onMouseLeave={onHideTip}
            >
              {entry.name}
            </Link>
            <span className="text-[0.78rem] text-text-muted whitespace-nowrap">{entry.tag}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export default function RaidConsumablesPage() {
  const server = useServer()
  const { tooltip, showTip, hideTip, moveTip } = useItemTooltip()
  const picked = sheetForXpac(server?.currentXpac ?? null)

  if (!picked) {
    return <p className="text-text-muted p-4">No consumables sheet curated yet.</p>
  }
  const { sheet, exact } = picked

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6 flex flex-col gap-4" onMouseMove={moveTip}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="font-heading text-gold text-[1.5rem] m-0">Raid Food &amp; Drink</h1>
        <span className="text-text-muted text-[0.88rem]">{sheet.label}</span>
      </div>
      {!exact && server?.currentXpac && (
        <p className="text-warning text-[0.8rem] m-0">
          No sheet curated for {server.currentXpac} yet — showing {sheet.label} until it is.
        </p>
      )}
      <p className="text-text-muted text-[0.85rem] m-0">
        Hover an item for its full tooltip; click through for the item page. All of these are
        provisioner-made for the current tier — lean on your guild crafters.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        {sheet.sections.map(section => (
          <SectionCard key={section.title} section={section} onShowTip={showTip} onHideTip={hideTip} />
        ))}
      </div>

      {tooltip && <ItemTooltip state={tooltip} />}
      {CONSUMABLE_SHEETS.length > 1 && (
        <p className="text-[0.72rem] text-text-muted">
          Sheets curated for: {CONSUMABLE_SHEETS.map(s => s.xpac).join(', ')}
        </p>
      )}
    </main>
  )
}
