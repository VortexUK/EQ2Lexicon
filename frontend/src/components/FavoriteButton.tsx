import { useEffect, useState } from 'react'
import { useFavorite } from '../hooks/useFavorite'

/**
 * Favourite star + count for a character page's banner. A favourite is a
 * bookmark, not ownership. The whole app sits behind the login gate, so no
 * anonymous affordance is needed — the button is always interactive once the
 * status has loaded.
 */
export default function FavoriteButton({ name }: { name: string }) {
  const { status, pending, error, toggle } = useFavorite(name)
  const [showError, setShowError] = useState(false)

  // Surface errors transiently (e.g. the 50-favourite cap's 409 message).
  useEffect(() => {
    if (!error) return
    setShowError(true)
    const id = setTimeout(() => setShowError(false), 4000)
    return () => clearTimeout(id)
  }, [error])

  if (status === null) return null

  const fav = status.favorited_by_me
  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        type="button"
        onClick={toggle}
        disabled={pending}
        aria-pressed={fav}
        title={fav ? 'Remove from favourites' : 'Add to favourites'}
        className={`appearance-none border-0 bg-transparent p-0 cursor-pointer text-[1.05rem] leading-none transition-transform hover:scale-110 ${
          fav ? 'text-gold' : 'text-text-muted opacity-60 hover:opacity-100'
        }`}
      >
        {fav ? '★' : '☆'}
      </button>
      <span className="text-[0.78rem] text-text-muted" title="Favourited by">
        {status.count.toLocaleString()}
      </span>
      {showError && error && (
        <span className="text-[0.72rem] text-danger">{error}</span>
      )}
    </span>
  )
}
