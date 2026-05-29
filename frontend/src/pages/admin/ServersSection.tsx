import { useCallback, useEffect, useState } from 'react'
import { Button } from '../../components/ui'
import {
  type ServerConfig,
  type ExpansionEntry,
  SECTION_TITLE_CLS,
} from './types'

// ── Shared styles ─────────────────────────────────────────────────────────────

const inputCls = 'w-full appearance-none bg-surface border border-border rounded-md px-3 py-[0.4rem] text-text text-[0.88rem] outline-none focus:border-gold/60'

// ── Date helpers ──────────────────────────────────────────────────────────────

/** Convert an ISO UTC string like "2026-06-09T20:00:00Z" to the value
 *  required by <input type="datetime-local">: "YYYY-MM-DDTHH:MM". */
function isoToDatetimeLocal(iso: string | null): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    // toISOString always returns UTC; slice off seconds and the trailing Z
    return d.toISOString().slice(0, 16)
  } catch {
    return ''
  }
}

/** Convert a datetime-local value ("YYYY-MM-DDTHH:MM") back to an ISO UTC
 *  string ("YYYY-MM-DDTHH:MM:00Z"). Returns null for empty/invalid input. */
function datetimeLocalToIso(value: string): string | null {
  if (!value) return null
  try {
    // datetime-local has no tz; treat it as UTC by appending Z
    const d = new Date(value + ':00Z')
    if (isNaN(d.getTime())) return null
    return d.toISOString()
  } catch {
    return null
  }
}

/** Find the best-matching expansion short code for a stored xpac value.
 *  The stored value may be a short code or a full name; match case-insensitively. */
function resolveXpacShort(
  current: string | null,
  expansions: ExpansionEntry[],
): string {
  if (!current || expansions.length === 0) return ''
  const lower = current.toLowerCase()
  const byShort = expansions.find(e => e.short.toLowerCase() === lower)
  if (byShort) return byShort.short
  const byName = expansions.find(e => e.name.toLowerCase() === lower)
  if (byName) return byName.short
  return ''
}

// ── ServerRow ─────────────────────────────────────────────────────────────────

interface ServerRowProps {
  server:          ServerConfig
  expansions:      ExpansionEntry[]
  defaultWorld:    string   // which world is currently the default (for radio)
  onDefaultChange: (world: string) => void
  onSaved:         () => void
}

function ServerRow({ server, expansions, defaultWorld, onDefaultChange, onSaved }: ServerRowProps) {
  const [maxLevel,  setMaxLevel]  = useState(server.max_level)
  const [xpacShort, setXpacShort] = useState<string>(() => resolveXpacShort(server.current_xpac, expansions))
  const [launchDt,  setLaunchDt]  = useState(() => isoToDatetimeLocal(server.launch_dt))
  const [busy,      setBusy]      = useState(false)
  const [result,    setResult]    = useState<{ ok: boolean; msg: string } | null>(null)

  // Keep local xpac state in sync when the expansions list arrives after mount
  // (both fetch in parallel, but expansions could win or lose the race) or when
  // the server prop changes. Done in an effect — never set state during render.
  useEffect(() => {
    setXpacShort(resolveXpacShort(server.current_xpac, expansions))
  }, [expansions, server.current_xpac])

  const isDefault = defaultWorld === server.world

  async function handleSave() {
    setBusy(true)
    setResult(null)
    try {
      // current_xpac: use selected short if expansions available; else keep
      // server's existing value unchanged (don't null it when zones.db absent)
      const current_xpac = expansions.length > 0
        ? (xpacShort || null)
        : server.current_xpac

      const body: Record<string, unknown> = {
        max_level:    maxLevel,
        current_xpac,
        launch_dt:    datetimeLocalToIso(launchDt),
      }

      // Only send is_default:true when this row is the default. Don't send
      // false — backend only acts on true.
      if (isDefault) {
        body.is_default = true
      }

      const res = await fetch(`/api/admin/servers/${encodeURIComponent(server.world)}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setResult({ ok: false, msg: data.detail ?? 'Save failed.' })
        return
      }

      setResult({ ok: true, msg: 'Saved.' })
      onSaved()
    } catch {
      setResult({ ok: false, msg: 'Network error — save failed.' })
    } finally {
      setBusy(false)
    }
  }

  const noExpansions = expansions.length === 0

  return (
    <div className="card mb-4 last:mb-0">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3 mb-4 flex-wrap">
        <div>
          <div className="font-heading text-gold text-[1rem] leading-[1.2]">
            {server.display_name}
          </div>
          <div className="text-text-muted text-[0.78rem] mt-[2px]">
            {server.subdomain} · <span className="font-mono text-[0.75rem]">{server.world}</span>
          </div>
        </div>
        {/* Default radio */}
        <label className="flex items-center gap-2 cursor-pointer select-none shrink-0">
          <input
            type="radio"
            name="server-default"
            value={server.world}
            checked={isDefault}
            onChange={() => onDefaultChange(server.world)}
            className="appearance-none w-4 h-4 rounded-full border border-border bg-surface checked:bg-gold checked:border-gold transition-colors cursor-pointer"
          />
          <span className="text-[0.82rem] text-text-muted">
            {isDefault ? <span className="text-gold font-semibold">Default (apex)</span> : 'Set as default'}
          </span>
        </label>
      </div>

      {/* Controls grid */}
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
        {/* Max Level */}
        <div>
          <label className="block text-[0.72rem] uppercase tracking-[0.06em] text-text-muted font-semibold mb-1">
            Max Level
          </label>
          <input
            type="number"
            min={1}
            max={999}
            value={maxLevel}
            onChange={e => setMaxLevel(Number(e.target.value))}
            className={inputCls}
          />
        </div>

        {/* Expansion */}
        <div>
          <label className="block text-[0.72rem] uppercase tracking-[0.06em] text-text-muted font-semibold mb-1">
            Current Expansion
          </label>
          {noExpansions ? (
            <div>
              <div className="w-full appearance-none bg-surface border border-border rounded-md px-3 py-[0.4rem] text-text-muted text-[0.88rem] opacity-60 cursor-not-allowed">
                {server.current_xpac ?? '—'}
              </div>
              <p className="text-[0.7rem] text-text-muted mt-1 italic">
                Expansion list unavailable (zones.db not loaded)
              </p>
            </div>
          ) : (
            <select
              value={xpacShort}
              onChange={e => setXpacShort(e.target.value)}
              className={`${inputCls} cursor-pointer`}
            >
              <option value="">— none —</option>
              {expansions.map(ex => (
                <option key={ex.short} value={ex.short}>
                  {ex.name}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Launch date */}
        <div>
          <label className="block text-[0.72rem] uppercase tracking-[0.06em] text-text-muted font-semibold mb-1">
            Launch Date (UTC)
          </label>
          <input
            type="datetime-local"
            value={launchDt}
            onChange={e => setLaunchDt(e.target.value)}
            className={`${inputCls} [color-scheme:dark]`}
          />
        </div>
      </div>

      {/* Save row */}
      <div className="flex items-center gap-3 mt-4 flex-wrap">
        <Button variant="primary" size="sm" onClick={handleSave} disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </Button>
        {result && (
          <span className={`text-[0.82rem] ${result.ok ? 'text-success' : 'text-danger'}`}>
            {result.msg}
          </span>
        )}
      </div>
    </div>
  )
}

// ── ServersSection ────────────────────────────────────────────────────────────

export function ServersSection() {
  const [servers,    setServers]    = useState<ServerConfig[]>([])
  const [expansions, setExpansions] = useState<ExpansionEntry[]>([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  // Track which world is currently selected as default (may differ from server
  // truth while the user has changed the radio but not yet saved)
  const [defaultWorld, setDefaultWorld] = useState<string>('')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [sRes, eRes] = await Promise.all([
        fetch('/api/admin/servers',    { credentials: 'include' }),
        fetch('/api/admin/expansions', { credentials: 'include' }),
      ])
      if (!sRes.ok) {
        const body = await sRes.json().catch(() => ({}))
        setError(`Error loading servers: ${body.detail ?? sRes.statusText}`)
        return
      }
      const sData: ServerConfig[] = await sRes.json()
      // Expansions are optional; silently fall back to [] on failure
      const eData: ExpansionEntry[] = eRes.ok ? await eRes.json().catch(() => []) : []

      setServers(sData)
      setExpansions(eData)
      const def = sData.find(s => s.is_default)
      setDefaultWorld(def?.world ?? sData[0]?.world ?? '')
    } catch {
      setError('Network error — could not load server settings.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  return (
    <div>
      <p className={SECTION_TITLE_CLS}>
        Servers ({servers.length})
      </p>

      {loading && <p className="text-text-muted text-[0.88rem]">Loading…</p>}
      {error   && <p className="text-danger text-[0.88rem]">{error}</p>}

      {!loading && !error && servers.length === 0 && (
        <p className="text-text-muted text-[0.88rem]">No servers configured.</p>
      )}

      {!loading && !error && servers.map(server => (
        <ServerRow
          key={server.world}
          server={server}
          expansions={expansions}
          defaultWorld={defaultWorld}
          onDefaultChange={setDefaultWorld}
          onSaved={fetchData}
        />
      ))}
    </div>
  )
}
