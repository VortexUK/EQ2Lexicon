import { useState, useEffect } from 'react'
import { FilterDropdown, groupedFromHeaders } from '../../components/FilterDropdown'
import { Button, Card } from '../../components/ui'

// ── Stat options ──────────────────────────────────────────────────────────────
// IMPORTANT: each value must match a `stat` string in the item_stats table
// EXACTLY — the search backend filters `item_stats.stat = ?` (see
// backend/server/api/item.py). Any entry that doesn't match is a dead filter
// that always returns zero results. Keep this list aligned with the DB; the
// canonical names come from the item_parser modifier paths, not STAT_MAP.

// Tuples are [dbValue, displayLabel] where the label differs from the DB stat
// string. The flat-pool stats are stored lowercase ('health'/'mana') and are
// distinct from the percentage 'Max Health'/'Max Power' below.
const STAT_OPTIONS_PRIMARY: (string | [string, string])[] = [
  'Stamina',
  'Primary Attributes',
  'Combat Skills',
  'Resistances',
  ['health', 'Health'],
  ['mana', 'Power'],
]

const STAT_OPTIONS_SECONDARY = [
  'Resolve',
  'Potency',
  'Fervor',
  'Ability Mod',
  'Crit Bonus',
  'Crit Chance',
  'Casting Speed',
  'Reuse Speed',
  'Spell Reuse Speed',
  'Ability Doublecast',
  'Attack Speed',
  'DPS',
  'Multi Attack',
  'Flurry',
  'Flurry Multiplier',
  'AE Auto',
  'Weapon Damage',
  'Accuracy',
  'Strikethrough',
  'Max Health',
  'Max Power',
  'Mitigation Increase',
  'Block Chance',
  'Parry',
  'Deflection',
  'Dodge',
  'Extra Riposte',
  'Hate Gain',
  'Combat Health Regen',
  'Combat Power Regen',
  'Haste',
  'Weapon Skills',
]

const STAT_OPTIONS_TRADESKILL = [
  'Crafting Skills',
  'Harvesting Skills',
  'Tinkering',
  'Adorning',
  'Transmuting',
]

/** Normalise a stat option (plain string, or [dbValue, label] tuple) into a dropdown option. */
function statOpt(s: string | [string, string], group: string) {
  const [value, label] = Array.isArray(s) ? s : [s, s]
  return { value, label, group }
}

// ── Static filter options ─────────────────────────────────────────────────────

const TIER_OPTIONS = [
  'Celestial', 'Ethereal', 'Mythical', 'Fabled',
  'Legendary', 'Treasured', 'Uncommon',
  'Mastercrafted', 'Handcrafted', 'Common',
]

const SLOT_OPTIONS = [
  'Accolade',
  'Ammo',
  'Charm',
  'Chest',
  'Cloak',
  'Drink',
  'Ear',
  'Feet',
  'Finger',
  'Food',
  'Forearms',
  'Hands',
  'Head',
  'Legs',
  'Neck',
  'Primary',
  'Ranged',
  'Secondary',
  'Shoulders',
  'Waist',
  'Wrist',
]

const ITEM_TYPE_OPTIONS = [
  'Adornment',
  'Ammo',
  'Armor',
  'Container',
  'Expendable',
  'Food',
  'House Item',
  'Material',
  'Pattern',
  'Shield',
  'Weapon',
]

// ── Class hierarchy for dropdown ──────────────────────────────────────────────

const CLASS_OPTIONS: { label: string; value: string }[] = [
  { label: 'All Classes',    value: '' },
  // Fighter
  { label: '── Fighter ──', value: '__hdr' },
  { label: '  All Fighters',  value: 'guardian,berserker,monk,bruiser,shadowknight,paladin' },
  { label: '    Guardian',     value: 'guardian' },
  { label: '    Berserker',    value: 'berserker' },
  { label: '    Monk',         value: 'monk' },
  { label: '    Bruiser',      value: 'bruiser' },
  { label: '    Shadowknight', value: 'shadowknight' },
  { label: '    Paladin',      value: 'paladin' },
  // Priest
  { label: '── Priest ──',  value: '__hdr' },
  { label: '  All Priests',   value: 'templar,inquisitor,warden,fury,mystic,defiler,channeler' },
  { label: '    Templar',      value: 'templar' },
  { label: '    Inquisitor',   value: 'inquisitor' },
  { label: '    Warden',       value: 'warden' },
  { label: '    Fury',         value: 'fury' },
  { label: '    Mystic',       value: 'mystic' },
  { label: '    Defiler',      value: 'defiler' },
  { label: '    Channeler',    value: 'channeler' },
  // Mage
  { label: '── Mage ──',    value: '__hdr' },
  { label: '  All Mages',     value: 'wizard,warlock,illusionist,coercer,conjuror,necromancer' },
  { label: '    Wizard',       value: 'wizard' },
  { label: '    Warlock',      value: 'warlock' },
  { label: '    Illusionist',  value: 'illusionist' },
  { label: '    Coercer',      value: 'coercer' },
  { label: '    Conjuror',     value: 'conjuror' },
  { label: '    Necromancer',  value: 'necromancer' },
  // Scout
  { label: '── Scout ──',   value: '__hdr' },
  { label: '  All Scouts',    value: 'swashbuckler,brigand,troubador,dirge,ranger,assassin,beastlord' },
  { label: '    Swashbuckler', value: 'swashbuckler' },
  { label: '    Brigand',      value: 'brigand' },
  { label: '    Troubador',    value: 'troubador' },
  { label: '    Dirge',        value: 'dirge' },
  { label: '    Ranger',       value: 'ranger' },
  { label: '    Assassin',     value: 'assassin' },
  { label: '    Beastlord',    value: 'beastlord' },
]

// ── Shared control style ──────────────────────────────────────────────────────

const CTRL_CLS = 'py-1.5 px-2.5 rounded-sm2 border border-border bg-surface-raised text-text text-[0.88rem] leading-[1.4] [color-scheme:dark]'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface StatFilter {
  id:    number
  stat:  string
  op:    'gte' | 'lte'
  value: string
}

export interface ItemSearchQuery {
  q:        string
  tier:     string
  slot:     string
  itemType: string
  cls:      string
  minLevel: string
  maxLevel: string
  stats:    StatFilter[]
}

interface FilterOptions {
  server_max_level?: number | null
}

let _statFilterId = 0
function nextId() { return ++_statFilterId }

interface Props {
  /** Initial filter values (from URL params on mount). */
  initial: ItemSearchQuery
  /** Called on form submit with the current filter state. */
  onSearch: (q: ItemSearchQuery) => void
  /**
   * Called when stat-filter add/remove implicitly changes the desired sort.
   * The parent owns sort state; these callbacks keep it in sync.
   */
  onSortChange: (sortBy: string, sortDir: 'asc' | 'desc') => void
  /** Current sort state from parent — needed to keep implicit sort-change logic correct. */
  sortBy: string
  /** Whether a search is in progress (disables Submit). */
  loading: boolean
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ItemSearchFilters({
  initial,
  onSearch,
  onSortChange,
  sortBy,
  loading,
}: Props) {
  const [name,        setName]        = useState(initial.q)
  const [tier,        setTier]        = useState(initial.tier)
  const [slot,        setSlot]        = useState(initial.slot)
  const [itemType,    setItemType]    = useState(initial.itemType)
  const [classVal,    setClassVal]    = useState(initial.cls)
  const [minLevel,    setMinLevel]    = useState(initial.minLevel)
  const [maxLevel,    setMaxLevel]    = useState(initial.maxLevel)
  const [statFilters, setStatFilters] = useState<StatFilter[]>(initial.stats)

  // ── On mount: fetch server_max_level for level defaults (only if not in URL) ─

  useEffect(() => {
    fetch('/api/items/filters', { credentials: 'include' })
      .then(r => r.json())
      .then((opts: FilterOptions) => {
        if (opts.server_max_level) {
          // Don't override values the user already has (non-empty initial means URL had them)
          if (!initial.maxLevel) setMaxLevel(String(opts.server_max_level))
          if (!initial.minLevel) setMinLevel(String(opts.server_max_level - 9))
        }
      })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Stat filter management ──────────────────────────────────────────────────

  function addStatFilter() {
    const newFilter = { id: nextId(), stat: STAT_OPTIONS_SECONDARY[0], op: 'gte' as const, value: '' }
    // First stat filter added → tell parent to sort by it descending
    if (statFilters.length === 0) {
      onSortChange(newFilter.stat, 'desc')
    }
    setStatFilters(prev => [...prev, newFilter])
  }

  function removeStatFilter(id: number) {
    const removed   = statFilters.find(f => f.id === id)
    const remaining = statFilters.filter(f => f.id !== id)
    if (removed && sortBy === removed.stat) {
      if (remaining.length > 0) {
        onSortChange(remaining[0].stat, 'desc')
      } else {
        onSortChange('name', 'asc')
      }
    } else if (remaining.length === 0) {
      onSortChange('name', 'asc')
    }
    setStatFilters(prev => prev.filter(f => f.id !== id))
  }

  function updateStatFilter(id: number, field: 'stat' | 'op' | 'value', val: string) {
    // If renaming the stat we're currently sorting by, keep parent sort in sync
    if (field === 'stat' && sortBy === statFilters.find(f => f.id === id)?.stat) {
      onSortChange(val, 'desc')
    }
    setStatFilters(prev => prev.map(f => f.id === id ? { ...f, [field]: val } : f))
  }

  // ── Submit ──────────────────────────────────────────────────────────────────

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSearch({ q: name, tier, slot, itemType, cls: classVal, minLevel, maxLevel, stats: statFilters })
  }

  const hasAnyFilter = !!(
    name.trim() || tier || slot || itemType || classVal ||
    minLevel.trim() || maxLevel.trim() || statFilters.length
  )

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <form onSubmit={handleSubmit}>
      <Card className="py-4 px-[1.1rem] mb-5">

        {/* Row 1: name + tier + type + slot + class */}
        <div className="flex flex-wrap gap-3 items-end mb-3">

          <Field label="Name">
            <input
              type="text"
              placeholder="Search name…"
              value={name}
              onChange={e => setName(e.target.value)}
              className={`${CTRL_CLS} w-[180px]`}
            />
          </Field>

          <Field label="Quality">
            <FilterDropdown
              standalone
              value={tier}
              placeholder="Any"
              options={[{ value: '', label: 'Any' }, ...TIER_OPTIONS.map(t => ({ value: t, label: t }))]}
              onChange={setTier}
            />
          </Field>

          <Field label="Item Type">
            <FilterDropdown
              standalone
              value={itemType}
              placeholder="Any"
              options={[{ value: '', label: 'Any' }, ...ITEM_TYPE_OPTIONS.map(t => ({ value: t, label: t }))]}
              onChange={setItemType}
            />
          </Field>

          <Field label="Slot">
            <FilterDropdown
              standalone
              value={slot}
              placeholder="Any"
              options={[{ value: '', label: 'Any' }, ...SLOT_OPTIONS.map(s => ({ value: s, label: s }))]}
              onChange={setSlot}
            />
          </Field>

          <Field label="Class">
            <FilterDropdown
              standalone
              value={classVal}
              placeholder="All Classes"
              options={groupedFromHeaders(CLASS_OPTIONS)}
              onChange={setClassVal}
            />
          </Field>

        </div>

        {/* Row 2: levels + search */}
        <div className="flex flex-wrap gap-3 items-end mb-3">

          <Field label="Min Level">
            <input
              type="number" min={0} max={135} placeholder="e.g. 70"
              value={minLevel} onChange={e => setMinLevel(e.target.value)}
              className={`${CTRL_CLS} w-[90px]`}
            />
          </Field>

          <Field label="Max Level">
            <input
              type="number" min={0} max={135} placeholder="e.g. 70"
              value={maxLevel} onChange={e => setMaxLevel(e.target.value)}
              className={`${CTRL_CLS} w-[90px]`}
            />
          </Field>

          <Field label=" " transparent>
            <Button
              type="submit"
              variant="primary"
              disabled={loading || !hasAnyFilter}
            >
              {loading ? 'Searching…' : 'Search'}
            </Button>
          </Field>

        </div>

        {/* Row 3: stat filters */}
        {statFilters.length > 0 && (
          <div className="mb-2">
            <div className="text-[0.68rem] uppercase tracking-[0.07em] text-text-muted mb-1.5">
              Has Stats
            </div>
            <div className="flex flex-col gap-1.5">
              {statFilters.map(f => (
                <div key={f.id} className="flex gap-2 items-center">
                  {/* Stat name */}
                  <FilterDropdown
                    standalone
                    value={f.stat}
                    options={[
                      ...STAT_OPTIONS_PRIMARY.map(s => statOpt(s, 'Primary')),
                      ...STAT_OPTIONS_SECONDARY.map(s => statOpt(s, 'Secondary')),
                      ...STAT_OPTIONS_TRADESKILL.map(s => statOpt(s, 'Tradeskill')),
                    ]}
                    onChange={v => updateStatFilter(f.id, 'stat', v)}
                  />
                  {/* Operator */}
                  <FilterDropdown
                    standalone
                    value={f.op}
                    options={[
                      { value: 'gte', label: '≥' },
                      { value: 'lte', label: '≤' },
                    ]}
                    onChange={v => updateStatFilter(f.id, 'op', v)}
                  />
                  {/* Value */}
                  <input
                    type="number"
                    min={0}
                    step="any"
                    placeholder="any"
                    value={f.value}
                    onChange={e => updateStatFilter(f.id, 'value', e.target.value)}
                    className={`${CTRL_CLS} w-[90px]`}
                  />
                  {/* Remove */}
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => removeStatFilter(f.id)}
                    className="border-none text-base leading-none"
                    title="Remove"
                  >
                    ×
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )}

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={addStatFilter}
          className="border border-dashed border-border"
          style={{ marginTop: statFilters.length ? '0.3rem' : 0 }}
        >
          + Add stat filter
        </Button>

      </Card>
    </form>
  )
}

// ── Sub-component ─────────────────────────────────────────────────────────────

function Field({
  label, children, transparent,
}: { label: string; children: React.ReactNode; transparent?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <label
        className="text-[0.68rem] uppercase tracking-[0.07em] select-none"
        style={{ color: transparent ? 'transparent' : 'var(--text-muted)' }}
      >
        {label}
      </label>
      {children}
    </div>
  )
}
