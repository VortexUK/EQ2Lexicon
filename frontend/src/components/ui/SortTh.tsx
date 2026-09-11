/**
 * SortTh — table header cell with sort caret. Pairs with useSortable.
 *
 * Renders the label + a small ▲ / ▼ caret when active. Click to toggle the
 * sort key + direction (handled by the useSortable handleSort callback).
 */
import type { CSSProperties, ReactNode, MouseEvent } from 'react'

interface SortThProps<K extends string> {
  /** The key this header sorts on. */
  sortKey: K
  /** The currently active sort key (from useSortable). */
  active: K
  /** Current direction (from useSortable). */
  dir: 'asc' | 'desc'
  /** Click handler (from useSortable). */
  onSort: (key: K) => void
  /** Extra th classes (e.g. text-right for numeric columns). */
  className?: string
  /** Inline styles — use only for runtime-computed values (e.g. data-driven colours). */
  style?: CSSProperties
  /** Native hover tooltip for abbreviated column labels. */
  title?: string
  children: ReactNode
}

export function SortTh<K extends string>({
  sortKey, active, dir, onSort, className = '', style, title, children,
}: SortThProps<K>) {
  const isActive = active === sortKey
  const caret = isActive ? (dir === 'asc' ? '▲' : '▼') : ''
  function handleClick(_e: MouseEvent<HTMLTableCellElement>) {
    onSort(sortKey)
  }
  return (
    <th
      onClick={handleClick}
      style={style}
      title={title}
      className={[
        'cursor-pointer select-none',
        isActive ? 'text-gold' : '',
        className,
      ].filter(Boolean).join(' ')}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {caret && <span className="text-[0.65rem] opacity-80">{caret}</span>}
      </span>
    </th>
  )
}
