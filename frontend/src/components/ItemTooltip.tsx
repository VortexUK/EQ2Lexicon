/**
 * HTML tooltip that mirrors the PIL renderer in image/tooltip.py.
 * Colours, layout order, and section logic are kept in sync with that file.
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

// ── Types (mirror web/routes/item.py) ─────────────────────────────────────────

interface ItemStat {
  display_name: string
  value: number
  stat_group: string
}

interface EffectLine {
  indentation: number
  text: string
}

interface ItemEffect {
  name: string
  trigger: string
  lines: EffectLine[]
}

interface ItemDetail {
  id: string
  name: string
  quality: string
  description: string
  icon_id: string | null
  slot_type: string
  armor_type: string
  mitigation: number | null
  item_level: number | null
  required_level: number | null
  container_slots: number | null
  classes_label: string
  stats: ItemStat[]
  effects: ItemEffect[]
  adornment_slots: string[]
  flags: string[]
  extra_info: [string, string][]
}

export interface TooltipState {
  itemId: string
  x: number
  y: number
}

// ── Colours (from tooltip.py) ─────────────────────────────────────────────────

const BG           = '#0a0a0e'
const BORDER_OUTER = '#c49e2c'   // golden amber
const BORDER_INNER = '#364c5c'   // slate teal
const C_BODY       = '#c7cfc7'
const C_NAME       = '#e8e8e8'
const C_PRIMARY    = '#22ff22'
const C_SECONDARY  = '#3cc0c0'
const C_GOLD       = '#e6e970'
const C_WHITE      = '#dcdcdc'

const _outline = '-1px 0 0 #000, 0 1px 0 #000, 1px 0 0 #000, 0 -1px 0 #000'
function glow(color: string) {
  return `${_outline}, 0 0 4px ${color}, 0 0 4px ${color}`
}

interface QualityStyle { color: string; glowColor?: string }
const QUALITY: Record<string, QualityStyle> = {
  fabled:        { color: '#ff939d', glowColor: '#df535f' },
  legendary:     { color: '#ffc993', glowColor: '#D56900' },
  treasured:     { color: '#93d9ff', glowColor: '#D56900' },
  mastercrafted: { color: '#93d9ff', glowColor: '#D56900' },
  uncommon:      { color: '#beff93' },
  handcrafted:   { color: '#beff93' },
  common:        { color: '#beff93' },
}
function qualityStyle(quality: string): QualityStyle {
  return QUALITY[quality.toLowerCase()] ?? { color: C_WHITE }
}

const ADORN_COLOR: Record<string, string> = {
  white:     '#dcdcdc',
  turquoise: '#3cc0c0',
  orange:    '#ff8a00',
  red:       '#d73737',
  blue:      '#5076da',
  green:     '#37c037',
  yellow:    '#d7c037',
  purple:    '#b04eda',
  black:     '#a0a0a0',
}

// ── Cache ─────────────────────────────────────────────────────────────────────

const _cache = new Map<string, ItemDetail>()

// ── Tooltip portal ────────────────────────────────────────────────────────────

const TIP_W = 360

export function ItemTooltip({ state }: { state: TooltipState }) {
  const [item, setItem] = useState<ItemDetail | null>(_cache.get(state.itemId) ?? null)
  const [loading, setLoading] = useState(!_cache.has(state.itemId))

  useEffect(() => {
    if (_cache.has(state.itemId)) {
      setItem(_cache.get(state.itemId)!)
      setLoading(false)
      return
    }
    setLoading(true)
    fetch(`/api/item/${state.itemId}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: ItemDetail) => { _cache.set(state.itemId, data); setItem(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [state.itemId])

  const MARGIN = 12
  const x = state.x + 16 + TIP_W > window.innerWidth ? state.x - TIP_W - 8 : state.x + 16
  const y = Math.max(MARGIN, state.y - 8)

  const qs = item ? qualityStyle(item.quality) : null

  return createPortal(
    <div style={{
      position: 'fixed', left: x, top: y, width: TIP_W, zIndex: 9999,
      pointerEvents: 'none', userSelect: 'none',
      fontFamily: '"Times New Roman", Times, serif',
      background: BG,
      border: `2px solid ${BORDER_OUTER}`,
      boxShadow: `inset 0 0 0 1px ${BORDER_INNER}, 0 8px 32px rgba(0,0,0,0.9)`,
      borderRadius: 2,
      padding: 6,
    }}>
      {/* Inner slate teal border */}
      <div style={{ border: `1px solid ${BORDER_INNER}`, padding: '8px 10px' }}>
        {loading && <div style={{ color: '#777', fontSize: '0.82rem' }}>Loading…</div>}
        {!loading && !item && <div style={{ color: '#f87171', fontSize: '0.82rem' }}>Item not found</div>}
        {item && <TooltipContent item={item} qs={qs!} />}
      </div>
    </div>,
    document.body,
  )
}

// ── Main content ──────────────────────────────────────────────────────────────

function TooltipContent({ item, qs }: { item: ItemDetail; qs: QualityStyle }) {
  const primary   = item.stats.filter(s => s.stat_group === 'primary')
  const secondary = item.stats.filter(s => s.stat_group === 'secondary')
  const isAdorn   = item.armor_type.toLowerCase().includes('adornment')

  return (
    <div style={{ fontSize: '0.83rem', lineHeight: 1.4, color: C_BODY }}>

      {/* ── Header: name + icon ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 4 }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: C_NAME, fontWeight: 'bold', fontSize: '0.97rem', lineHeight: 1.25 }}>
            {item.name}
          </div>
          {item.description && (
            <div style={{ color: C_BODY, marginTop: 3 }}>{item.description}</div>
          )}
          {item.container_slots != null && (
            <KV label="Slots" value={String(item.container_slots)} />
          )}
        </div>
        {item.icon_id && (
          <div style={{
            width: 46, height: 46, flexShrink: 0,
            backgroundImage: "url('/slot-empty-blue.png')",
            backgroundSize: 'cover',
            border: `1px solid ${BORDER_INNER}`,
            borderRadius: 2, overflow: 'hidden',
          }}>
            <img src={`/icons/${item.icon_id}.png`} alt=""
              style={{ width: 46, height: 46, display: 'block' }}
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
          </div>
        )}
      </div>

      {/* ── Quality badge ── */}
      {item.quality && (
        <div style={{
          color: qs.color,
          fontWeight: 'bold', fontSize: '0.9rem',
          textShadow: qs.glowColor ? glow(qs.glowColor) : undefined,
          marginBottom: 6,
        }}>
          {item.quality.toUpperCase()}
        </div>
      )}

      {/* ── Adornment item preamble ── */}
      {isAdorn && (
        <div style={{ color: C_BODY, marginBottom: 4 }}>Adds the following to an item:</div>
      )}

      {/* ── Primary stats (bright green, 2-col) ── */}
      {primary.length > 0 && (
        <Section>
          <StatCols stats={primary} color={C_PRIMARY} />
        </Section>
      )}

      {/* ── Secondary stats (cyan, 2-col) ── */}
      {secondary.length > 0 && (
        <Section>
          <StatCols stats={secondary} color={C_SECONDARY} />
        </Section>
      )}

      {/* ── Item properties block ── */}
      {(item.armor_type || item.slot_type || item.mitigation || item.item_level != null || item.extra_info.length > 0) && (
        <Section>
          {item.armor_type && <KV label="Type"       value={item.armor_type} />}
          {item.slot_type  && <KV label="Slot"       value={item.slot_type} />}
          {item.mitigation != null && <KV label="Mitigation" value={item.mitigation.toLocaleString()} />}
          {item.item_level != null && <KV label="Level"      value={String(item.item_level)} valueColor={C_PRIMARY} />}
          {item.extra_info.map(([label, val]) => (
            <KV key={label} label={label} value={val} />
          ))}
        </Section>
      )}

      {/* ── Class restrictions ── */}
      {item.classes_label && (
        <div style={{ color: C_PRIMARY, fontWeight: 'bold', marginTop: 5 }}>
          {item.classes_label}
        </div>
      )}

      {/* ── Effects ── */}
      {item.effects.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', marginBottom: 4 }}>Effects:</div>
          {item.effects.map((eff, i) => (
            <EffectBlock key={i} eff={eff} qs={qs} />
          ))}
        </Section>
      )}

      {/* ── Adornment slots ── */}
      {item.adornment_slots.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', marginBottom: 3 }}>Adornment Slots:</div>
          <div>
            {item.adornment_slots.map((slot, i) => (
              <span key={i}>
                <span style={{ color: ADORN_COLOR[slot.toLowerCase()] ?? C_WHITE }}>{slot}</span>
                {i < item.adornment_slots.length - 1 && <span style={{ color: C_WHITE }}>, </span>}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* ── Flags ── */}
      {item.flags.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', letterSpacing: '0.04em' }}>
            {item.flags.join('   ')}
          </div>
        </Section>
      )}

    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Section({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ borderTop: `1px solid ${BORDER_INNER}`, marginTop: 6, paddingTop: 5 }}>
      {children}
    </div>
  )
}

function KV({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ display: 'flex', gap: 0 }}>
      <span style={{ color: C_WHITE, minWidth: 90 }}>{label}</span>
      <span style={{ color: valueColor ?? C_SECONDARY }}>{value}</span>
    </div>
  )
}

function StatCols({ stats, color }: { stats: ItemStat[]; color: string }) {
  const rows: [ItemStat, ItemStat | null][] = []
  for (let i = 0; i < stats.length; i += 2) {
    rows.push([stats[i], stats[i + 1] ?? null])
  }
  return (
    <>
      {rows.map(([a, b], i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 4px' }}>
          <span style={{ color, fontWeight: 'bold' }}>{fmtStat(a)}</span>
          {b && <span style={{ color, fontWeight: 'bold' }}>{fmtStat(b)}</span>}
        </div>
      ))}
    </>
  )
}

function EffectBlock({ eff, qs }: { eff: ItemEffect; qs: QualityStyle }) {
  const effColor = qs.color ?? '#ff939d'
  const effGlow  = qs.glowColor

  return (
    <div style={{ marginBottom: 5 }}>
      <div style={{
        color: effColor, fontWeight: 'bold',
        textShadow: effGlow ? glow(effGlow) : undefined,
      }}>
        {eff.name}
      </div>
      {eff.trigger && (
        <div style={{ color: C_BODY, fontStyle: 'italic', fontSize: '0.8rem' }}>{eff.trigger}</div>
      )}
      {eff.lines.map((ln, j) => (
        <div key={j} style={{
          color: C_BODY, fontSize: '0.8rem',
          paddingLeft: `${Math.max(0, ln.indentation - 1) * 10}px`,
        }}>
          {'• '}{ln.text}
        </div>
      ))}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtStat(s: ItemStat): string {
  const v = s.value
  return `${Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1)} ${s.display_name}`
}
