/**
 * FlaggedCharactersSection — the spell audit's working set.
 *
 * A character found with an out-of-era spell tier (the 2026-10 TLE event
 * bug), or one Census cannot show us at all, is barred: every parse since
 * the cutoff they played in is hidden and filed as a tamper report. Admins
 * see the flags here, can add one by hand, clear one (which restores the
 * parses unless another flagged character is in them) and run a sweep now.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button } from '../../components/ui'
import { fmtLocalDateTime } from '../../formatters'
import { handle } from '../../lib/api'
import { toErrorMessage } from '../../lib/errors'
import { INPUT_CLS, SECTION_TITLE_CLS } from './types'

interface FlaggedSpell { name: string; tier: string; level: number }
interface FlaggedCharacter {
  world: string
  name: string
  reason: string
  details: { spells?: FlaggedSpell[]; note?: string | null } | null
  flagged_at: number
  flagged_by: string
  cleared_at: number | null
  cleared_by: string | null
}
interface FlaggedList { results: FlaggedCharacter[]; since: number; tiers: string[] }

const REASON_LABEL: Record<string, string> = {
  out_of_era_spells: 'Out-of-era spells',
  census_hidden: 'Not in Census (unverifiable)',
  manual: 'Manual',
}
const REASON_VARIANT: Record<string, 'danger' | 'warning' | 'muted'> = {
  out_of_era_spells: 'danger',
  census_hidden: 'warning',
  manual: 'muted',
}

export function FlaggedCharactersSection() {
  const [data, setData] = useState<FlaggedList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [includeCleared, setIncludeCleared] = useState(false)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const r = await fetch(`/api/admin/flagged-characters?include_cleared=${includeCleared}`, { credentials: 'include' })
      setData(await handle<FlaggedList>(r))
    } catch (err) {
      setError(toErrorMessage(err))
    }
  }, [includeCleared])

  useEffect(() => { void load() }, [load])

  async function flag() {
    if (!name.trim()) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await handle<{ new: boolean; hidden: number; reports: number }>(
        await fetch('/api/admin/flagged-characters', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim(), note: note.trim() || null }),
        }),
      )
      setMsg(`${r.new ? 'Flagged' : 'Already flagged'} — ${r.hidden} parse(s) hidden, ${r.reports} report(s) filed.`)
      setName('')
      setNote('')
      await load()
    } catch (err) {
      setMsg(toErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function clear(c: FlaggedCharacter) {
    if (!window.confirm(`Clear the flag on ${c.name}? Parses it hid are restored unless another flagged character is in them.`)) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await handle<{ restored: number }>(
        await fetch(`/api/admin/flagged-characters/${encodeURIComponent(c.name)}`, { method: 'DELETE', credentials: 'include' }),
      )
      setMsg(`Cleared ${c.name} — ${r.restored} parse(s) restored.`)
      await load()
    } catch (err) {
      setMsg(toErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function runNow(force: boolean) {
    setBusy(true)
    setMsg(null)
    try {
      await handle<{ started: boolean }>(
        await fetch(`/api/admin/spell-audit/run?force_rescan=${force}`, { method: 'POST', credentials: 'include' }),
      )
      setMsg(force ? 'Sweep started (re-checking everyone).' : 'Sweep started.')
    } catch (err) {
      setMsg(toErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <p className={SECTION_TITLE_CLS}>
        Flagged characters{data ? ` (${data.results.filter(c => c.cleared_at == null).length})` : ''}
      </p>
      <div className="card flex flex-col gap-3">
        {data && (
          <p className="text-[0.8rem] text-text-muted m-0">
            Characters carrying {data.tiers.join(' / ')} spells, or that Census cannot show, are barred: every parse
            since {fmtLocalDateTime(data.since)} they played in is hidden and filed under tamper reports. Clearing a flag
            restores those parses.
          </p>
        )}

        <div className="flex items-end gap-2 flex-wrap">
          <label className="flex flex-col gap-1 text-[0.72rem] uppercase tracking-[0.06em] text-text-muted font-semibold">
            Character
            <input className={INPUT_CLS} value={name} onChange={e => setName(e.target.value)} placeholder="Name" />
          </label>
          <label className="flex flex-col gap-1 text-[0.72rem] uppercase tracking-[0.06em] text-text-muted font-semibold min-w-[260px]">
            Note
            <input className={INPUT_CLS} value={note} onChange={e => setNote(e.target.value)} placeholder="Why (optional)" />
          </label>
          <Button variant="danger" size="sm" onClick={flag} disabled={busy || !name.trim()}>Flag</Button>
          <Button variant="secondary" size="sm" onClick={() => runNow(false)} disabled={busy}>Run audit now</Button>
          <Button variant="ghost" size="sm" onClick={() => runNow(true)} disabled={busy}>Re-check everyone</Button>
          <label className="flex items-center gap-1 text-[0.8rem] text-text-muted ml-auto cursor-pointer">
            <input type="checkbox" checked={includeCleared} onChange={e => setIncludeCleared(e.target.checked)} />
            show cleared
          </label>
        </div>

        {msg && <p className="text-[0.8rem] text-text m-0">{msg}</p>}
        {error && <p className="text-[0.8rem] text-danger m-0">{error}</p>}

        {data && data.results.length === 0 && (
          <p className="text-[0.8rem] text-text-muted m-0">No flagged characters on this server.</p>
        )}
        {data && data.results.length > 0 && (
          <table className="w-full text-[0.82rem]">
            <thead>
              <tr className="text-left text-text-muted">
                <th className="py-1 pr-3">Character</th>
                <th className="py-1 pr-3">Reason</th>
                <th className="py-1 pr-3">Evidence</th>
                <th className="py-1 pr-3">Flagged</th>
                <th className="py-1 pr-3">Status</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {data.results.map(c => (
                <tr key={`${c.world}:${c.name}`} className="border-t border-border align-top">
                  <td className="py-1 pr-3">
                    <Link to={`/character/${encodeURIComponent(c.name)}`} className="text-gold">{c.name}</Link>
                  </td>
                  <td className="py-1 pr-3">
                    <Badge variant={REASON_VARIANT[c.reason] ?? 'muted'}>{REASON_LABEL[c.reason] ?? c.reason}</Badge>
                  </td>
                  <td className="py-1 pr-3 text-text-muted">
                    {c.details?.spells?.length
                      ? c.details.spells.map(s => `${s.name} (${s.tier})`).join(', ')
                      : c.details?.note ?? '—'}
                  </td>
                  <td className="py-1 pr-3 text-text-muted whitespace-nowrap">
                    {fmtLocalDateTime(c.flagged_at)}
                    <span className="block text-[0.72rem]">by {c.flagged_by}</span>
                  </td>
                  <td className="py-1 pr-3 text-text-muted whitespace-nowrap">
                    {c.cleared_at == null ? 'active' : `cleared ${fmtLocalDateTime(c.cleared_at)}`}
                  </td>
                  <td className="py-1 text-right">
                    {c.cleared_at == null && (
                      <Button variant="ghost" size="sm" onClick={() => clear(c)} disabled={busy}>Clear</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
