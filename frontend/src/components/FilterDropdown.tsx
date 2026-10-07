import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface DropdownOption {
  value: string
  label: string
  group?: string // rows sharing a group get a right-aligned group caption
}

/**
 * Convert a flat `{label, value}[]` list that uses `value: '__hdr'` separator
 * rows + indented labels into grouped
 * DropdownOptions. Header rows become the group for the options beneath them;
 * `──`/leading-space decoration is stripped from labels.
 */
export function groupedFromHeaders(opts: { label: string; value: string }[]): DropdownOption[] {
  const out: DropdownOption[] = []
  let group: string | undefined
  for (const o of opts) {
    if (o.value === '__hdr') {
      group = o.label.replace(/─/g, '').trim()
      continue
    }
    out.push({ value: o.value, label: o.label.trim(), group })
  }
  return out
}

/**
 * Seamless Warcraft-Logs-style filter strip: one continuous gilded bar that
 * holds FilterDropdown segments flush together, divided only by hairlines.
 * `overflow-hidden` clips the segment hovers to the rounded ends — safe because
 * each dropdown's popover is portaled to <body>, not clipped by the bar.
 */
export function FilterBar({ children }: { children: React.ReactNode }) {
  // No pill — a full-width strip framed by a gold rule above and below, with the
  // segments left-aligned and the rules extending across the page.
  return <div className="flex w-full border-y border-gold/70">{children}</div>
}

/**
 * One segment of the FilterBar: a flat "value ▾" trigger (no pill/border of its
 * own) plus a themed popover of hover-highlighted rows, optionally grouped.
 * NOTE: this project omits Tailwind Preflight, so every <button> is explicitly
 * reset (appearance-none, border-0, explicit bg) or it renders as a white block.
 */
export function FilterDropdown({
  value,
  options,
  onChange,
  placeholder = 'Select…',
  disabled = false,
  label,
  active = false,
  standalone = false,
  className = '',
}: {
  value: string
  options: DropdownOption[]
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  /** Fixed trigger text (e.g. a category name "Raids") shown instead of the selected value. */
  label?: string
  /** Highlight as the currently-active tab even when closed. */
  active?: boolean
  /** Bordered, self-contained control for use OUTSIDE a FilterBar (e.g. a form field). */
  standalone?: boolean
  /** Extra classes for the trigger — e.g. width control (`w-full justify-between`, `min-w-[130px]`). */
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number; minWidth: number } | null>(null)

  useLayoutEffect(() => {
    if (open && triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect()
      setPos({ left: r.left, top: r.bottom + 5, minWidth: r.width })
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      const t = e.target as Node
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return
      setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    function onScroll(e: Event) {
      // Close on PAGE scroll (the fixed-position panel would otherwise detach),
      // but ignore scrolling within the panel's own list.
      if (panelRef.current && e.target instanceof Node && panelRef.current.contains(e.target)) return
      setOpen(false)
    }
    function onResize() {
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open])

  const selected = options.find(o => o.value === value)
  const displayText = label ?? (selected ? selected.label : placeholder)
  const dim = !label && !selected // dim only a true placeholder, never a fixed category label

  // Bar variant: flush, transparent, borderless (lives inside the gold-rule
  // FilterBar). Standalone: a bordered, self-contained control for form rows.
  const base = standalone ? 'rounded-md border px-3 py-1.5' : 'border-0 bg-transparent px-4 py-2'
  const triggerState = disabled
    ? standalone
      ? 'cursor-not-allowed border-border bg-surface text-gold/30'
      : 'cursor-not-allowed text-gold/30'
    : open || active
      ? standalone
        ? 'cursor-pointer border-gold bg-surface-raised text-gold-bright'
        : 'cursor-pointer bg-gold/10 text-gold-bright'
      : standalone
        ? 'cursor-pointer border-border bg-surface text-gold hover:border-gold/60'
        : 'cursor-pointer text-gold hover:bg-gold/10 hover:text-gold-bright'

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen(o => !o)}
        className={`flex appearance-none items-center gap-2 font-heading text-sm tracking-wide transition-colors ${base} ${triggerState} ${className}`}
      >
        <span className={`max-w-[14rem] truncate ${dim ? 'opacity-55' : ''}`}>{displayText}</span>
        <span className={`text-[0.6rem] leading-none transition-transform duration-150 ${open ? 'rotate-180' : ''}`}>▼</span>
      </button>

      {open &&
        !disabled &&
        pos &&
        createPortal(
          <div
            ref={panelRef}
            style={{ position: 'fixed', left: pos.left, top: pos.top, minWidth: pos.minWidth }}
            className="z-[9999] max-h-80 overflow-auto rounded-md border border-gold/40 bg-surface-raised py-1 shadow-[0_14px_36px_rgba(0,0,0,0.7)]"
          >
            {options.length === 0 && <div className="px-4 py-1.5 text-sm italic text-text-muted">No options</div>}
            {options.map((opt, i) => {
              const newGroup = opt.group && opt.group !== options[i - 1]?.group
              const isSel = opt.value === value
              return (
                <div key={opt.value || `__${i}`}>
                  {newGroup && (
                    <div className="px-4 pb-0.5 pt-2 text-right text-[0.6rem] uppercase tracking-[0.15em] text-text-muted">
                      {opt.group}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      onChange(opt.value)
                      setOpen(false)
                    }}
                    className={`block w-full cursor-pointer appearance-none whitespace-nowrap border-0 px-4 py-1.5 text-left text-sm transition-colors ${
                      isSel ? 'bg-gold/15 text-gold-bright' : 'bg-transparent text-text hover:bg-gold/10 hover:text-gold-bright'
                    }`}
                  >
                    {opt.label}
                  </button>
                </div>
              )
            })}
          </div>,
          document.body,
        )}
    </>
  )
}
