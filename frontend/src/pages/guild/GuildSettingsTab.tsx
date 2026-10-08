/**
 * GuildSettingsTab — the guild leader's switches.
 *
 * Rendered only for the guild leader (Census rank 0) or a site admin; the
 * server enforces the same rule on PUT. Two settings: whether officers may
 * delete the guild's parses (uploaders can always delete their own uploads
 * and admins are never gated, whatever this says), and which guild ranks the
 * site treats as officers — some guilds run three leadership ranks, so the
 * leader picks rather than the site assuming the top two. The leader rank is
 * always an officer.
 */
import { useEffect, useState } from 'react'

import { Button, SectionLabel } from '../../components/ui'
import { fmtRelative } from '../../formatters'
import { useFetch } from '../../hooks/useFetch'
import { handle } from '../../lib/api'
import { toErrorMessage } from '../../lib/errors'
import type { GuildRank, GuildSettings } from './types'

const LEADER_RANK = 0
const DEFAULT_RANKS = [0, 1]

interface Draft {
  officers_can_delete_parses: boolean
  /** null = the site default (ranks 0 and 1). */
  officer_rank_ids: number[] | null
}

function draftFrom(data: GuildSettings): Draft {
  return {
    officers_can_delete_parses: data.officers_can_delete_parses,
    officer_rank_ids: data.officer_rank_ids_custom ? [...data.officer_rank_ids] : null,
  }
}

function sameRanks(a: number[] | null, b: number[] | null): boolean {
  if (a === null || b === null) return a === b
  const sa = [...a].sort((x, y) => x - y)
  const sb = [...b].sort((x, y) => x - y)
  return sa.length === sb.length && sa.every((v, i) => v === sb[i])
}

export function GuildSettingsTab({ guildName, ranks }: { guildName: string; ranks: GuildRank[] }) {
  const url = `/api/guild/${encodeURIComponent(guildName)}/settings`
  const { data, loading, error, refetch } = useFetch<GuildSettings>(url)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (data) setDraft(draftFrom(data))
  }, [data])

  const dirty =
    data !== null &&
    draft !== null &&
    (draft.officers_can_delete_parses !== data.officers_can_delete_parses ||
      !sameRanks(draft.officer_rank_ids, draftFrom(data).officer_rank_ids))

  // The ranks shown as checked: the explicit choice, else the site default.
  const effectiveRanks = draft?.officer_rank_ids ?? DEFAULT_RANKS
  // The roster only lists ranks in use; make sure the checked ones still appear.
  const rankRows: GuildRank[] = [...ranks]
  for (const id of effectiveRanks) {
    if (!rankRows.some(r => r.id === id)) rankRows.push({ id, name: `Rank ${id + 1}` })
  }
  rankRows.sort((a, b) => a.id - b.id)

  function toggleRank(id: number, on: boolean) {
    if (!draft || id === LEADER_RANK) return
    const next = new Set(effectiveRanks)
    if (on) next.add(id)
    else next.delete(id)
    next.add(LEADER_RANK)
    setDraft({ ...draft, officer_rank_ids: [...next].sort((a, b) => a - b) })
    setSaved(false)
  }

  async function save() {
    if (draft === null || !dirty) return
    setSaving(true)
    setSaveError(null)
    setSaved(false)
    try {
      await handle<GuildSettings>(
        await fetch(url, {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(draft),
        }),
      )
      setSaved(true)
      refetch()
    } catch (err) {
      setSaveError(toErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  if (loading && !data) return <p className="text-text-muted p-4">Loading guild settings…</p>
  if (error) return <p className="text-danger p-4">{error}</p>
  if (!data || !draft) return null

  return (
    <div className="px-4 py-3 flex flex-col gap-4">
      <SectionLabel>Guild Settings</SectionLabel>

      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          className="mt-1"
          checked={draft.officers_can_delete_parses}
          onChange={e => { setDraft({ ...draft, officers_can_delete_parses: e.target.checked }); setSaved(false) }}
        />
        <span className="flex flex-col gap-1">
          <span className="text-text">Officers can delete guild parses</span>
          <span className="text-[0.8rem] text-text-muted">
            When off, only the guild leader, site admins and each parse&apos;s own uploader can delete
            parses uploaded under {guildName}. Turn this off if officer turnover or guild drama makes
            bulk removal of the guild&apos;s raid history a risk.
          </span>
        </span>
      </label>

      <div className="flex flex-col gap-2">
        <span className="text-text">Officer ranks</span>
        <span className="text-[0.8rem] text-text-muted">
          Members holding these ranks can review claims, edit the raid schedule and recruitment
          profile, manage item watches and (if allowed above) delete parses. The leader rank is
          always included. Ranks come from your Census roster, so only ranks someone currently holds
          are listed.
        </span>
        <div className="flex flex-col gap-1">
          {rankRows.map(r => (
            <label key={r.id} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={effectiveRanks.includes(r.id)}
                disabled={r.id === LEADER_RANK}
                onChange={e => toggleRank(r.id, e.target.checked)}
              />
              <span className="text-text">{r.name}</span>{' '}
              <span className="text-[0.74rem] text-text-muted">rank {r.id + 1}</span>
            </label>
          ))}
        </div>
        {draft.officer_rank_ids !== null && (
          <div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { setDraft({ ...draft, officer_rank_ids: null }); setSaved(false) }}
            >
              Use site default (top two ranks)
            </Button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <Button variant="primary" size="sm" onClick={save} disabled={!dirty || saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
        {saved && !dirty && <span className="text-[0.8rem] text-success">Saved.</span>}
        {saveError && <span className="text-[0.8rem] text-danger">{saveError}</span>}
        {data.updated_at != null && (
          <span className="text-[0.78rem] text-text-muted">
            Last changed {fmtRelative(data.updated_at)}{data.updated_by_name ? ` by ${data.updated_by_name}` : ''}
          </span>
        )}
      </div>
    </div>
  )
}
