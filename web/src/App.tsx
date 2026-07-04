import { useEffect, useMemo, useRef, useState } from 'react'
import { onApiSlow } from './lib/api'
import type { Pt } from './lib/geometry'
import MapView, { EMPTY_LAYERS, type MapLayers } from './components/MapView'
import Sheet from './components/Sheet'
import TabBar, { type Tab } from './components/TabBar'
import { Spinner } from './components/ui'
import { AuthProvider, useAuth } from './state/auth'
import { supabase } from './lib/supabase'
import AdminView from './views/AdminView'
import LoginView from './views/LoginView'
import MatrixView from './views/MatrixView'
import RouteView, { type MapCtl } from './views/RouteView'
import VerifyView from './views/VerifyView'
import VesselsView from './views/VesselsView'
import VoyageView from './views/VoyageView'

function ProfileMenu() {
  const { profile } = useAuth()
  const [open, setOpen] = useState(false)
  const initials = (profile?.email ?? '?').slice(0, 2).toUpperCase()
  return (
    <div className="relative">
      <button
        className="flex size-10 items-center justify-center rounded-full border border-slate-200 bg-white text-xs font-semibold text-slate-600 shadow-md dark:border-slate-600 dark:bg-navy-800 dark:text-slate-200"
        onClick={() => setOpen((o) => !o)}
        aria-label="Account menu"
      >
        {initials}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-56 rounded-2xl border border-slate-200 bg-white p-3 shadow-xl dark:border-slate-600 dark:bg-navy-800">
          <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
            {profile?.email}
          </p>
          <p className="mt-0.5 text-xs capitalize text-slate-400">{profile?.role ?? 'member'}</p>
          <button
            className="mt-3 w-full rounded-xl bg-slate-100 py-2 text-sm font-medium text-slate-600 hover:bg-slate-200 dark:bg-navy-950 dark:text-slate-300"
            onClick={() => supabase.auth.signOut()}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

function Shell() {
  const { profile } = useAuth()
  const [tab, setTab] = useState<Tab>('route')
  const [layers, setLayers] = useState<MapLayers>(EMPTY_LAYERS)
  const [clickMode, setClickMode] = useState<'none' | 'waypoint' | 'polygon'>('none')
  const [showSeamap, setShowSeamap] = useState(false)
  const [slow, setSlow] = useState(false)
  const clickRef = useRef<((p: Pt) => void) | null>(null)

  useEffect(() => onApiSlow(setSlow), [])

  const mapCtl: MapCtl = useMemo(
    () => ({ setLayers, setClickMode, onClick: clickRef }),
    [],
  )

  const mapless = tab === 'matrix' || tab === 'verify' || tab === 'vessels' || tab === 'admin'
  useEffect(() => {
    if (mapless) setClickMode('none')
  }, [mapless])

  return (
    <div className="fixed inset-0 overflow-hidden bg-slate-100 dark:bg-navy-950">
      <div className="absolute inset-0 flex md:left-16">
        {/* Views stay mounted so switching tabs never loses work */}
        <div className={tab === 'route' ? 'contents' : 'hidden'}>
          <Sheet>
            <RouteView map={mapCtl} active={tab === 'route'} />
          </Sheet>
        </div>
        <div className={tab === 'voyage' ? 'contents' : 'hidden'}>
          <Sheet>
            <VoyageView map={mapCtl} active={tab === 'voyage'} />
          </Sheet>
        </div>
        <div className={tab === 'matrix' ? 'contents' : 'hidden'}>
          <Sheet mapless>
            <MatrixView />
          </Sheet>
        </div>
        <div className={tab === 'verify' ? 'contents' : 'hidden'}>
          <Sheet mapless>
            <VerifyView />
          </Sheet>
        </div>
        <div className={tab === 'vessels' ? 'contents' : 'hidden'}>
          <Sheet mapless>
            <VesselsView />
          </Sheet>
        </div>
        {profile?.role === 'admin' && (
          <div className={tab === 'admin' ? 'contents' : 'hidden'}>
            <Sheet mapless>
              <AdminView />
            </Sheet>
          </div>
        )}

        {/* Map region */}
        <div className="relative min-w-0 flex-1">
          <MapView
            layers={layers}
            showSeamap={showSeamap}
            clickMode={clickMode}
            onMapClick={(p) => clickRef.current?.(p)}
          />
          <div className="absolute right-3 top-3 flex flex-col items-end gap-2">
            <ProfileMenu />
            <button
              className={`flex size-10 items-center justify-center rounded-full border shadow-md transition ${
                showSeamap
                  ? 'border-sea-500 bg-sea-600 text-white'
                  : 'border-slate-200 bg-white text-slate-500 dark:border-slate-600 dark:bg-navy-800 dark:text-slate-300'
              }`}
              onClick={() => setShowSeamap((s) => !s)}
              title="Nautical chart overlay (OpenSeaMap)"
              aria-label="Toggle nautical overlay"
            >
              ⚓
            </button>
          </div>
          {slow && (
            <div className="absolute inset-x-0 top-3 mx-auto w-fit rounded-full bg-navy-900/90 px-4 py-2 text-xs text-slate-200 shadow-lg">
              <Spinner className="mr-2 align-middle" /> Server is waking up — the first request
              can take up to a minute…
            </div>
          )}
        </div>
      </div>

      <TabBar tab={tab} setTab={setTab} isAdmin={profile?.role === 'admin'} />
    </div>
  )
}

function Gate() {
  const { session, loading, needsPassword } = useAuth()
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-navy-950">
        <Spinner className="size-6 border-slate-500" />
      </div>
    )
  }
  if (!session || needsPassword) return <LoginView />
  return <Shell />
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}
