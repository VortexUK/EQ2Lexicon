/**
 * Trigger Library — every ACT trigger + spell timer the community has curated,
 * in one place. Two sections:
 *
 *  • Categories — contributor-defined general-purpose groups ("Death Saves",
 *    "Cures", …) not tied to any boss. Stored server-side as encounters under
 *    the synthetic "General" zone, so the standard per-encounter editor
 *    (<ActTriggers zoneName="General" …/>) works on them unchanged and the
 *    EQ2Parser sync pack ships them automatically.
 *
 *  • Raid encounters — everything curated on the boss pages, grouped
 *    expansion → zone → encounter, with the same inline editor.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ActTriggers } from '../components/ActTriggers'
import { Badge, Button, Card, SectionLabel } from '../components/ui'
import { useAuth, isContributor } from '../hooks/useAuth'
import type { AuthState } from '../hooks/useAuth'
import { useFetch } from '../hooks/useFetch'
import { handle } from '../lib/api'
import { toErrorMessage } from '../lib/errors'

// ── API shapes ────────────────────────────────────────────────────────────────

interface Category {
  name: string
  position: number
  trigger_count: number
  spell_timer_count: number
}

interface PackEncounter {
  mob: string
  position: number
  triggers: unknown[]
  spell_timers: unknown[]
}

interface PackZone {
  zone: string
  expansion: string
  encounters: PackEncounter[]
}

interface Pack {
  version: string
  generated_at: number
  zones: PackZone[]
}

interface FeaturedExpansion {
  short: string
  name: string | null
  year: number | null
}

/** The synthetic zone that holds categories — excluded from the raid section. */
const GENERAL_ZONE = 'General'

// ── Page ──────────────────────────────────────────────────────────────────────

interface Props {
  /** Optional auth-state override for tests. */
  authOverride?: AuthState
}

export default function TriggersPage({ authOverride }: Props = {}) {
  const liveAuth = useAuth()
  const auth = authOverride ?? liveAuth
  const canEdit = isContributor(auth)

  const categoriesFetch = useFetch<Category[]>('/api/act/categories')
  const packFetch = useFetch<Pack>('/api/act/pack')
  const expansionsFetch = useFetch<FeaturedExpansion[]>('/api/raids/expansions')

  const categories = categoriesFetch.data ?? []

  // Raid zones grouped by expansion, newest expansion first (year from the
  // featured-expansion list; unknown eras sink to the bottom).
  const groups = useMemo(() => {
    const zones = (packFetch.data?.zones ?? []).filter(z => z.zone !== GENERAL_ZONE)
    const meta = new Map((expansionsFetch.data ?? []).map(e => [e.short, e]))
    const byExpansion = new Map<string, PackZone[]>()
    for (const z of zones) {
      const list = byExpansion.get(z.expansion) ?? []
      list.push(z)
      byExpansion.set(z.expansion, list)
    }
    return [...byExpansion.entries()]
      .map(([short, zs]) => ({
        short,
        name: meta.get(short)?.name ?? null,
        year: meta.get(short)?.year ?? null,
        zones: zs,
      }))
      .sort((a, b) => (b.year ?? -1) - (a.year ??  -1))
  }, [packFetch.data, expansionsFetch.data])

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6">
      <header className="mb-6">
        <h1 className="font-heading text-[1.7rem] text-gold">Trigger Library</h1>
        <p className="text-text-muted text-[0.92rem] leading-relaxed max-w-[640px]">
          The community's ACT triggers and spell timers — general-purpose categories
          plus everything curated on the raid boss pages. EQ2Parser syncs all of it
          automatically; ACT users can download the XML per section.
        </p>
        {packFetch.data && (
          <p className="text-text-muted text-[0.72rem] mt-1">Pack v{packFetch.data.version}</p>
        )}
      </header>

      <CategoriesSection
        categories={categories}
        loading={categoriesFetch.loading}
        error={categoriesFetch.error}
        canEdit={canEdit}
        onChanged={categoriesFetch.refetch}
      />

      <section className="mt-10">
        <SectionLabel>Raid encounters</SectionLabel>
        {packFetch.loading && <p className="text-text-muted text-sm mt-2">Loading…</p>}
        {packFetch.error && (
          <p className="text-danger text-sm mt-2">Couldn't load the trigger pack: {packFetch.error}</p>
        )}
        {!packFetch.loading && !packFetch.error && groups.length === 0 && (
          <p className="text-text-muted text-sm mt-2 leading-relaxed">
            No raid triggers shared yet. Triggers added on a boss's{' '}
            <Link to="/raids" className="text-gold">raid strategy page</Link> appear here.
          </p>
        )}
        <div className="flex flex-col gap-4 mt-2">
          {groups.map(g => (
            <div key={g.short}>
              <h2 className="font-heading text-gold-dim text-[1.05rem] mb-2">
                {g.name ?? g.short}
                {g.year != null && <span className="text-text-muted text-[0.78rem] ml-2">{g.year}</span>}
              </h2>
              <div className="flex flex-col gap-3">
                {g.zones.map(z => <ZoneCard key={z.zone} zone={z} />)}
              </div>
            </div>
          ))}
        </div>
      </section>
    </main>
  )
}

// ── Categories section ────────────────────────────────────────────────────────

interface CategoriesSectionProps {
  categories: Category[]
  loading: boolean
  error: string | null
  canEdit: boolean
  onChanged: () => void
}

function CategoriesSection({ categories, loading, error, canEdit, onChanged }: CategoriesSectionProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  function toggle(position: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(position)) next.delete(position)
      else next.add(position)
      return next
    })
  }

  async function createCategory() {
    const name = newName.trim()
    if (!name) return
    setBusy(true)
    setActionError(null)
    try {
      await handle<Category>(await fetch('/api/act/categories', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }))
      setNewName('')
      setAdding(false)
      onChanged()
    } catch (e) {
      setActionError(toErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  async function renameCategory(position: number, name: string) {
    setBusy(true)
    setActionError(null)
    try {
      await handle<Category>(await fetch(`/api/act/categories/${position}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }))
      onChanged()
      return true
    } catch (e) {
      setActionError(toErrorMessage(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  async function deleteCategory(cat: Category) {
    const contents = cat.trigger_count + cat.spell_timer_count
    const msg = contents > 0
      ? `Delete "${cat.name}" and its ${cat.trigger_count} trigger${cat.trigger_count === 1 ? '' : 's'} + ${cat.spell_timer_count} spell timer${cat.spell_timer_count === 1 ? '' : 's'}? Cannot be undone.`
      : `Delete "${cat.name}"? Cannot be undone.`
    if (!confirm(msg)) return
    setBusy(true)
    setActionError(null)
    try {
      await handle<{ ok: boolean }>(await fetch(`/api/act/categories/${cat.position}`, {
        method: 'DELETE',
        credentials: 'include',
      }))
      setExpanded(prev => {
        const next = new Set(prev)
        next.delete(cat.position)
        return next
      })
      onChanged()
    } catch (e) {
      setActionError(toErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <header className="flex items-baseline justify-between flex-wrap gap-2">
        <SectionLabel>Categories</SectionLabel>
        {canEdit && !adding && (
          <Button size="sm" variant="primary" onClick={() => { setAdding(true); setActionError(null) }}>
            New category
          </Button>
        )}
      </header>
      <p className="text-text-muted text-[0.82rem] mb-2">
        General-purpose trigger groups that aren't tied to a boss — death saves, cures, dispels…
      </p>

      {adding && (
        <div className="flex items-center gap-2 mb-3">
          <input
            type="text"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void createCategory() }}
            placeholder="Category name — e.g. Death Saves"
            maxLength={64}
            autoFocus
            className="appearance-none border border-border rounded-sm bg-bg/60 text-text px-3 py-1.5 text-[0.9rem] flex-1 max-w-[320px]"
          />
          <Button size="sm" variant="primary" onClick={createCategory} disabled={busy || !newName.trim()}>
            Create
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { setAdding(false); setNewName(''); setActionError(null) }} disabled={busy}>
            Cancel
          </Button>
        </div>
      )}

      {actionError && <p className="text-danger text-sm mb-2">{actionError}</p>}
      {loading && <p className="text-text-muted text-sm">Loading…</p>}
      {error && <p className="text-danger text-sm">Couldn't load categories: {error}</p>}

      {!loading && !error && categories.length === 0 && !adding && (
        <p className="text-text-muted text-sm leading-relaxed">
          No categories yet.
          {canEdit && <> Click <em>New category</em> to create the first one.</>}
        </p>
      )}

      {categories.length > 0 && (
        <div className="flex flex-col gap-2">
          {categories.map(cat => (
            <CategoryCard
              key={cat.position}
              category={cat}
              expanded={expanded.has(cat.position)}
              onToggle={() => toggle(cat.position)}
              canEdit={canEdit}
              busy={busy}
              onRename={renameCategory}
              onDelete={() => deleteCategory(cat)}
            />
          ))}
        </div>
      )}
    </section>
  )
}

interface CategoryCardProps {
  category: Category
  expanded: boolean
  onToggle: () => void
  canEdit: boolean
  busy: boolean
  onRename: (position: number, name: string) => Promise<boolean>
  onDelete: () => void
}

function CategoryCard({ category, expanded, onToggle, canEdit, busy, onRename, onDelete }: CategoryCardProps) {
  const [renaming, setRenaming] = useState(false)
  const [draftName, setDraftName] = useState(category.name)

  async function saveRename() {
    const name = draftName.trim()
    if (!name || name === category.name) {
      setRenaming(false)
      return
    }
    if (await onRename(category.position, name)) setRenaming(false)
  }

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2">
        {renaming ? (
          <>
            <input
              type="text"
              value={draftName}
              onChange={e => setDraftName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') void saveRename() }}
              maxLength={64}
              autoFocus
              className="appearance-none border border-border rounded-sm bg-bg/60 text-text px-2 py-1 text-[0.92rem] flex-1 max-w-[320px]"
            />
            <Button size="sm" variant="primary" onClick={saveRename} disabled={busy || !draftName.trim()}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => { setRenaming(false); setDraftName(category.name) }} disabled={busy}>Cancel</Button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              className="flex-1 min-w-0 flex items-baseline gap-3 text-left appearance-none border-0 bg-transparent hover:text-gold-bright"
            >
              <span className="text-[0.72rem] text-gold-dim w-4 shrink-0">{expanded ? '▾' : '▸'}</span>
              <span className="font-heading text-gold text-[1.05rem] truncate">{category.name}</span>
            </button>
            <Badge variant={category.trigger_count > 0 ? 'gold' : 'muted'}>
              {category.trigger_count} trigger{category.trigger_count === 1 ? '' : 's'}
            </Badge>
            <Badge variant={category.spell_timer_count > 0 ? 'info' : 'muted'}>
              {category.spell_timer_count} timer{category.spell_timer_count === 1 ? '' : 's'}
            </Badge>
            {canEdit && (
              <div className="flex items-center gap-1 shrink-0">
                <Button size="sm" variant="ghost" onClick={() => { setDraftName(category.name); setRenaming(true) }}>Rename</Button>
                <Button size="sm" variant="danger" onClick={onDelete} disabled={busy}>Delete</Button>
              </div>
            )}
          </>
        )}
      </div>
      {expanded && (
        <div className="border-t border-border/60 px-3 pb-3 pt-2 bg-bg/30">
          <ActTriggers zoneName={GENERAL_ZONE} position={category.position} />
        </div>
      )}
    </Card>
  )
}

// ── Raid zone card ────────────────────────────────────────────────────────────

function ZoneCard({ zone }: { zone: PackZone }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  function toggle(position: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(position)) next.delete(position)
      else next.add(position)
      return next
    })
  }

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-baseline justify-between gap-3 px-3 py-2 border-b border-border/60">
        <h3 className="font-heading text-gold text-[1.05rem] truncate">{zone.zone}</h3>
        <Link to={`/raids/${encodeURIComponent(zone.zone)}`} className="text-gold-dim text-[0.8rem] shrink-0">
          Strategies →
        </Link>
      </div>
      <ul className="divide-y divide-border/60">
        {zone.encounters.map(enc => {
          const open = expanded.has(enc.position)
          return (
            <li key={enc.position}>
              <button
                type="button"
                onClick={() => toggle(enc.position)}
                aria-expanded={open}
                className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-surface-raised/60 appearance-none border-0 bg-transparent"
              >
                <span className="text-[0.72rem] text-gold-dim w-4 shrink-0">{open ? '▾' : '▸'}</span>
                <span className="flex-1 min-w-0 text-[0.92rem] text-text truncate">{enc.mob}</span>
                <span className="text-text-muted text-[0.78rem] shrink-0">
                  {enc.triggers.length > 0 && `${enc.triggers.length} trigger${enc.triggers.length === 1 ? '' : 's'}`}
                  {enc.triggers.length > 0 && enc.spell_timers.length > 0 && ' · '}
                  {enc.spell_timers.length > 0 && `${enc.spell_timers.length} timer${enc.spell_timers.length === 1 ? '' : 's'}`}
                </span>
              </button>
              {open && (
                <div className="border-t border-border/60 px-3 pb-3 pt-2 bg-bg/30">
                  <ActTriggers zoneName={zone.zone} position={enc.position} />
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
