import { Link } from 'react-router-dom'

interface CrumbItem {
  label: string
  to?: string
}

/**
 * Horizontal breadcrumb trail.
 * Pass an array of items; the last item is the current page (no link).
 * Example: <Breadcrumb items={[{ label: 'Characters', to: '/characters' }, { label: name }]} />
 */
export default function Breadcrumb({ items }: { items: CrumbItem[] }) {
  return (
    <nav
      aria-label="Breadcrumb"
      className="flex items-center gap-1.5 mb-3 text-[0.88rem] text-text-muted flex-wrap"
    >
      {items.map((item, i) => {
        const isLast = i === items.length - 1
        return (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && (
              <span className="opacity-45 select-none">›</span>
            )}
            {item.to && !isLast ? (
              <Link
                to={item.to}
                className="text-text-muted no-underline"
                onMouseEnter={e => (e.currentTarget.style.color = 'var(--text)')}
                onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
              >
                {item.label}
              </Link>
            ) : (
              <span className={isLast ? 'text-text' : 'text-text-muted'}>
                {item.label}
              </span>
            )}
          </span>
        )
      })}
    </nav>
  )
}
