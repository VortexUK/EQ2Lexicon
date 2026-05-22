import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'

// ── Stat options (canonical display names from STAT_MAP) ──────────────────────

const STAT_OPTIONS_PRIMARY = [
  'Stamina',
  'Primary Attributes',
  'Combat Skills',
  'Resistances',
]

const STAT_OPTIONS_SECONDARY = [
  'Ability Mod',
  'Potency',
  'Crit Bonus',
  'Crit Chance',
  'Casting Speed',
  'Max Health',
  'Max Power',
  'Haste',
  'DPS',
  'Multi Attack',
  'Strikethrough',
  'Accuracy',
  'Flurry',
  'Block',
  'Parry',
  'Deflection',
  'Dodge',
  'Spell Aversion',
  'Critical Avoidance',
  'Overcap Bonus',
  'Weapon Skill',
  'AE Auto Attack',
  'Spell Dmg Bonus',
  'Attack Speed',
]

// ── Quality colour map ────────────────────────────────────────────────────────

const TIER_COLOUR: Record<string, string> = {
  'Fabled':       '#ff99ff',
  'Legendary':    '#ffc993',
  'Treasured':    '#93d9ff',
  'Mastercrafted':'#93d9ff',
  'Handcrafted':  '#beff93',
  'COMMON':       'var(--text-muted)',
}

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

// ── Types ─────────────────────────────────────────────────────────────────────

interface StatFilter {
  id:       number
  stat:     string
  minValue: string
}

interface ItemSearchResult {
  id:               number
  name:             string
  tier:             string | null
  slot:             string | null
  item_type:        string | null
  level:            number | null
  class_label:      string | null
  icon_id:          number | null
  stats:            string[]
  stat_values:      Record<string, number>
}

interface ItemSearchResponse {
  results:  ItemSearchResult[]
  total:    number
  page:     number
  per_page: number
}

interface FilterOptions {
  tiers:      string[]
  slots:      string[]
  item_types: string[]
}

// ── Shared control style ──────────────────────────────────────────────────────

const CTRL: React.CSSProperties = {
  padding:      '0.42rem 0.6rem',
  borderRadius: 6,
  border:       '1px solid var(--border)',
  background:   'var(--surface-raised)',
  color:        'var(--text)',
  fontSize:     '0.88rem',
  lineHeight:   '1.4',
  colorScheme:  'dark',
}

// ── Table header / cell styles ────────────────────────────────────────────────

const TH: React.CSSProperties = {
  padding:       '0.5rem 0.7rem',
  fontSize:      '0.72rem',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color:         'var(--text-muted)',
  fontWeight:    600,
  whiteSpace:    'nowrap',
  textAlign:     'left',
  borderBottom:  '2px solid var(--border)',
  background:    'var(--surface-raised)',
}

const TD: React.CSSProperties = {
  padding:      '0.42rem 0.7rem',
  fontSize:     '0.85rem',
  whiteSpace:   'nowrap',
  borderBottom: '1px solid var(--border)',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

let _statFilterId = 0
function nextId() { return ++_statFilterId }

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ItemSearchPage() {
  // Filter state
  const [name,       setName]       = useState('')
  const [tier,       setTier]       = useState('')
  const [slot,       setSlot]       = useState('')
  const [itemType,   setItemType]   = useState('')
  const [classVal,   setClassVal]   = useState('')
  const [minLevel,   setMinLevel]   = useState('')
  const [maxLevel,   setMaxLevel]   = useState('')
  const [statFilters, setStatFilters] = useState<StatFilter[]>([])
  const [sortBy,     setSortBy]     = useState<string>('name')
  const [sortDir,    setSortDir]    = useState<'asc' | 'desc'>('asc')
  const [page,       setPage]       = useState(1)

  // Results state
  const [results,  setResults]  = useState<ItemSearchResponse | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  // Filter options from server
  const [filterOpts, setFilterOpts] = useState<FilterOptions>({ tiers: [], slots: [], item_types: [] })

  useEffect(() => {
    fetch('/api/items/filters', { credentials: 'include' })
      .then(r => r.json())
      .then(setFilterOpts)
      .catch(() => {})
  }, [])

  // ── Stat filter management ──────────────────────────────────────────────────

  function addStatFilter() {
    const newFilter = { id: nextId(), stat: STAT_OPTIONS_SECONDARY[0], minValue: '' }
    // First stat filter added → start sorting by it descending
    if (statFilters.length === 0) {
      setSortBy(newFilter.stat)
      setSortDir('desc')
    }
    setStatFilters(prev => [...prev, newFilter])
  }

  function removeStatFilter(id: number) {
    const removed  = statFilters.find(f => f.id === id)
    const remaining = statFilters.filter(f => f.id !== id)
    if (removed && sortBy === removed.stat) {
      // Switch sort to the next available stat filter, or fall back to name
      if (remaining.length > 0) {
        setSortBy(remaining[0].stat)
        setSortDir('desc')
      } else {
        setSortBy('name')
        setSortDir('asc')
      }
    } else if (remaining.length === 0) {
      // All filters removed — reset to name sort regardless
      setSortBy('name')
      setSortDir('asc')
    }
    setStatFilters(prev => prev.filter(f => f.id !== id))
  }

  function updateStatFilter(id: number, field: 'stat' | 'minValue', value: string) {
    // If renaming the stat we're currently sorting by, keep sort in sync
    if (field === 'stat' && sortBy === statFilters.find(f => f.id === id)?.stat) {
      setSortBy(value)
    }
    setStatFilters(prev => prev.map(f => f.id === id ? { ...f, [field]: value } : f))
  }

  // ── Search ──────────────────────────────────────────────────────────────────

  async function runSearch(p = 1) {
    const params = new URLSearchParams()
    if (name.trim())     params.set('name',      name.trim())
    if (tier)            params.set('tier',       tier)
    if (slot)            params.set('slot',       slot)
    if (itemType)        params.set('item_type',  itemType)
    if (minLevel.trim()) params.set('min_level',  minLevel.trim())
    if (maxLevel.trim()) params.set('max_level',  maxLevel.trim())
    params.set('sort_by',  sortBy)
    params.set('sort_dir', sortDir)
    params.set('page',     String(p))

    // Class — if multi-class shortcut, use the first class as a presence check
    // The backend filters by any single class name present in classes_json
    if (classVal && !classVal.includes(',')) {
      params.set('class_name', classVal)
    } else if (classVal && classVal.includes(',')) {
      // For archetype shortcuts we send the first class name to match "All Fighters" etc.
      // The DB stores classes_json with all individual class keys, so matching one is enough
      // for "usable by at least one class in the archetype" — acceptable UX for v1
      params.set('class_name', classVal.split(',')[0].trim())
    }

    // Stat filters
    for (const f of statFilters) {
      if (f.stat) params.append('has_stat', f.stat)
    }

    setLoading(true)
    setError(null)
    setSearched(true)
    try {
      const res = await fetch(`/api/items/search?${params}`, { credentials: 'include' })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`
        setError(detail)
        return
      }
      setResults(await res.json())
      setPage(p)
    } catch {
      setError('Network error — please try again.')
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    runSearch(1)
  }

  const hasAnyFilter = !!(
    name.trim() || tier || slot || itemType || classVal ||
    minLevel.trim() || maxLevel.trim() || statFilters.length
  )

  const totalPages = results ? Math.ceil(results.total / results.per_page) : 0

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <main style={{ maxWidth: 1100, margin: '0 auto', padding: '2rem 1.5rem 4rem' }}>
      <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem', textDecoration: 'none' }}>
        ← Back
      </Link>

      <h1 style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '1.9rem', fontWeight: 700, letterSpacing: '0.06em',
        margin: '1rem 0 0.25rem',
        background: 'linear-gradient(135deg, #c8a96e 0%, #e8d5a3 40%, #c8a96e 70%, #a07840 100%)',
        WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
        backgroundClip: 'text', display: 'inline-block',
      }}>
        Item Search
      </h1>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', marginBottom: '1.5rem' }}>
        Search the local item database by name, quality, slot, class, level and stats.
      </p>

      {/* ── Filter form ───────────────────────────────────────────────────── */}
      <form onSubmit={handleSubmit}>
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 8, padding: '1rem 1.1rem', marginBottom: '1.25rem',
        }}>

          {/* Row 1: name + tier + type + slot + class */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'flex-end', marginBottom: '0.75rem' }}>

            <Field label="Name">
              <input
                type="text"
                placeholder="Search name…"
                value={name}
                onChange={e => setName(e.target.value)}
                style={{ ...CTRL, width: 180 }}
              />
            </Field>

            <Field label="Quality">
              <select value={tier} onChange={e => setTier(e.target.value)} style={{ ...CTRL, minWidth: 130 }}>
                <option value="">Any</option>
                {filterOpts.tiers.map(t => (
                  <option key={t} value={t}>{t === 'COMMON' ? 'Common' : t}</option>
                ))}
              </select>
            </Field>

            <Field label="Item Type">
              <select value={itemType} onChange={e => setItemType(e.target.value)} style={{ ...CTRL, minWidth: 140 }}>
                <option value="">Any</option>
                {filterOpts.item_types.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </Field>

            <Field label="Slot">
              <select value={slot} onChange={e => setSlot(e.target.value)} style={{ ...CTRL, minWidth: 130 }}>
                <option value="">Any</option>
                {filterOpts.slots.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </Field>

            <Field label="Class">
              <select
                value={classVal}
                onChange={e => {
                  if (e.target.value !== '__hdr') setClassVal(e.target.value)
                }}
                style={{ ...CTRL, minWidth: 160 }}
              >
                {CLASS_OPTIONS.map((opt, i) => (
                  <option
                    key={i}
                    value={opt.value}
                    disabled={opt.value === '__hdr'}
                    style={opt.value === '__hdr' ? { color: 'var(--text-muted)', fontWeight: 700 } : {}}
                  >
                    {opt.label}
                  </option>
                ))}
              </select>
            </Field>

          </div>

          {/* Row 2: levels + search */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'flex-end', marginBottom: '0.75rem' }}>

            <Field label="Min Level">
              <input
                type="number" min={1} max={135} placeholder="e.g. 70"
                value={minLevel} onChange={e => setMinLevel(e.target.value)}
                style={{ ...CTRL, width: 90 }}
              />
            </Field>

            <Field label="Max Level">
              <input
                type="number" min={1} max={135} placeholder="e.g. 70"
                value={maxLevel} onChange={e => setMaxLevel(e.target.value)}
                style={{ ...CTRL, width: 90 }}
              />
            </Field>

            <Field label=" " transparent>
              <button
                type="submit"
                disabled={loading || !hasAnyFilter}
                style={{
                  ...CTRL,
                  cursor:     loading || !hasAnyFilter ? 'not-allowed' : 'pointer',
                  opacity:    loading || !hasAnyFilter ? 0.45 : 1,
                  padding:    '0.42rem 1.4rem',
                  border:     '1px solid rgba(var(--accent-rgb,99,210,130),0.4)',
                  background: 'rgba(var(--accent-rgb,99,210,130),0.12)',
                  color:      'var(--accent)',
                  fontWeight: 600,
                }}
              >
                {loading ? 'Searching…' : 'Search'}
              </button>
            </Field>

          </div>

          {/* Row 3: stat filters */}
          {statFilters.length > 0 && (
            <div style={{ marginBottom: '0.5rem' }}>
              <div style={{
                fontSize: '0.68rem', textTransform: 'uppercase',
                letterSpacing: '0.07em', color: 'var(--text-muted)',
                marginBottom: '0.4rem',
              }}>
                Has Stats
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                {statFilters.map(f => (
                  <div key={f.id} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                    <select
                      value={f.stat}
                      onChange={e => updateStatFilter(f.id, 'stat', e.target.value)}
                      style={{ ...CTRL, minWidth: 180 }}
                    >
                      <optgroup label="Primary">
                        {STAT_OPTIONS_PRIMARY.map(s => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </optgroup>
                      <optgroup label="Secondary">
                        {STAT_OPTIONS_SECONDARY.map(s => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </optgroup>
                    </select>
                    <input
                      type="number"
                      min={0}
                      placeholder="any value"
                      value={f.minValue}
                      onChange={e => updateStatFilter(f.id, 'minValue', e.target.value)}
                      style={{ ...CTRL, width: 100 }}
                    />
                    <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                      {f.minValue ? `≥ ${f.minValue}` : '(present)'}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeStatFilter(f.id)}
                      style={{
                        background: 'none', border: 'none',
                        color: '#f87171', cursor: 'pointer', fontSize: '1rem', lineHeight: 1,
                      }}
                      title="Remove"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            type="button"
            onClick={addStatFilter}
            style={{
              background: 'none', border: '1px dashed var(--border)',
              borderRadius: 5, color: 'var(--text-muted)', cursor: 'pointer',
              fontSize: '0.8rem', padding: '0.25rem 0.75rem',
              marginTop: statFilters.length ? '0.3rem' : 0,
            }}
          >
            + Add stat filter
          </button>

        </div>
      </form>

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && <p style={{ color: '#f87171', marginBottom: '1rem' }}>{error}</p>}

      {/* ── Prompt ─────────────────────────────────────────────────────────── */}
      {!searched && !loading && (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
          Set at least one filter to search.
        </p>
      )}

      {/* ── Results ────────────────────────────────────────────────────────── */}
      {searched && !loading && !error && results && (
        <>
          <ResultsHeader
            total={results.total} page={page} totalPages={totalPages}
            onPrev={() => runSearch(page - 1)} onNext={() => runSearch(page + 1)}
          />

          {results.total > 0 && <ItemTable
            items={results.results}
            sortBy={sortBy}
            sortDir={sortDir}
            statFilters={statFilters}
            onSortByStat={(stat) => {
              if (sortBy === stat) {
                setSortDir(d => d === 'asc' ? 'desc' : 'asc')
              } else {
                setSortBy(stat)
                setSortDir('desc')
              }
            }}
          />}

          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.4rem', marginTop: '0.75rem' }}>
              <button onClick={() => runSearch(page - 1)} disabled={page <= 1}         style={PAGIN_BTN}>← Prev</button>
              <button onClick={() => runSearch(page + 1)} disabled={page >= totalPages} style={PAGIN_BTN}>Next →</button>
            </div>
          )}
        </>
      )}
    </main>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Field({
  label, children, transparent,
}: { label: string; children: React.ReactNode; transparent?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <label style={{
        fontSize: '0.68rem', textTransform: 'uppercase',
        letterSpacing: '0.07em',
        color: transparent ? 'transparent' : 'var(--text-muted)',
        userSelect: 'none',
      }}>
        {label}
      </label>
      {children}
    </div>
  )
}

function ResultsHeader({
  total, page, totalPages, onPrev, onNext,
}: { total: number; page: number; totalPages: number; onPrev: () => void; onNext: () => void }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      marginBottom: '0.6rem', flexWrap: 'wrap', gap: '0.5rem',
    }}>
      <span style={{ fontSize: '0.83rem', color: 'var(--text-muted)' }}>
        {total === 0
          ? 'No items found.'
          : `${total.toLocaleString()} item${total === 1 ? '' : 's'} found`}
        {totalPages > 1 && ` · page ${page} of ${totalPages}`}
      </span>
      {totalPages > 1 && (
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <button onClick={onPrev} disabled={page <= 1}         style={PAGIN_BTN}>← Prev</button>
          <button onClick={onNext} disabled={page >= totalPages} style={PAGIN_BTN}>Next →</button>
        </div>
      )}
    </div>
  )
}

function StatPills({ stats, highlight }: { stats: string[]; highlight: string[] }) {
  if (!stats.length) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  const highlightSet = new Set(highlight)
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.2rem' }}>
      {stats.map(s => (
        <span
          key={s}
          style={{
            display:      'inline-block',
            padding:      '0.1rem 0.35rem',
            borderRadius: 3,
            fontSize:     '0.72rem',
            background:   highlightSet.has(s)
              ? 'rgba(var(--accent-rgb,99,210,130),0.18)'
              : 'var(--surface-raised)',
            color: highlightSet.has(s)
              ? 'var(--accent)'
              : 'var(--text-muted)',
            border: highlightSet.has(s)
              ? '1px solid rgba(var(--accent-rgb,99,210,130),0.35)'
              : '1px solid var(--border)',
          }}
        >
          {s}
        </span>
      ))}
    </div>
  )
}

function ItemTable({
  items, sortBy, sortDir, statFilters, onSortByStat,
}: {
  items: ItemSearchResult[]
  sortBy: string
  sortDir: 'asc' | 'desc'
  statFilters: StatFilter[]
  onSortByStat: (stat: string) => void
}) {
  // Show one column per active stat filter, capped at 3
  const statCols = statFilters.filter(f => f.stat).slice(0, 3)

  return (
    <div style={{ overflowX: 'auto', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>Name</th>
            <th style={TH}>Quality</th>
            <th style={TH}>Slot</th>
            <th style={{ ...TH, textAlign: 'right' }}>Level</th>
            {statCols.map(f => {
              const active = sortBy === f.stat
              return (
                <th
                  key={f.id}
                  style={{
                    ...TH,
                    textAlign: 'right',
                    cursor: 'pointer',
                    userSelect: 'none',
                    color: active ? 'var(--accent)' : 'var(--text-muted)',
                    whiteSpace: 'nowrap',
                  }}
                  onClick={() => onSortByStat(f.stat)}
                  title={`Sort by ${f.stat}`}
                >
                  {f.stat}&thinsp;{active ? (sortDir === 'desc' ? '↓' : '↑') : '↕'}
                </th>
              )
            })}
            <th style={TH}>Classes</th>
            <th style={TH}>Stats</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr
              key={item.id}
              style={{ transition: 'background 0.1s' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-raised)')}
              onMouseLeave={e => (e.currentTarget.style.background = '')}
            >
              <td style={TD}>
                <Link
                  to={`/item/${item.id}`}
                  style={{ color: TIER_COLOUR[item.tier ?? ''] ?? 'var(--accent)', textDecoration: 'none', fontWeight: 500 }}
                >
                  {item.name}
                </Link>
              </td>
              <td style={{ ...TD, color: TIER_COLOUR[item.tier ?? ''] ?? 'var(--text-muted)', fontSize: '0.8rem', fontWeight: 500 }}>
                {item.tier === 'COMMON' ? 'Common' : (item.tier ?? '—')}
              </td>
              <td style={{ ...TD, color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                {item.slot ?? (item.item_type ?? '—')}
              </td>
              <td style={{ ...TD, textAlign: 'right' }}>
                {item.level ?? '—'}
              </td>
              {statCols.map(f => {
                const val = item.stat_values[f.stat]
                const active = sortBy === f.stat
                return (
                  <td
                    key={f.id}
                    style={{
                      ...TD,
                      textAlign: 'right',
                      fontWeight: active ? 600 : undefined,
                      color: active ? 'var(--accent)' : undefined,
                    }}
                  >
                    {val != null
                      ? val
                      : <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>—</span>}
                  </td>
                )
              })}
              <td style={{ ...TD, color: 'var(--text-muted)', fontSize: '0.8rem', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {item.class_label ?? '—'}
              </td>
              <td style={{ ...TD, fontSize: '0.78rem', color: 'var(--text-muted)', maxWidth: 260 }}>
                <StatPills stats={item.stats} highlight={statFilters.map(f => f.stat)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const PAGIN_BTN: React.CSSProperties = {
  padding: '0.3rem 0.8rem', borderRadius: 5,
  border: '1px solid var(--border)', background: 'var(--surface)',
  color: 'var(--text-muted)', cursor: 'pointer', fontSize: '0.82rem',
}
