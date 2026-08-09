import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

// ── Shared editor primitives ──────────────────────────────────────────────────

export const inputCls =
  'w-full bg-bg/60 border border-border rounded-sm px-2 py-1 text-text outline-none focus:border-gold/60 appearance-none'

/**
 * Integer input that actually accepts negatives. A controlled
 * `<input type="number">` coerced with `Number()` silently eats a leading
 * "-" (the lone minus parses to empty → 0, and the re-render wipes it), so
 * a field like the spell-timer "remove at" value (−15 = linger past zero)
 * was impossible to enter. This keeps the raw text locally so partial
 * entries ("", "-") survive mid-edit, commits the parsed int as it becomes
 * valid, and normalises on blur.
 */
export function IntInput({
  value,
  onChange,
  min,
  title,
  fallback = 0,
}: {
  value: number
  onChange: (v: number) => void
  min?: number
  title?: string
  fallback?: number
}) {
  const [raw, setRaw] = useState(String(value))

  // Re-sync when the value changes from outside (loading a different draft),
  // but not while the user is mid-edit on an equivalent value.
  useEffect(() => {
    if (Number(raw) !== value) setRaw(String(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  return (
    <input
      type="text"
      inputMode="numeric"
      title={title}
      value={raw}
      onChange={e => {
        const next = e.target.value
        if (next !== '' && next !== '-' && !/^-?\d+$/.test(next)) return
        setRaw(next)
        if (/^-?\d+$/.test(next)) {
          const n = Number(next)
          onChange(min != null ? Math.max(min, n) : n)
        }
      }}
      onBlur={() => {
        if (!/^-?\d+$/.test(raw)) {
          setRaw(String(fallback))
          onChange(fallback)
        }
      }}
      className={inputCls}
    />
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-gold-dim uppercase tracking-[0.08em] text-[0.7rem]">{label}</span>
      {children}
    </label>
  )
}

export function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        className="appearance-none w-4 h-4 border border-border rounded-sm bg-bg/60 checked:bg-gold/40 checked:border-gold cursor-pointer"
      />
      <span className="text-text">{label}</span>
    </label>
  )
}
