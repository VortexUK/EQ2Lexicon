import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <main style={{
      maxWidth: 480,
      margin: '0 auto',
      padding: '6rem 1.5rem',
      textAlign: 'center',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: '1rem',
    }}>
      <div style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '5rem',
        fontWeight: 700,
        lineHeight: 1,
        background: 'linear-gradient(135deg, #c8a96e 0%, #e8d5a3 50%, #a07840 100%)',
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
        backgroundClip: 'text',
      }}>
        404
      </div>
      <h1 style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '1.3rem',
        fontWeight: 600,
        color: 'var(--text)',
        margin: 0,
        letterSpacing: '0.05em',
      }}>
        Page Not Found
      </h1>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.92rem', lineHeight: 1.6, margin: 0 }}>
        The scroll you seek does not exist in this realm.
      </p>
      <Link
        to="/"
        style={{
          marginTop: '0.5rem',
          padding: '0.5rem 1.4rem',
          border: '1px solid rgba(200,169,110,0.4)',
          borderRadius: 6,
          color: '#c8a96e',
          textDecoration: 'none',
          fontSize: '0.88rem',
          fontWeight: 600,
          transition: 'border-color 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.borderColor = '#c8a96e')}
        onMouseLeave={e => (e.currentTarget.style.borderColor = 'rgba(200,169,110,0.4)')}
      >
        Return Home
      </Link>
    </main>
  )
}
