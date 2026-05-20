import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'

interface EquipmentSlot {
  slot: string
  name: string
  item_id: string | null
  icon_id: string | null
  tier: string | null
}

interface Character {
  id: string
  name: string
  level: number | null
  cls: string | null
  race: string | null
  gender: string | null
  deity: string | null
  aa_count: number
  world: string
  ts_class: string | null
  ts_level: number | null
  equipment: EquipmentSlot[]
}

// Canonical display name → internal slot name mapping, in paperdoll order
const LEFT_SLOTS: [string, string][] = [
  ['Charm',      'activate1'],
  ['Charm',      'activate2'],
  ['Head',       'head'],
  ['Neck',       'neck'],
  ['Shoulders',  'shoulders'],
  ['Cloak',      'cloak'],
  ['Chest',      'chest'],
  ['Forearms',   'forearms'],
  ['Hands',      'hands'],
  ['Waist',      'waist'],
  ['Legs',       'legs'],
  ['Feet',       'feet'],
]

const RIGHT_SLOTS: [string, string][] = [
  ['Primary',    'primary'],
  ['Secondary',  'secondary'],
  ['Ranged',     'ranged'],
  ['Wrist',      'left_wrist'],
  ['Wrist',      'right_wrist'],
  ['Finger',     'left_ring'],
  ['Finger',     'right_ring'],
  ['Ear',        'ears'],
  ['Ear',        'ears2'],
]

const TIER_COLOUR: Record<string, string> = {
  FABLED:    'var(--tier-fabled)',
  LEGENDARY: 'var(--tier-legendary)',
  TREASURED: 'var(--tier-treasured)',
  UNCOMMON:  'var(--tier-uncommon)',
  COMMON:    'var(--tier-common)',
}

function tierColour(tier: string | null): string {
  return TIER_COLOUR[(tier ?? '').toUpperCase()] ?? 'var(--text-muted)'
}

type State =
  | { status: 'loading' }
  | { status: 'ok'; char: Character }
  | { status: 'not_found'; name: string }
  | { status: 'error'; message: string }

export default function CharacterPage() {
  const { name } = useParams<{ name: string }>()
  const [state, setState] = useState<State>({ status: 'loading' })

  useEffect(() => {
    if (!name) return
    setState({ status: 'loading' })
    fetch(`/api/character/${encodeURIComponent(name)}`, { credentials: 'include' })
      .then(async res => {
        if (res.status === 404) { setState({ status: 'not_found', name }); return }
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          setState({ status: 'error', message: body.detail ?? `HTTP ${res.status}` })
          return
        }
        setState({ status: 'ok', char: await res.json() })
      })
      .catch(err => setState({ status: 'error', message: String(err) }))
  }, [name])

  return (
    <main style={{ maxWidth: 900, margin: '2rem auto', padding: '0 1rem' }}>
      <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>← Back</Link>

      {state.status === 'loading' && <p style={{ marginTop: '2rem', color: 'var(--text-muted)' }}>Loading…</p>}
      {state.status === 'not_found' && <p style={{ marginTop: '2rem', color: 'var(--text-muted)' }}>Character <strong>{state.name}</strong> not found.</p>}
      {state.status === 'error' && <p style={{ marginTop: '2rem', color: '#f87171' }}>Error: {state.message}</p>}
      {state.status === 'ok' && <CharacterView char={state.char} />}
    </main>
  )
}

function CharacterView({ char }: { char: Character }) {
  // Map item_id → slot so we can look up by the canonical internal key.
  // The API's slot field is the Census displayname (e.g. "Primary", "Finger").
  // We index by item_id to avoid display-name collisions (two "Finger" slots, two "Ear" etc.)
  // and build a separate ordered list keyed by our canonical slot names.
  const byInternalKey = new Map<string, EquipmentSlot>()
  // Census slot displayname → internal key mapping (matches LEFT_SLOTS / RIGHT_SLOTS)
  const DISPLAY_TO_KEY: Record<string, string> = {
    'Primary': 'primary', 'Secondary': 'secondary', 'Ranged': 'ranged',
    'Head': 'head', 'Chest': 'chest', 'Shoulders': 'shoulders',
    'Forearms': 'forearms', 'Hands': 'hands', 'Legs': 'legs',
    'Feet': 'feet', 'Waist': 'waist', 'Neck': 'neck', 'Cloak': 'cloak',
    'Charm': 'activate',   // handled below with counter
    'Finger': 'ring',      // handled below with counter
    'Ear': 'ear',          // handled below with counter
    'Wrist': 'wrist',      // handled below with counter
  }
  const counters: Record<string, number> = {}
  for (const s of char.equipment) {
    const base = DISPLAY_TO_KEY[s.slot]
    if (!base) continue
    // Multi slots get a numeric suffix: activate1/activate2, left_ring/right_ring etc.
    let key: string
    if (['activate', 'ring', 'ear', 'wrist'].includes(base)) {
      counters[base] = (counters[base] ?? 0) + 1
      const suffixes: Record<string, string[]> = {
        activate: ['activate1', 'activate2'],
        ring:     ['left_ring', 'right_ring'],
        ear:      ['ears', 'ears2'],
        wrist:    ['left_wrist', 'right_wrist'],
      }
      key = suffixes[base][counters[base] - 1] ?? base
    } else {
      key = base
    }
    byInternalKey.set(key, s)
  }
  const bySlot = byInternalKey

  return (
    <div style={{ marginTop: '1.5rem' }}>
      {/* ── Header ── */}
      <h1 style={{ marginBottom: '0.15rem' }}>{char.name}</h1>
      <p style={{ color: 'var(--text-muted)', marginBottom: '1.25rem' }}>
        {[char.world, char.race, char.gender].filter(Boolean).join(' · ')}
      </p>

      {/* ── Summary strip ── */}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1.5rem' }}>
        <Chip label="Level" value={char.level ?? '—'} />
        <Chip label="Class" value={char.cls ?? '—'} />
        <Chip label="AAs"   value={char.aa_count} />
        {char.deity   && <Chip label="Deity" value={char.deity} />}
        {char.ts_class && <Chip label="Crafting" value={`${char.ts_class} ${char.ts_level ?? ''}`} />}
      </div>

      {/* ── Paperdoll ── */}
      <h2 style={sectionHeading}>Equipment</h2>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
        <SlotColumn slots={LEFT_SLOTS}  bySlot={bySlot} />
        <SlotColumn slots={RIGHT_SLOTS} bySlot={bySlot} />
      </div>
    </div>
  )
}

function SlotColumn({
  slots,
  bySlot,
}: {
  slots: [string, string][]
  bySlot: Map<string, EquipmentSlot>
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
      {slots.map(([label, key]) => {
        const item = bySlot.get(key)
        return (
          <div key={key} style={slotRow}>
            <span style={slotLabel}>{label}</span>
            {item ? (
              <span style={{ color: tierColour(item.tier), fontWeight: 500, fontSize: '0.88rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.name}
              </span>
            ) : (
              <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem', fontStyle: 'italic' }}>Empty</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '0.3rem 0.75rem', display: 'flex', gap: '0.4rem', alignItems: 'baseline' }}>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{label}</span>
      <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{value}</span>
    </div>
  )
}

const sectionHeading: React.CSSProperties = {
  fontSize: '0.8rem',
  textTransform: 'uppercase',
  letterSpacing: '0.07em',
  color: 'var(--text-muted)',
  marginBottom: '0.5rem',
}

const slotRow: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 5,
  padding: '0.3rem 0.6rem',
  minWidth: 0,
}

const slotLabel: React.CSSProperties = {
  minWidth: 72,
  flexShrink: 0,
  color: 'var(--text-muted)',
  fontSize: '0.78rem',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
}
