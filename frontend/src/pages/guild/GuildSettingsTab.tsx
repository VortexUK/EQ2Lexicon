/**
 * GuildSettingsTab — the guild leader's switches.
 *
 * Rendered only for the guild leader (Census rank 0) or a site admin; the
 * server enforces the same rule on PUT. One switch today: whether officers
 * may delete the guild's parses. Uploaders can always delete their own
 * uploads and admins are never gated, whatever this says.
 */
import { useEffect, useState } from 'react'

import { Button, SectionLabel } from '../../components/ui'
import { fmtRelative } from '../../formatters'
import { useFetch } from '../../hooks/useFetch'
import { handle } from '../../lib/api'
import { toErrorMessage } from '../../lib/errors'
import type { GuildSettings } from './types'

export function GuildSettingsTab({ guildName }: { guildName: string }) {
  const url = `/api/guild/${encodeURIComponent(guildName)}/settings`
  const { data, loading, error, refetch } = useFetch<GuildSettings>(url)
  const [draft, setDraft] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (data) setDraft(data.officers_can_delete_parses)
  }, [data])

  const dirty = data !== null && draft !== null && draft !== data.officers_can_delete_parses

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
          body: JSON.stringify({ officers_can_delete_parses: draft }),
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
  if (!data) return null

  return (
    <div className="px-4 py-3 flex flex-col gap-4">
      <SectionLabel>Guild Settings</SectionLabel>

      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          className="mt-1"
          checked={draft ?? data.officers_can_delete_parses}
          onChange={e => { setDraft(e.target.checked); setSaved(false) }}
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
