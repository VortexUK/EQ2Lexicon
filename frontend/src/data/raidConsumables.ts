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
      {
        title: 'Potions',
        blurb: "Alchemist-made Expert's elixirs — the T8 combat potions.",
        entries: [
          { name: "Expert's Elixir of Constitution", itemId: '2991055542', tag: '+446 max HP · 15m' },
          { name: "Expert's Elixir of Transcendence", itemId: '3326107361', tag: '+319 max power · 15m' },
          { name: "Expert's Elixir of Second Sight", itemId: '2545151128', tag: 'crit +4 (mage) · 15m' },
          { name: "Expert's Elixir of Tactics", itemId: '4180168692', tag: 'crit +4 · 15m' },
          { name: "Expert's Elixir of Thorns", itemId: '2150480951', tag: 'damage shield · 2m' },
          { name: "Expert's Elixir of Deftness", itemId: '843513420', tag: '+26 AGI · 30m' },
          { name: "Expert's Elixir of Fortitude", itemId: '900482767', tag: '+26 STR · 30m' },
          { name: "Expert's Elixir of Intellect", itemId: '3917449409', tag: '+26 INT · 30m' },
          { name: "Expert's Elixir of Piety", itemId: '3724122836', tag: '+26 WIS · 30m' },
        ],
      },
      {
        title: 'Totems',
        blurb: 'Woodworker-made utility — everyone should carry a stack. (Level-80 totems like Sabertooth/Void are next-tier books, not in era.)',
        entries: [
          { name: 'Spirit Totem of the Chokidai', itemId: '2252027845', tag: 'run speed +45% · 30m' },
          { name: 'Totem of the Mythic Chameleon', itemId: '1240847606', tag: 'self invis · 15m' },
          { name: 'Vision Totem of the Cat', itemId: '729207267', tag: 'see invis · 15m' },
          { name: 'Totem of Escape', itemId: '3629878814', tag: 'evac (out of combat)' },
          { name: 'Totem of the Otter', itemId: '613400061', tag: 'water breathing · 15m' },
        ],
      },
      {
        title: 'Temporary Adornments',
        blurb: 'Crafted expendable adorns — one per weapon/item, consumed on use. Player-made only (crate/marketplace versions excluded).',
        entries: [
          { name: 'Smoldering Whetstone', itemId: '2248740665', tag: '70 · crit +1' },
          { name: 'Smoldering Oilstone', itemId: '281436377', tag: '70 · DPS +10' },
          { name: 'Smoldering Waterstone', itemId: '3649454449', tag: '70 · crit +1 (ranged)' },
          { name: 'Smoldering Scroll of Combat', itemId: '4108433004', tag: '70 · crit +1 (mage)' },
          { name: 'Smoldering Scroll of Tactics', itemId: '1407485282', tag: '70 · +40 spell dmg' },
          { name: 'Smoldering Scroll of Benediction', itemId: '1344934361', tag: '70 · crit +1 (priest)' },
          { name: 'Smoldering Scroll of Blessing', itemId: '495806769', tag: '70 · +40 heals' },
          { name: 'Rilissian Rager Whetstone', itemId: '3162511740', tag: '75 · crit +3' },
          { name: 'Rilissian Rager Slipstone', itemId: '957837032', tag: '75 · multi attack +3' },
          { name: 'Danak Regimental Oilstone', itemId: '1763512832', tag: '75 · DPS +15' },
          { name: 'Danak Regimental Compoundstone', itemId: '3529496540', tag: '75 · +50 CA dmg' },
          { name: "Archer of Di'Zok Waterstone", itemId: '2105289266', tag: '75 · crit +3 (ranged)' },
          { name: "Archer of Di'Zok Flashstone", itemId: '3595014389', tag: '75 · MA +3 (ranged)' },
          { name: 'Sathirian Rune of Combat', itemId: '1880924496', tag: '75 · crit +3 (mage)' },
          { name: 'Sathirian Rune of Benediction', itemId: '3405064112', tag: '75 · crit +3 (priest)' },
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
