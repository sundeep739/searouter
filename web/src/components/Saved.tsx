import { useCallback, useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../state/auth'
import { SectionTitle, Spinner } from './ui'

export interface SavedItem {
  id: string
  name: string
  payload: Record<string, unknown>
  created_at: string
}

/** Saved routes / voyages stored per-user in Supabase. */
export default function Saved({
  kind,
  onLoad,
  refreshKey,
}: {
  kind: 'route' | 'voyage'
  onLoad: (payload: Record<string, unknown>) => void
  refreshKey: number
}) {
  const { session } = useAuth()
  const [items, setItems] = useState<SavedItem[] | null>(null)

  const refresh = useCallback(() => {
    supabase
      .from('saved_routes')
      .select('id,name,payload,created_at')
      .eq('kind', kind)
      .order('created_at', { ascending: false })
      .limit(20)
      .then(({ data }) => setItems((data as SavedItem[]) ?? []))
  }, [kind])

  useEffect(() => {
    if (session) refresh()
  }, [session, refresh, refreshKey])

  if (!items) return <Spinner />
  if (items.length === 0) return null
  return (
    <div className="space-y-2">
      <SectionTitle>Saved</SectionTitle>
      <ul className="space-y-1.5">
        {items.map((it) => (
          <li
            key={it.id}
            className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-navy-900"
          >
            <button
              className="min-w-0 flex-1 truncate text-left text-sm text-slate-700 hover:text-sea-600 dark:text-slate-200"
              onClick={() => onLoad(it.payload)}
              title="Load"
            >
              {it.name}
            </button>
            <button
              className="shrink-0 text-xs text-slate-400 hover:text-rose-500"
              onClick={async () => {
                await supabase.from('saved_routes').delete().eq('id', it.id)
                refresh()
              }}
              aria-label={`Delete ${it.name}`}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export async function saveItem(
  userId: string,
  kind: 'route' | 'voyage',
  name: string,
  payload: Record<string, unknown>,
) {
  await supabase.from('saved_routes').insert({ owner_id: userId, kind, name, payload })
}
