import { useNavigate } from 'react-router-dom'

/**
 * "← Back" link that returns to the previous history entry.
 * Falls back to "/" if there is no history (e.g. direct URL navigation).
 */
export default function BackLink() {
  const navigate = useNavigate()
  return (
    <button
      onClick={() => window.history.length > 1 ? navigate(-1) : navigate('/')}
      style={{
        display: 'block',
        background: 'none',
        border: 'none',
        padding: 0,
        marginBottom: '0.6rem',
        cursor: 'pointer',
        color: 'var(--text-muted)',
        fontSize: '0.9rem',
        textDecoration: 'none',
      }}
    >
      ← Back
    </button>
  )
}
