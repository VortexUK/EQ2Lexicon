import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <main className="max-w-[480px] mx-auto py-24 px-6 text-center flex flex-col items-center gap-4">
      <div
        className="font-heading text-[5rem] font-bold leading-none"
        style={{
          background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 50%, var(--gold-dim) 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
        }}
      >
        404
      </div>
      <h1 className="font-heading text-[1.3rem] font-semibold text-text m-0 tracking-[0.05em]">
        Page Not Found
      </h1>
      <p className="text-text-muted text-[0.92rem] leading-relaxed m-0">
        The scroll you seek does not exist in this realm.
      </p>
      <Link
        to="/"
        className="mt-2 py-2 px-[1.4rem] rounded-sm2 text-gold no-underline text-[0.88rem] font-semibold transition-colors"
        style={{ border: '1px solid rgba(var(--gold-rgb), 0.4)' }}
        onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--gold)')}
        onMouseLeave={e => (e.currentTarget.style.borderColor = 'rgba(var(--gold-rgb), 0.4)')}
      >
        Return Home
      </Link>
    </main>
  )
}
