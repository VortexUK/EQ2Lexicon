/**
 * RecruitingPage — /recruiting — the "Guilds Recruiting" browse page.
 *
 * Cards for every guild on this server with an active recruitment profile,
 * filterable by needed class and tag (OR within a group, AND across groups)
 * plus a text search over name/description/contacts. Filters are plain React
 * state (no URL mirror), so no setSearchParams-throw handling is needed.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, Card, LinkButton } from '../components/ui'
import { FilterPill } from '../components/FilterPill'
import { fmtRelative } from '../formatters'
import { useFetch } from '../hooks/useFetch'
import { usePagedSearch } from '../hooks/usePagedSearch'
import { useClasses } from '../useClasses'
import { tagLabel } from './guild/GuildRecruitmentTab'

interface RecruitingGuild {
  guild_name: string
  description: string
  classes: string[]
  tags: string[]
  contacts: string[]
  discord_url: string | null
  updated_at: number | null
  has_logo: boolean
  logo_uploaded_at: number | null
  member_count: number | null
}

interface RecruitingList {
  guilds: RecruitingGuild[]
  available_tags: string[]
}

function GuildCard({ g }: { g: RecruitingGuild }) {
  const { colourFor } = useClasses()
  const logoUrl = g.has_logo
    ? `/api/guild/${encodeURIComponent(g.guild_name)}/recruitment/logo?v=${g.logo_uploaded_at ?? 0}`
    : null
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start gap-3">
        {logoUrl ? (
          <img
            src={logoUrl}
            alt=""
            className="w-[56px] h-[56px] rounded-md border border-border object-contain bg-surface shrink-0"
          />
        ) : (
          <div className="w-[56px] h-[56px] rounded-md border border-border bg-surface-raised shrink-0 flex items-center justify-center font-heading text-[1.6rem] text-gold-dim">
            {g.guild_name.charAt(0)}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <Link
            to={`/guild/${encodeURIComponent(g.guild_name)}?tab=recruitment`}
            className="font-heading text-[1.15rem] text-gold leading-tight block"
          >
            {g.guild_name}
          </Link>
          <div className="text-[0.78rem] text-text-muted mt-0.5">
            {g.member_count != null ? `${g.member_count} characters · ` : ''}
            {g.updated_at != null ? `updated ${fmtRelative(g.updated_at)}` : ''}
          </div>
        </div>
      </div>

      {g.tags.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {g.tags.map(t => (
            <Badge key={t} variant="gold">{tagLabel(t)}</Badge>
          ))}
        </div>
      )}

      {g.description && (
        <p className="text-[0.86rem] text-text leading-normal m-0 line-clamp-3 whitespace-pre-line">
          {g.description}
        </p>
      )}

      {g.classes.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-[0.68rem] text-text-muted uppercase tracking-[0.06em] mr-1">Needs</span>
          {g.classes.map(c => (
            <span
              key={c}
              className="rounded-pill border border-border bg-surface px-2 py-px text-[0.74rem] font-medium"
              style={{ color: colourFor(c) }}
            >
              {c}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap mt-auto pt-1">
        {g.contacts.length > 0 && (
          <span className="text-[0.78rem] text-text-muted">
            Contact{' '}
            {g.contacts.map((name, i) => (
              <span key={name}>
                {i > 0 && ', '}
                <Link to={`/character/${encodeURIComponent(name)}`} className="text-gold">
                  {name}
                </Link>
              </span>
            ))}
          </span>
        )}
        {g.discord_url && (
          <LinkButton href={g.discord_url} target="_blank" rel="noopener noreferrer" size="sm" className="ml-auto">
            Discord
          </LinkButton>
        )}
      </div>
    </Card>
  )
}

export default function RecruitingPage() {
  const { data, loading, error } = useFetch<RecruitingList>('/api/recruiting')
  const { classes: classCatalogue } = useClasses()
  const [classFilter, setClassFilter] = useState<Set<string>>(new Set())
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set())

  function toggle(set: Set<string>, value: string, apply: (s: Set<string>) => void) {
    const next = new Set(set)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    apply(next)
  }

  // OR within a filter group, AND across groups.
  const filtered = useMemo(() => {
    const guilds = data?.guilds ?? []
    return guilds.filter(g => {
      if (classFilter.size > 0 && !g.classes.some(c => classFilter.has(c))) return false
      if (tagFilter.size > 0 && !g.tags.some(t => tagFilter.has(t))) return false
      return true
    })
  }, [data, classFilter, tagFilter])

  const paged = usePagedSearch(
    filtered,
    (g, q) =>
      g.guild_name.toLowerCase().includes(q) ||
      g.description.toLowerCase().includes(q) ||
      g.contacts.some(c => c.toLowerCase().includes(q)),
    { perPage: 24, filterKey: `${[...classFilter].join(',')}|${[...tagFilter].join(',')}` },
  )

  const anyFilter = classFilter.size > 0 || tagFilter.size > 0 || paged.search.trim() !== ''

  return (
    <main className="max-w-[1100px] mx-auto my-12 px-4 page-enter">
      <h1 className="font-heading text-[2rem] text-gold mb-1">Guilds Recruiting</h1>
      <p className="text-text-muted text-[0.9rem] mb-5">
        Guilds on this server looking for members. Officers can list their guild from the
        Recruitment tab on their guild page.
      </p>

      {loading && <p className="text-text-muted">Loading recruiting guilds…</p>}
      {error && <p className="text-danger">{error}</p>}

      {data && (
        <>
          {/* Filters */}
          <div className="flex flex-col gap-2 mb-5">
            <input
              type="text"
              placeholder="Search by guild, description or contact…"
              value={paged.search}
              onChange={e => paged.setSearch(e.target.value)}
              className="max-w-[340px] box-border"
            />
            {data.available_tags.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em] mr-[0.2rem]">Tags</span>
                {data.available_tags.map(t => (
                  <FilterPill key={t} active={tagFilter.has(t)} onClick={() => toggle(tagFilter, t, setTagFilter)}>
                    {tagLabel(t)}
                  </FilterPill>
                ))}
              </div>
            )}
            {classCatalogue.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em] mr-[0.2rem]">Needs</span>
                {classCatalogue.map(c => (
                  <FilterPill
                    key={c.name}
                    active={classFilter.has(c.name)}
                    onClick={() => toggle(classFilter, c.name, setClassFilter)}
                  >
                    {c.name}
                  </FilterPill>
                ))}
                {anyFilter && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setClassFilter(new Set())
                      setTagFilter(new Set())
                      paged.setSearch('')
                    }}
                  >
                    reset
                  </Button>
                )}
              </div>
            )}
          </div>

          {/* Cards */}
          {paged.rows.length === 0 ? (
            <p className="text-text-muted text-center py-10">
              {data.guilds.length === 0
                ? 'No guilds are recruiting right now. Officers: open your guild page → Recruitment to list yours.'
                : 'No recruiting guilds match those filters.'}
            </p>
          ) : (
            <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
              {paged.rows.map(g => (
                <GuildCard key={g.guild_name} g={g} />
              ))}
            </div>
          )}

          {/* Pager */}
          {paged.pageCount > 1 && (
            <div className="flex items-center justify-center gap-3 mt-6">
              <Button variant="ghost" size="sm" onClick={() => paged.setPage(paged.page - 1)} disabled={paged.page <= 1}>
                ← Prev
              </Button>
              <span className="text-[0.82rem] text-text-muted">
                Page {paged.page} of {paged.pageCount}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => paged.setPage(paged.page + 1)}
                disabled={paged.page >= paged.pageCount}
              >
                Next →
              </Button>
            </div>
          )}
        </>
      )}
    </main>
  )
}
