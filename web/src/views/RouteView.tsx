import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, PASSAGES, variantLabel, type Loc, type RouteResp } from '../lib/api'
import { routeCrossesPolygons, type Pt } from '../lib/geometry'
import { downloadCsv } from '../lib/csv'
import { eta as fmtEta, fmtNm, fmtTransit, toLocalInput } from '../lib/format'
import { useIsMobile } from '../lib/useMedia'
import { EMPTY_LAYERS, type MapLayers, type WeatherPoint } from '../components/MapView'
import PortSearch, { portLabel } from '../components/PortSearch'
import Saved, { saveItem } from '../components/Saved'
import { useSheet } from '../components/Sheet'
import WeatherSection from '../components/WeatherSection'
import { Btn, Chip, Card, ErrorNote, Field, SectionTitle, Spinner } from '../components/ui'
import { useAuth } from '../state/auth'

const PALETTE = ['#0ea5e9', '#f97316', '#a855f7', '#22c55e', '#eab308', '#14b8a6']
const CUSTOM_COLOR = '#f43f5e'

const PRESETS: [string, string, string][] = [
  ['Rotterdam → Shanghai', 'NLRTM', 'CNSHG'],
  ['Singapore → Rotterdam', 'SGSIN', 'NLRTM'],
  ['Houston → Antwerp', 'USHOU', 'BEANR'],
  ['Santos → Qingdao', 'BRSSZ', 'CNTAO'],
]

export interface MapCtl {
  setLayers: (l: MapLayers) => void
  setClickMode: (m: 'none' | 'waypoint' | 'polygon') => void
  onClick: React.MutableRefObject<((p: Pt) => void) | null>
}

export default function RouteView({ map, active }: { map: MapCtl; active: boolean }) {
  const { session } = useAuth()
  const isMobile = useIsMobile()
  const sheet = useSheet()
  const [origin, setOrigin] = useState<Loc | null>(null)
  const [dest, setDest] = useState<Loc | null>(null)
  const [avoid, setAvoid] = useState<string[]>([])
  const [mode, setMode] = useState<'speed' | 'schedule'>('speed')
  const [speed, setSpeed] = useState(24)
  const [departure, setDeparture] = useState(toLocalInput(new Date()))
  const [etd, setEtd] = useState(toLocalInput(new Date()))
  const [etaIn, setEtaIn] = useState(toLocalInput(new Date(Date.now() + 14 * 86400_000)))
  const [waypoints, setWaypoints] = useState<Pt[]>([])
  const [areas, setAreas] = useState<Pt[][]>([])
  const [draft, setDraft] = useState<Pt[]>([])
  const [drawMode, setDrawMode] = useState<'none' | 'waypoint' | 'polygon'>('none')
  const [result, setResult] = useState<RouteResp | null>(null)
  const [selected, setSelected] = useState(0) // index into variants; -1 = custom
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [savedRefresh, setSavedRefresh] = useState(0)
  const [searchOpen, setSearchOpen] = useState(false)
  const [showOptions, setShowOptions] = useState(false)
  const [showWeather, setShowWeather] = useState(false)
  const [weatherPoints, setWeatherPoints] = useState<WeatherPoint[] | null>(null)
  const bootstrapped = useRef(false)

  // ---- map click routing ----
  useEffect(() => {
    if (!active) return
    map.setClickMode(drawMode)
    map.onClick.current = (p: Pt) => {
      if (drawMode === 'waypoint') setWaypoints((w) => [...w, p])
      if (drawMode === 'polygon') setDraft((d) => [...d, p])
    }
  }, [drawMode, map, active])

  const run = useCallback(
    async (o: string, d: string, opts?: { avoid?: string[]; speed?: number; wps?: Pt[] }) => {
      setBusy(true)
      setError('')
      try {
        const body: Record<string, unknown> = {
          origin: o,
          dest: d,
          avoid: opts?.avoid ?? avoid,
          speed: opts?.speed ?? speed,
          waypoints: opts?.wps ?? waypoints,
        }
        if (mode === 'schedule') {
          body.schedule = { etd: new Date(etd).toISOString(), eta: new Date(etaIn).toISOString() }
        }
        const r = await api<RouteResp>('/route', body)
        setResult(r)
        setOrigin(r.origin)
        setDest(r.dest)
        setSelected(r.custom ? -1 : 0)
        setSearchOpen(false)
        sheet?.raise()
        const params = new URLSearchParams({
          origin: r.origin.unlocode,
          dest: r.dest.unlocode,
          speed: String(body.speed),
        })
        if ((body.avoid as string[]).length) params.set('avoid', (body.avoid as string[]).join(','))
        history.replaceState(null, '', `?${params}`)
      } catch (e) {
        setResult(null)
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [avoid, speed, waypoints, mode, etd, etaIn, sheet],
  )

  // Shareable URL: restore ?origin=&dest=&avoid=&speed= once on mount.
  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    const p = new URLSearchParams(location.search)
    const o = p.get('origin')
    const d = p.get('dest')
    if (o && d) {
      const av = (p.get('avoid') ?? '').split(',').filter(Boolean)
      const sp = Number(p.get('speed')) || 24
      setAvoid(av)
      setSpeed(sp)
      run(o, d, { avoid: av, speed: sp, wps: [] })
    }
  }, [run])

  // ---- push map layers ----
  useEffect(() => {
    if (!active) return
    const layers: MapLayers = { ...EMPTY_LAYERS, polygons: areas, draft, routes: [], markers: [] }
    if (origin) layers.markers.push({ lon: origin.lon, lat: origin.lat, color: '#0d9488', label: portLabel(origin) })
    if (dest) layers.markers.push({ lon: dest.lon, lat: dest.lat, color: '#e11d48', label: portLabel(dest) })
    waypoints.forEach((w, i) =>
      layers.markers.push({ lon: w[0], lat: w[1], color: '#f59e0b', label: `Waypoint ${i + 1}` }),
    )
    if (result) {
      result.variants.forEach((v, i) => {
        layers.routes.push({
          id: v.name,
          coords: v.coords,
          color: PALETTE[i % PALETTE.length],
          width: selected === i ? 4.5 : 2,
          dashed: selected !== i,
        })
      })
      if (result.custom) {
        layers.routes.push({
          id: 'custom',
          coords: result.custom.coords,
          color: CUSTOM_COLOR,
          width: selected === -1 ? 4.5 : 2,
          dashed: selected !== -1,
        })
      }
      layers.fitKey = `${result.origin.unlocode}-${result.dest.unlocode}-${result.variants[0].distance_nm}`
    }
    if (weatherPoints) layers.weatherPoints = weatherPoints
    map.setLayers(layers)
  }, [result, selected, areas, draft, waypoints, origin, dest, map, active, weatherPoints])

  const crossing = useMemo(() => {
    if (!result || areas.length === 0) return false
    const line = selected === -1 && result.custom ? result.custom.coords : result.variants[selected]?.coords
    return line ? routeCrossesPolygons([line], areas) : false
  }, [result, selected, areas])

  const dep = mode === 'speed' ? new Date(departure) : new Date(etd)
  const effSpeed = result?.schedule ? result.schedule.required_speed_knots : speed
  const canRun = !!origin && !!dest && !busy
  const doRun = () => origin && dest && run(origin.unlocode, dest.unlocode)

  const optionsSummary = `${mode === 'speed' ? `${speed} kn` : 'scheduled'}${
    avoid.length ? ` · avoiding ${avoid.length}` : ''
  }${waypoints.length ? ` · ${waypoints.length} wp` : ''}`

  // ---------- reusable blocks ----------

  const portFields = (
    <div className="relative space-y-2">
      <PortSearch label="From" value={origin} onSelect={setOrigin} placeholder="Origin port…" />
      <PortSearch label="To" value={dest} onSelect={setDest} placeholder="Destination port…" />
      <button
        className="absolute -right-1 top-8 rounded-full border border-slate-300 bg-white p-1.5 text-xs text-slate-500 shadow-sm hover:text-sea-600 dark:border-slate-600 dark:bg-navy-800 dark:text-slate-300"
        onClick={() => {
          setOrigin(dest)
          setDest(origin)
        }}
        title="Swap"
        aria-label="Swap origin and destination"
      >
        ⇅
      </button>
    </div>
  )

  const presetsRow = (
    <div className="flex flex-wrap gap-1.5">
      {PRESETS.map(([label, o, d]) => (
        <button
          key={label}
          className="rounded-full bg-slate-200/70 px-2.5 py-1 text-[11px] text-slate-500 hover:bg-slate-300 dark:bg-navy-800 dark:text-slate-400"
          onClick={() => run(o, d, { wps: [] })}
        >
          {label}
        </button>
      ))}
    </div>
  )

  const optionsDrawer = (
    <div className="rounded-2xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-navy-900">
      <button
        className="flex w-full items-center justify-between px-3.5 py-3 text-left"
        onClick={() => setShowOptions((v) => !v)}
      >
        <span className="text-sm font-medium text-slate-700 dark:text-slate-200">Options</span>
        <span className="flex items-center gap-2 text-xs text-slate-400">
          {optionsSummary}
          <span className={`transition ${showOptions ? 'rotate-180' : ''}`}>⌄</span>
        </span>
      </button>
      {showOptions && (
        <div className="space-y-3 border-t border-slate-100 px-3.5 py-3 dark:border-slate-800">
          <div className="flex gap-1 rounded-xl bg-slate-200/70 p-1 text-xs dark:bg-navy-800">
            {(['speed', 'schedule'] as const).map((m) => (
              <button
                key={m}
                className={`flex-1 rounded-lg py-1.5 font-medium transition ${
                  mode === m
                    ? 'bg-white text-slate-800 shadow-sm dark:bg-navy-950 dark:text-slate-100'
                    : 'text-slate-500 dark:text-slate-400'
                }`}
                onClick={() => setMode(m)}
              >
                {m === 'speed' ? 'Fixed speed' : 'Meet a schedule'}
              </button>
            ))}
          </div>
          {mode === 'speed' ? (
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 flex justify-between text-xs font-medium text-slate-500 dark:text-slate-400">
                  Speed <b className="text-sea-600 dark:text-sea-400">{speed} kn</b>
                </span>
                <input
                  type="range"
                  min={3}
                  max={30}
                  step={0.5}
                  value={speed}
                  onChange={(e) => setSpeed(Number(e.target.value))}
                  className="w-full accent-teal-600"
                />
              </label>
              <Field label="Departure" type="datetime-local" value={departure} onChange={(e) => setDeparture(e.target.value)} />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <Field label="ETD" type="datetime-local" value={etd} onChange={(e) => setEtd(e.target.value)} />
              <Field label="ETA" type="datetime-local" value={etaIn} onChange={(e) => setEtaIn(e.target.value)} />
            </div>
          )}

          <div>
            <span className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">
              Avoid passages {avoid.length > 0 && <span className="text-rose-500">({avoid.length})</span>}
            </span>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(PASSAGES).map(([id, label]) => (
                <Chip
                  key={id}
                  active={avoid.includes(id)}
                  onClick={() => setAvoid((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id]))}
                >
                  {label}
                </Chip>
              ))}
            </div>
          </div>

          <div>
            <span className="mb-1.5 block text-xs font-medium text-slate-500 dark:text-slate-400">Draw on map</span>
            <div className="flex flex-wrap items-center gap-1.5">
              <Chip active={drawMode === 'waypoint'} onClick={() => setDrawMode(drawMode === 'waypoint' ? 'none' : 'waypoint')}>
                + Waypoint
              </Chip>
              <Chip active={drawMode === 'polygon'} onClick={() => setDrawMode(drawMode === 'polygon' ? 'none' : 'polygon')}>
                + Avoid area
              </Chip>
              {drawMode === 'polygon' && draft.length >= 3 && (
                <Btn
                  variant="ghost"
                  className="!px-2.5 !py-1 text-xs"
                  onClick={() => {
                    setAreas((a) => [...a, draft])
                    setDraft([])
                  }}
                >
                  Close area ({draft.length})
                </Btn>
              )}
              {(waypoints.length > 0 || areas.length > 0 || draft.length > 0) && (
                <button
                  className="text-xs text-slate-400 underline-offset-2 hover:underline"
                  onClick={() => {
                    setWaypoints([])
                    setAreas([])
                    setDraft([])
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            {drawMode !== 'none' && (
              <p className="mt-1 text-[11px] text-slate-400">
                Tap the map to {drawMode === 'waypoint' ? 'drop waypoints' : 'outline an area, then close it'}.
              </p>
            )}
          </div>

          <Btn className="w-full" disabled={!canRun} onClick={doRun}>
            {busy ? <Spinner className="border-white" /> : result ? 'Recalculate' : 'Calculate route'}
          </Btn>
        </div>
      )}
    </div>
  )

  const resultsBlock = result && (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <SectionTitle>Routes</SectionTitle>
        <div className="flex items-center gap-3">
          {session && (
            <button
              className="text-xs text-slate-500 underline-offset-2 hover:underline dark:text-slate-400"
              onClick={async () => {
                await saveItem(session.user.id, 'route', `${result.origin.unlocode} → ${result.dest.unlocode}`, {
                  origin: result.origin.unlocode,
                  dest: result.dest.unlocode,
                  avoid,
                  speed,
                })
                setSavedRefresh((n) => n + 1)
              }}
            >
              Save
            </button>
          )}
          <button
            className="text-xs text-sea-600 underline-offset-2 hover:underline dark:text-sea-400"
            onClick={() =>
              downloadCsv(
                `route_${result.origin.unlocode}_${result.dest.unlocode}.csv`,
                result.variants.map((v) => ({
                  route: variantLabel(v.name),
                  distance_nm: v.distance_nm,
                  distance_km: v.distance_km,
                  hours_at_speed: v.hours,
                  via: v.passage_names.join('; '),
                })),
              )
            }
          >
            Export
          </button>
        </div>
      </div>

      {result.schedule && (
        <Card className="!p-3 text-sm">
          To make <b>{fmtTransit(result.schedule.target_hours)}</b> you need{' '}
          <b className="text-sea-600 dark:text-sea-400">{result.schedule.required_speed_knots} kn</b> average.
        </Card>
      )}

      {result.custom && (
        <VariantCard
          color={CUSTOM_COLOR}
          label="Via waypoints"
          nm={result.custom.distance_nm}
          hours={result.custom.distance_nm / effSpeed}
          via={[`${waypoints.length} waypoint${waypoints.length === 1 ? '' : 's'}`]}
          dep={dep}
          active={selected === -1}
          onClick={() => setSelected(-1)}
        />
      )}
      {result.variants.map((v, i) => (
        <VariantCard
          key={v.name}
          color={PALETTE[i % PALETTE.length]}
          label={variantLabel(v.name)}
          nm={v.distance_nm}
          hours={v.distance_nm / effSpeed}
          via={v.passage_names}
          dep={dep}
          active={selected === i}
          onClick={() => setSelected(i)}
        />
      ))}
    </div>
  )

  const weatherBlock = origin && dest && (
    <div className="rounded-2xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-navy-900">
      <button
        className="flex w-full items-center justify-between px-3.5 py-3 text-left"
        onClick={() => setShowWeather((v) => !v)}
      >
        <span className="text-sm font-medium text-slate-700 dark:text-slate-200">
          Weather-aware speed plan
        </span>
        <span className={`text-xs text-slate-400 transition ${showWeather ? 'rotate-180' : ''}`}>⌄</span>
      </button>
      {showWeather && (
        <div className="border-t border-slate-100 px-3.5 py-3 dark:border-slate-800">
          <WeatherSection
            origin={origin}
            dest={dest}
            avoid={avoid}
            waypoints={waypoints}
            mode={mode}
            speed={speed}
            departure={departure}
            etd={etd}
            etaIn={etaIn}
            onSampled={setWeatherPoints}
          />
        </div>
      )}
    </div>
  )

  const savedBlock = (
    <Saved
      kind="route"
      refreshKey={savedRefresh}
      onLoad={(p) => {
        const av = (p.avoid as string[]) ?? []
        const sp = (p.speed as number) ?? 24
        setAvoid(av)
        setSpeed(sp)
        run(p.origin as string, p.dest as string, { avoid: av, speed: sp, wps: [] })
      }}
    />
  )

  // ---------- mobile: top search pill + overlay ----------
  // Portaled to <body> so the sheet's backdrop-filter doesn't capture these
  // position:fixed elements (which would pin them to the sheet, not the screen).
  const mobileChrome = isMobile && active && createPortal(
    <>
      <button
        onClick={() => setSearchOpen(true)}
        className="fixed left-3 right-16 z-30 flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/95 px-3.5 py-2.5 text-left shadow-lg backdrop-blur dark:border-slate-600 dark:bg-navy-900/95"
        style={{ top: 'max(12px, env(safe-area-inset-top))' }}
      >
        <span className="text-slate-400">⌕</span>
        {origin && dest ? (
          <span className="flex-1 truncate text-sm font-medium text-slate-800 dark:text-slate-100">
            {origin.name} <span className="text-slate-400">→</span> {dest.name}
          </span>
        ) : (
          <span className="flex-1 truncate text-sm text-slate-400">Set origin and destination</span>
        )}
      </button>

      {searchOpen && (
        <div className="fixed inset-0 z-40 flex flex-col bg-black/30" onClick={() => setSearchOpen(false)}>
          <div
            className="rounded-b-3xl border-b border-slate-200 bg-slate-50 p-4 pt-[max(16px,env(safe-area-inset-top))] shadow-xl dark:border-slate-700 dark:bg-navy-950"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-semibold text-slate-800 dark:text-slate-100">Plan a route</h2>
              <button className="text-sm text-slate-400" onClick={() => setSearchOpen(false)} aria-label="Close">
                ✕
              </button>
            </div>
            <div className="space-y-3">
              {portFields}
              {presetsRow}
              {error && <ErrorNote>{error}</ErrorNote>}
              <Btn className="w-full" disabled={!canRun} onClick={doRun}>
                {busy ? <Spinner className="border-white" /> : 'Calculate route'}
              </Btn>
            </div>
          </div>
        </div>
      )}
    </>,
    document.body,
  )

  // ---------- compose ----------
  if (isMobile) {
    return (
      <>
        {mobileChrome}
        {result ? (
          resultsBlock
        ) : (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-4 py-6 text-center dark:border-slate-700 dark:bg-navy-900/60">
            <p className="text-sm text-slate-500 dark:text-slate-400">Tap the search bar to set two ports.</p>
          </div>
        )}
        {error && !searchOpen && <ErrorNote>{error}</ErrorNote>}
        {crossing && (
          <ErrorNote>The selected route crosses your avoid area(s). Add waypoints to steer around them, then recalculate.</ErrorNote>
        )}
        {optionsDrawer}
        {weatherBlock}
        {savedBlock}
      </>
    )
  }

  // desktop
  return (
    <>
      <div className="space-y-3">
        {portFields}
        {presetsRow}
        <Btn className="w-full" disabled={!canRun} onClick={doRun}>
          {busy ? <Spinner className="border-white" /> : 'Calculate route'}
        </Btn>
        {error && <ErrorNote>{error}</ErrorNote>}
        {crossing && (
          <ErrorNote>The selected route crosses your avoid area(s). Add waypoints to steer around them, then recalculate.</ErrorNote>
        )}
      </div>
      {optionsDrawer}
      {resultsBlock}
      {weatherBlock}
      {savedBlock}
    </>
  )
}

function VariantCard({
  color,
  label,
  nm,
  hours,
  via,
  dep,
  active,
  onClick,
}: {
  color: string
  label: string
  nm: number
  hours: number
  via: string[]
  dep: Date
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`block w-full rounded-2xl border p-3 text-left transition ${
        active
          ? 'border-sea-500 bg-sea-500/5 ring-2 ring-sea-500/30'
          : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-700 dark:bg-navy-900'
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="size-2.5 shrink-0 rounded-full" style={{ background: color }} />
        <span className="flex-1 text-sm font-medium text-slate-800 dark:text-slate-100">{label}</span>
        <span className="text-sm font-semibold text-slate-900 dark:text-white">{fmtNm(nm)}</span>
      </div>
      <div className="mt-1 flex items-center justify-between pl-4.5 text-xs text-slate-500 dark:text-slate-400">
        <span className="truncate">{via.length ? 'via ' + via.join(', ') : 'open water'}</span>
        <span className="ml-2 shrink-0">
          {fmtTransit(hours)} · ETA {fmtEta(dep, hours)}
        </span>
      </div>
    </button>
  )
}
