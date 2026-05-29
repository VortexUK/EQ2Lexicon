import { useParams, Link } from 'react-router-dom'
import { Card, SectionLabel } from '../components/ui'
import { itemRarityColor } from '../rarityColors'
import { useFetch } from '../hooks/useFetch'

interface ItemStat   { display_name: string; value: number; stat_group: string }
interface EffectLine { indentation: number; text: string }
interface ItemEffect { name: string; trigger: string; lines: EffectLine[] }

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
  recipe_list: { id: string; name: string }[]
}


export default function ItemPage() {
  const { itemId } = useParams<{ itemId: string }>()
  const { data: item, loading, error } = useFetch<ItemDetail>(
    itemId ? `/api/item/${itemId}` : null,
  )

  if (loading) return <LoadingShell />
  if (error || !item) return (
    <main className="max-w-[700px] mx-auto py-8 px-6">
      <Link to="/items" className="text-text-muted text-[0.9rem] no-underline">← Item Search</Link>
      <p className="text-danger mt-4">{error ?? 'Item not found.'}</p>
    </main>
  )

  const colour = itemRarityColor(item.quality)
  const iconUrl = item.icon_id ? `/icons/${item.icon_id}.png` : null

  return (
    <main className="max-w-[700px] mx-auto pt-8 px-6 pb-16">
      <Link to="/items" className="text-text-muted text-[0.9rem] no-underline">
        ← Item Search
      </Link>

      {/* Header */}
      <div className="flex items-start gap-4 mt-4 mb-6">
        {iconUrl && (
          <img src={iconUrl} alt="" width={48} height={48}
            className="rounded-sm border border-border shrink-0" />
        )}
        <div>
          <h1
            className="font-heading text-[1.6rem] font-bold mt-0 mx-0 mb-[0.2rem]"
            style={{
              color: colour,
              textShadow: colour !== 'var(--text)' ? `0 0 12px ${colour}55` : 'none',
            }}
          >
            {item.name}
          </h1>
          <div className="flex gap-2 flex-wrap items-center">
            <span className="text-[0.85rem] font-semibold" style={{ color: colour }}>
              {item.quality}
            </span>
            {item.slot_type && (
              <span className="text-text-muted text-[0.82rem]">· {item.slot_type}</span>
            )}
            {item.required_level && (
              <span className="text-text-muted text-[0.82rem]">· Level {item.required_level}</span>
            )}
            {item.classes_label && (
              <span className="text-text-muted text-[0.82rem]">· {item.classes_label}</span>
            )}
          </div>
          {item.flags.length > 0 && (
            <div className="mt-[0.3rem] flex gap-[0.3rem] flex-wrap">
              {item.flags.map(f => (
                <span
                  key={f}
                  className="text-[0.68rem] font-bold tracking-[0.05em] py-[0.1rem] px-[0.4rem] rounded-[3px] text-gold"
                  style={{
                    background: 'rgba(200,169,110,0.15)',
                    border: '1px solid rgba(200,169,110,0.3)',
                  }}
                >
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      <Card className="px-5 py-[1.1rem]">

        {/* Description */}
        {item.description && (
          <p className="text-text-muted text-[0.85rem] italic mb-4">
            {item.description}
          </p>
        )}

        {/* Mitigation */}
        {item.mitigation != null && (
          <InfoRow label="Mitigation" value={String(item.mitigation)} />
        )}

        {/* Extra info */}
        {item.extra_info.map(([label, value]) => (
          <InfoRow key={label} label={label} value={value} />
        ))}

        {/* Stats */}
        {item.stats.length > 0 && (
          <div className="my-4">
            {item.stats.map(s => (
              <div
                key={s.display_name}
                className="flex justify-between items-baseline py-[0.12rem] border-b border-border text-[0.88rem]"
              >
                <span style={{ color: s.stat_group === 'primary' ? 'var(--color-stat-primary)' : 'var(--color-stat-secondary)' }}>
                  {s.display_name}
                </span>
                <span className="font-semibold" style={{ color: s.stat_group === 'primary' ? 'var(--color-stat-primary)' : 'var(--color-stat-secondary)' }}>
                  {s.value > 0 ? '+' : ''}{s.value}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Effects */}
        {item.effects.map((eff, i) => (
          <div key={i} className="my-3 text-[0.85rem]">
            {eff.name && (
              <div className="text-text-muted text-[0.75rem] mb-[0.2rem] italic">
                {eff.name}
              </div>
            )}
            {eff.trigger && (
              <div className="text-text font-semibold mb-[0.15rem]">
                {eff.trigger}
              </div>
            )}
            {eff.lines.map((ln, j) => (
              <div
                key={j}
                className="text-[0.83rem] leading-normal"
                style={{
                  paddingLeft: `${ln.indentation * 1.1}rem`,
                  color: ln.indentation === 0 ? 'var(--text)' : 'var(--text-muted)',
                }}
              >
                {ln.indentation > 0 ? '• ' : ''}{ln.text}
              </div>
            ))}
          </div>
        ))}

        {/* Adornment slots */}
        {item.adornment_slots.length > 0 && (
          <div className="mt-3">
            {item.adornment_slots.map(s => (
              <div key={s} className="text-[0.82rem] text-text-muted">
                {s}
              </div>
            ))}
          </div>
        )}

        {/* Recipe book — the recipes this volume teaches */}
        {item.recipe_list.length > 0 && (
          <div className="mt-4">
            <SectionLabel>Teaches {item.recipe_list.length} {item.recipe_list.length === 1 ? 'Recipe' : 'Recipes'}</SectionLabel>
            <div className="flex flex-col gap-px">
              {item.recipe_list.map(r => (
                <Link
                  key={r.id}
                  to={`/recipes?q=${encodeURIComponent(r.name)}`}
                  className="text-[0.88rem] text-gold hover:text-gold-bright no-underline py-[0.15rem]"
                >
                  {r.name}
                </Link>
              ))}
            </div>
          </div>
        )}

      </Card>

      <div className="mt-2 text-[0.75rem] text-text-muted text-right">
        ID: {item.id}
      </div>
    </main>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-baseline py-[0.12rem] border-b border-border text-[0.85rem]">
      <span className="text-text-muted">{label}</span>
      <span className="text-text">{value}</span>
    </div>
  )
}

function LoadingShell() {
  return (
    <main className="max-w-[700px] mx-auto py-8 px-6">
      <Link to="/items" className="text-text-muted text-[0.9rem] no-underline">← Item Search</Link>
      <p className="text-text-muted mt-4">Loading…</p>
    </main>
  )
}
