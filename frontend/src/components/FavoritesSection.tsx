import { useFetch } from '../hooks/useFetch'
import CharacterSummaryCard from './CharacterSummaryCard'

interface FavoriteEntry {
  character_name: string
  world: string
  created_at: number
  level: number | null
  cls: string | null
  ts_class: string | null
  ts_level: number | null
  guild_name: string | null
}

/**
 * Home-page "Favourites" grid — the signed-in user's bookmarked characters on
 * the current server, served pre-enriched by GET /api/favorites (one round
 * trip, census-store data, never blocks on Census). Renders nothing while
 * loading, on error, or when the user has no favourites.
 */
export default function FavoritesSection() {
  const { data } = useFetch<{ favorites: FavoriteEntry[] }>('/api/favorites')

  if (!data || data.favorites.length === 0) return null

  return (
    <section>
      <h2 className="font-heading text-[0.88rem] font-semibold tracking-[0.1em] uppercase text-gold/70 mt-0 mx-0 mb-3">
        Favourites
      </h2>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
        {data.favorites.map(f => (
          <CharacterSummaryCard
            key={f.character_name}
            name={f.character_name}
            guildName={f.guild_name}
            cls={f.cls}
            level={f.level}
            tsClass={f.ts_class}
            tsLevel={f.ts_level}
            detailLoaded={true}
          />
        ))}
      </div>
    </section>
  )
}
