// Curated raid food & drink sheets, one per expansion tier. The page picks
// the sheet matching the server's current_xpac (the admin registry), so
// flipping the server to TSO switches the page the moment a TSO sheet is
// curated here — until then the newest curated sheet shows with a note.
//
// itemIds come from items.db (data/items/items.db, displayname lookup) and
// drive the hover tooltips + /item links. Tags are the raid-caller shorthand
// (duration · stats), deliberately terse — the tooltip carries the details.

export interface ConsumableEntry {
  name: string
  itemId: string
  tag: string
}

export interface ConsumableSection {
  title: string
  blurb?: string
  entries: ConsumableEntry[]
}

export interface ConsumableSheet {
  /** Matches servers.current_xpac (case-insensitive), e.g. "RoK". */
  xpac: string
  label: string
  sections: ConsumableSection[]
}

/** Ordered oldest → newest; the last entry is the fallback sheet. */
export const CONSUMABLE_SHEETS: ConsumableSheet[] = [
  {
    xpac: 'RoK',
    label: 'Rise of Kunark (T8)',
    sections: [
      {
        title: 'Drinks',
        blurb: 'Long-duration stat drinks — keep one running all night.',
        entries: [
          { name: 'Overthere Reserve Ale', itemId: '2044227135', tag: '5h · max power/HP' },
          { name: 'Drachnid Bliss', itemId: '1147008911', tag: '5h · WIS STA' },
          { name: 'After Dark', itemId: '2583959408', tag: '5h · INT STA' },
          { name: 'Sweet Brewed Torsis Tea', itemId: '2742214923', tag: '1h30 · STR STA' },
          { name: 'Succulent Tonic', itemId: '304728014', tag: '5h · INT' },
          { name: 'Iced Torsis Tea', itemId: '2478582435', tag: '5h · STR' },
          { name: 'Cranberry Margarita', itemId: '2145245399', tag: '5h · AGI' },
          { name: 'Cranberry Cream Liqueur', itemId: '1827666744', tag: '5h · WIS' },
          { name: 'Spiced Torsis Tea', itemId: '993742984', tag: '5h · WIS' },
        ],
      },
      {
        title: 'Food',
        blurb: 'Long-duration stat food — pair with your drink.',
        entries: [
          { name: 'Juicy Cranberry Cobbler', itemId: '3972275889', tag: '5h · AGI STR' },
          { name: 'Cranberry-filled Sweet Pastries', itemId: '630289485', tag: '2h15 · WIS STA' },
          { name: 'Succulent and King Prawn Pie', itemId: '3175143131', tag: '1h · AGI STA' },
          { name: 'Teren Field Ration', itemId: '1225402264', tag: '5h · STA' },
          { name: 'Steamed King Prawn Dumplings', itemId: '769611987', tag: '5h · INT' },
          { name: 'Cranberry Tea Cake', itemId: '3304273390', tag: '5h · STR' },
          { name: 'Cabilis Cocoa Mousse', itemId: '1793103513', tag: '5h · AGI' },
          { name: 'Barracuda Roll', itemId: '66661236', tag: '5h · WIS' },
          { name: 'Succulent Dip', itemId: '620416943', tag: '5h · WIS' },
        ],
      },
      {
        title: 'Infusions',
        blurb: '30-minute combat drinks — swap in for the pull.',
        entries: [
          { name: 'Fiery Magma Infusion', itemId: '3518264228', tag: 'ability mod' },
          { name: 'Hornet Venom Infusion', itemId: '310001181', tag: 'max HP' },
          { name: 'Cockatrice Blood Infusion', itemId: '3200238351', tag: 'max power' },
          { name: 'Mountain Giant Sweat Infusion', itemId: '3530363076', tag: 'dodge%' },
          { name: 'Succulent Brutemarrow Infusion', itemId: '2508113072', tag: 'parry%' },
          { name: 'Sweetened Devourer Blood Infusion', itemId: '1486053435', tag: 'INT · dodge%' },
          { name: 'Cranberry Flavored Cockatrice Blood Infusion', itemId: '3425506897', tag: 'WIS · dodge%' },
        ],
      },
      {
        title: 'Reductions',
        blurb: '30-minute combat food — swap in for the pull.',
        entries: [
          { name: 'Magma Fish Reduction', itemId: '1196850511', tag: 'damage skin' },
          { name: 'Behemoth Reduction', itemId: '1305020193', tag: 'max HP/power' },
          { name: 'Cranberry Glazed Drachnid Reduction', itemId: '1657766822', tag: 'STR · parry%' },
          { name: 'Bruteflesh Reduction', itemId: '1437569953', tag: 'STA · parry%' },
          { name: 'Spiced Hornet Legs Reduction', itemId: '2684599595', tag: 'dodge%' },
          { name: 'Mountain Giant Flesh Reduction', itemId: '3700808561', tag: 'max power' },
          { name: 'Devourer Flesh Reduction', itemId: '1193030996', tag: 'max HP' },
        ],
      },
    ],
  },
]

/** The sheet for the server's current expansion; falls back to the newest
 * curated sheet (exact=false) when that tier has no sheet yet. Null only
 * when nothing is curated at all. */
export function sheetForXpac(currentXpac: string | null): { sheet: ConsumableSheet; exact: boolean } | null {
  if (CONSUMABLE_SHEETS.length === 0) return null
  const match = currentXpac
    ? CONSUMABLE_SHEETS.find(s => s.xpac.toLowerCase() === currentXpac.toLowerCase())
    : undefined
  if (match) return { sheet: match, exact: true }
  return { sheet: CONSUMABLE_SHEETS[CONSUMABLE_SHEETS.length - 1], exact: false }
}
