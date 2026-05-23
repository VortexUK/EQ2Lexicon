import React, { useEffect, useState } from 'react'
import { StatGroup } from './CharacterPage'
import { SpellTierPip } from '../components/SpellScrollTooltip'
import {
  type SpellEntry,
  type CharacterSpellsData,
  type Ingredient,
  type UpgradeMaterialsData,
  _spellsCache,
  _materialsCache,
  SPELL_TIER_ORDER,
  SPELL_TIER_ICON,
  SPELL_TIER_COLOURS,
} from './spellConstants'

const _SPELL_TH: React.CSSProperties = {
  padding: '0.4rem 0.6rem',
  fontSize: '0.7rem',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color: 'var(--text-muted)',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  textAlign: 'left',
}
const _SPELL_TD: React.CSSProperties = {
  padding: '0.35rem 0.6rem',
  fontSize: '0.88rem',
  whiteSpace: 'nowrap',
}

// ── Spell Raid Ready card ─────────────────────────────────────────────────────

function SpellRaidReady({ expertOrBetter, totalSpells }: { expertOrBetter: number; totalSpells: number }) {
  if (totalSpells === 0) return null
  const pct       = Math.min(100, Math.round(expertOrBetter / totalSpells * 100))
  const raidReady = pct >= 90
  const color     = raidReady ? '#4ade80' : pct >= 70 ? '#fbbf24' : '#f87171'

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div style={{
        fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.08em',
        color: 'var(--accent)', fontWeight: 600, marginBottom: 3,
      }}>
        Raid Ready
      </div>
      <div style={{
        background: 'var(--surface)',
        border: `1px solid ${raidReady ? 'rgba(74,222,128,0.25)' : 'var(--border)'}`,
        borderRadius: 5,
        padding: '8px 10px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <div style={{
            fontFamily: "'Cinzel', serif",
            fontSize: '2rem', fontWeight: 700, lineHeight: 1,
            color, textShadow: `0 0 20px ${color}55`,
            flexShrink: 0, minWidth: '3ch', textAlign: 'center',
          }}>
            {pct}%
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 600, color: raidReady ? '#4ade80' : '#f87171', marginBottom: '0.2rem' }}>
              {raidReady ? '✓ Raid Ready' : '✗ Not Ready'}
            </div>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              {expertOrBetter} / {totalSpells} at Expert+
            </div>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', opacity: 0.7 }}>
              (90% required)
            </div>
          </div>
        </div>
        <div style={{ marginTop: 7, height: 4, borderRadius: 2, background: 'var(--border)', overflow: 'hidden' }}>
          <div style={{
            height: '100%', width: `${pct}%`, borderRadius: 2,
            background: color, transition: 'width 0.3s ease',
          }} />
        </div>
      </div>
    </div>
  )
}

// ── Spell progress bar ────────────────────────────────────────────────────────

function SpellProgressBar({ label, subtitle, value, total, pct, color }: {
  label:    string
  subtitle: string
  value:    number
  total:    number
  pct:      number
  color:    string
}) {
  const clamped = Math.min(100, pct)
  const done    = clamped >= 100
  return (
    <div style={{ padding: '5px 0 7px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 2 }}>
        <span style={{ fontSize: '0.78rem', fontWeight: 600, color: done ? color : 'var(--text)' }}>{label}</span>
        <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{value}/{total}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden', marginBottom: 2 }}>
        <div style={{ height: '100%', width: `${clamped}%`, borderRadius: 3, background: color, transition: 'width 0.3s' }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>{subtitle}</span>
        <span style={{ fontSize: '0.75rem', fontWeight: 700, color: done ? color : 'var(--text-muted)' }}>
          {Math.round(pct)}%
        </span>
      </div>
    </div>
  )
}

// ── Upgrade materials section ─────────────────────────────────────────────────

const _CAT_COLOUR: Record<string, string> = {
  primary:   '#c8a96e',   // gold  — the key component
  secondary: '#94a3b8',   // slate — stackable mats
  fuel:      '#64748b',   // muted — bulk fuel
}

const _TIER_COLOUR: Record<string, string> = {
  Fabled:        '#ff99ff',
  Legendary:     '#ffc993',
  Treasured:     '#93d9ff',
  Mastercrafted: '#93d9ff',
  Handcrafted:   '#beff93',
  Uncommon:      '#beff93',
  Common:        'var(--text)',
}

function IngredientTooltip({ ing }: { ing: Ingredient }) {
  const tierColour = _TIER_COLOUR[ing.tier ?? ''] ?? 'var(--text)'
  return (
    <div style={{
      position: 'absolute', zIndex: 9999,
      left: '100%', top: 0, marginLeft: 8,
      width: 220,
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
      padding: '0.6rem 0.75rem',
      pointerEvents: 'none',
    }}>
      {/* Header: icon + name */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        {ing.icon_id ? (
          <img
            src={`/icons/${ing.icon_id}.png`}
            alt=""
            width={32} height={32}
            style={{ borderRadius: 3, border: '1px solid var(--border)', flexShrink: 0 }}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <div style={{ width: 32, height: 32, borderRadius: 3, background: 'var(--border)', flexShrink: 0 }} />
        )}
        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: tierColour, lineHeight: 1.3 }}>
          {ing.name}
        </span>
      </div>
      {/* Tier badge */}
      {ing.tier && (
        <div style={{ fontSize: '0.7rem', color: tierColour, marginBottom: ing.description ? 4 : 0 }}>
          {ing.tier}
        </div>
      )}
      {/* Description */}
      {ing.description && (
        <div style={{
          fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.4,
          maxHeight: 80, overflow: 'hidden',
        }}>
          {ing.description}
        </div>
      )}
    </div>
  )
}

function IngredientRow({ ing }: { ing: Ingredient }) {
  const [hovered, setHovered] = useState(false)
  const catColour = _CAT_COLOUR[ing.category] ?? 'var(--text)'

  return (
    <div
      style={{ position: 'relative' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div style={{
        display: 'flex', alignItems: 'center', gap: 5,
        padding: '3px 0',
        borderBottom: '1px solid var(--border)',
        cursor: ing.item_id ? 'default' : undefined,
      }}>
        {/* Icon */}
        <div style={{ width: 20, height: 20, flexShrink: 0 }}>
          {ing.icon_id ? (
            <img
              src={`/icons/${ing.icon_id}.png`}
              alt=""
              width={20} height={20}
              style={{ borderRadius: 2, display: 'block' }}
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <div style={{ width: 20, height: 20, borderRadius: 2, background: 'var(--border)' }} />
          )}
        </div>
        {/* Name */}
        <span style={{
          fontSize: '0.76rem', color: catColour, flex: 1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {ing.name}
        </span>
        {/* Quantity */}
        <span style={{ fontSize: '0.82rem', fontWeight: 600, color: catColour, flexShrink: 0 }}>
          {ing.quantity.toLocaleString()}
        </span>
      </div>
      {hovered && <IngredientTooltip ing={ing} />}
    </div>
  )
}

function MaterialsSection({ charName }: { charName: string }) {
  const cacheKey = charName.toLowerCase()
  const cached   = _materialsCache.get(cacheKey)

  const [data, setData]       = useState<UpgradeMaterialsData | null>(cached ?? null)
  const [loading, setLoading] = useState(!cached)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    if (_materialsCache.has(cacheKey)) return
    let cancelled = false
    fetch(`/api/character/${encodeURIComponent(charName)}/upgrade-materials`)
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<UpgradeMaterialsData>
      })
      .then(d => {
        if (cancelled) return
        _materialsCache.set(cacheKey, d)
        setData(d)
        setLoading(false)
      })
      .catch(err => {
        if (!cancelled) {
          console.error('[upgrade-materials] fetch failed:', err)
          setError(String(err))
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [charName, cacheKey])

  if (loading) return (
    <StatGroup title="Upgrade Materials">
      <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: '4px 0' }}>Loading…</p>
    </StatGroup>
  )
  if (error || !data) return null
  if (data.spells_needing_upgrade === 0) return (
    <StatGroup title="Upgrade Materials">
      <p style={{ fontSize: '0.78rem', color: '#22c55e', margin: '4px 0' }}>
        All spells at Expert or better ✓
      </p>
    </StatGroup>
  )

  const missing = data.spells_needing_upgrade - data.spells_with_recipe

  return (
    <StatGroup title="Upgrade Materials">
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: 6, lineHeight: 1.4 }}>
        Expert recipes for{' '}
        <span style={{ color: 'var(--text)', fontWeight: 600 }}>{data.spells_with_recipe}</span>
        {' '}of{' '}
        <span style={{ color: 'var(--text)', fontWeight: 600 }}>{data.spells_needing_upgrade}</span>
        {' '}upgradeable spells
        {missing > 0 && <span style={{ color: '#f97316' }}> ({missing} no recipe)</span>}
      </div>

      {(() => {
        // Group by crafting tier: tier_num = floor(item_level / 10) + 1
        // Items with no item_level fall into tier 0 (shown last as "Unknown")
        const tierOf = (ing: Ingredient) =>
          ing.item_level != null ? Math.floor(ing.item_level / 10) + 1 : 0

        const groups = new Map<number, Ingredient[]>()
        for (const ing of data.ingredients) {
          const t = tierOf(ing)
          if (!groups.has(t)) groups.set(t, [])
          groups.get(t)!.push(ing)
        }

        // Sort groups: highest tier first; tier 0 (unknown) last
        const sortedTiers = [...groups.keys()].sort((a, b) =>
          a === 0 ? 1 : b === 0 ? -1 : b - a
        )

        return sortedTiers.map(t => (
          <div key={t} style={{ marginBottom: 6 }}>
            <div style={{
              fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.07em',
              textTransform: 'uppercase', color: 'var(--text-muted)',
              padding: '3px 0 2px',
              borderBottom: '1px solid var(--border)',
              marginBottom: 1,
            }}>
              {t === 0 ? 'Unknown' : `T${t}`}
            </div>
            {groups.get(t)!.map(ing => (
              <IngredientRow key={ing.name} ing={ing} />
            ))}
          </div>
        ))
      })()}
    </StatGroup>
  )
}

// ── Spells tab ────────────────────────────────────────────────────────────────

type SpellsTabState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; data: CharacterSpellsData }

export function SpellsTab({ charName }: { charName: string }) {
  const cacheKey = charName.toLowerCase()
  const cached   = _spellsCache.get(cacheKey)

  const [state, setState]         = useState<SpellsTabState>(
    cached ? { status: 'ok', data: cached } : { status: 'loading' }
  )
  const [search, setSearch]       = useState('')
  const [tierFilter, setTierFilter] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (_spellsCache.has(cacheKey)) return
    let cancelled = false
    fetch(`/api/character/${encodeURIComponent(charName)}/spells`)
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<CharacterSpellsData>
      })
      .then(data => {
        if (cancelled) return
        _spellsCache.set(cacheKey, data)
        setState({ status: 'ok', data })
      })
      .catch(err => { if (!cancelled) setState({ status: 'error', message: String(err) }) })
    return () => { cancelled = true }
  }, [charName, cacheKey])

  if (state.status === 'loading') {
    return <p style={{ marginTop: '1.5rem', color: 'var(--text-muted)' }}>Loading spell data…</p>
  }
  if (state.status === 'error') {
    return <p style={{ marginTop: '1.5rem', color: '#f87171' }}>Error: {state.message}</p>
  }

  const { data } = state
  const totalSpells    = data.spells.length
  const expertOrBetter = (data.tier_counts['Expert'] ?? 0) + (data.tier_counts['Master'] ?? 0) + (data.tier_counts['Grandmaster'] ?? 0)
  const masterOrBetter = (data.tier_counts['Master'] ?? 0) + (data.tier_counts['Grandmaster'] ?? 0)
  const masteredPct    = totalSpells > 0 ? masterOrBetter / totalSpells * 100 : 0

  // Filter the list
  const q = search.trim().toLowerCase()
  const filtered = data.spells.filter(s => {
    if (tierFilter.size > 0 && !tierFilter.has(s.tier)) return false
    if (q) return s.name.toLowerCase().includes(q)
    return true
  })

  function toggleTier(tier: string) {
    setTierFilter(prev => {
      const next = new Set(prev)
      next.has(tier) ? next.delete(tier) : next.add(tier)
      return next
    })
  }

  return (
    <div style={{ marginTop: '1rem', display: 'flex', gap: '1.5rem', alignItems: 'flex-start' }}>

      {/* ── Left sidebar ── */}
      <div style={{ width: 240, flexShrink: 0 }}>
        <SpellRaidReady expertOrBetter={expertOrBetter} totalSpells={totalSpells} />

        <StatGroup title="By Tier">
          {SPELL_TIER_ORDER.map(tier => {
            const count    = data.tier_counts[tier] ?? 0
            if (count === 0) return null
            const tc       = SPELL_TIER_COLOURS[tier]
            const isActive = tierFilter.has(tier)
            return (
              <div
                key={tier}
                onClick={() => toggleTier(tier)}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                  padding: '3px 0', borderBottom: '1px solid var(--border)',
                  cursor: 'pointer',
                  opacity: tierFilter.size > 0 && !isActive ? 0.35 : 1,
                  transition: 'opacity 0.12s',
                }}
              >
                <span style={{ fontSize: '0.78rem', color: tc?.text ?? 'var(--text)', fontWeight: isActive ? 700 : 400 }}>
                  {tier}
                </span>
                <span style={{
                  fontSize: '0.85rem', fontWeight: 600,
                  color: tc?.text ?? 'var(--text)',
                  background: isActive ? (tc?.bg ?? 'transparent') : 'transparent',
                  borderRadius: 3, padding: '0 4px',
                }}>
                  {count}
                </span>
              </div>
            )
          })}
          {/* Total row */}
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0 1px', marginTop: 2 }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Total</span>
            <span style={{ fontSize: '0.9rem', fontWeight: 600 }}>{totalSpells}</span>
          </div>
        </StatGroup>

        {/* Mastery progress */}
        <StatGroup title="Mastery">
          <SpellProgressBar
            label="Fully Mastered"
            subtitle="Master or better"
            value={masterOrBetter}
            total={totalSpells}
            pct={masteredPct}
            color="#22c55e"
          />
        </StatGroup>

        <MaterialsSection charName={charName} />

        {tierFilter.size > 0 && (
          <button
            onClick={() => setTierFilter(new Set())}
            style={{
              width: '100%', padding: '4px 0', fontSize: '0.75rem',
              color: 'var(--text-muted)', background: 'none',
              border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer',
              marginTop: 4,
            }}
          >
            Clear tier filter
          </button>
        )}
      </div>

      {/* ── Right: spell list (2 columns) ── */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <input
          type="text"
          placeholder="Search spells…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ marginBottom: '0.75rem', width: 260, boxSizing: 'border-box' }}
        />

        {filtered.length === 0 ? (
          <div style={{
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
            padding: '1.5rem', color: 'var(--text-muted)', textAlign: 'center', fontSize: '0.88rem',
          }}>
            No spells match your filter.
          </div>
        ) : (() => {
          const mid = Math.ceil(filtered.length / 2)
          const cols = [filtered.slice(0, mid), filtered.slice(mid)]

          const renderTable = (rows: SpellEntry[]) => (
            <div style={{
              background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
              overflow: 'hidden', flex: 1, minWidth: 0,
            }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '2px solid var(--border)', background: 'var(--surface-raised, var(--surface))' }}>
                    <th style={{ ..._SPELL_TH, width: 36, textAlign: 'right' }}>Lvl</th>
                    <th style={_SPELL_TH}>Name</th>
                    <th style={{ ..._SPELL_TH, textAlign: 'right', paddingRight: '0.5rem' }}>Tier</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ ..._SPELL_TD, textAlign: 'right', color: 'var(--text-muted)', fontSize: '0.8rem', width: 36 }}>
                        {s.level}
                      </td>
                      <td style={{ ..._SPELL_TD, fontWeight: 500 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                          {(s.icon_id != null || s.icon_backdrop != null) && (
                            <div style={{ position: 'relative', width: 18, height: 18, flexShrink: 0 }}>
                              {s.icon_backdrop != null && s.icon_backdrop > 0 && (
                                <img
                                  src={`/spell-icons/${s.icon_backdrop}.png`}
                                  alt=""
                                  style={{ position: 'absolute', inset: 0, width: 18, height: 18 }}
                                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                                />
                              )}
                              {s.icon_id != null && s.icon_id > 0 && (
                                <img
                                  src={`/spell-icons/${s.icon_id}.png`}
                                  alt=""
                                  style={{ position: 'absolute', inset: 0, width: 18, height: 18 }}
                                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                                />
                              )}
                            </div>
                          )}
                          <span style={{ fontSize: '0.82rem' }}>{s.name}</span>
                        </div>
                      </td>
                      <td style={{ ..._SPELL_TD, textAlign: 'right', paddingRight: '0.5rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 1 }}>
                          {SPELL_TIER_ORDER.map(t => {
                            const base = SPELL_TIER_ICON[t]
                            const filename = t === s.tier ? `${base}-lit.png` : `${base}.png`
                            return (
                              <SpellTierPip
                                key={t}
                                src={`/spell-icons/${filename}`}
                                tier={t}
                                spellName={s.name}
                              />
                            )
                          })}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )

          return (
            <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
              {cols.map((col, ci) => <React.Fragment key={ci}>{renderTable(col)}</React.Fragment>)}
            </div>
          )
        })()}
      </div>
    </div>
  )
}
