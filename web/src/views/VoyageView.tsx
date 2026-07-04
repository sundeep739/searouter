import { useEffect, useState } from 'react'
import { api, type Loc, type VoyageResp } from '../lib/api'
import { downloadCsv } from '../lib/csv'
import { fmtNm, fmtTransit, toLocalInput } from '../lib/format'
import { EMPTY_LAYERS, type MapLayers } from '../components/MapView'
import PortSearch from '../components/PortSearch'
import Saved, { saveItem } from '../components/Saved'
import { Btn, Card, ErrorNote, Field, SectionTitle, Spinner } from '../components/ui'
import { useAuth } from '../state/auth'
import type { MapCtl } from './RouteView'

const LEG_PALETTE = ['#0ea5e9', '#f97316', '#a855f7', '#22c55e', '#eab308', '#14b8a6', '#f43f5e']

export default function VoyageView({ map, active }: { map: MapCtl; active: boolean }) {
  const { session } = useAuth()
  const [ports, setPorts] = useState<Loc[]>([])
  const [adding, setAdding] = useState<Loc | null>(null)
  const [speed, setSpeed] = useState(17)
  const [dwell, setDwell] = useState(12)
  const [useDeparture, setUseDeparture] = useState(false)
  const [departure, setDeparture] = useState(toLocalInput(new Date()))
  const [result, setResult] = useState<VoyageResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [savedRefresh, setSavedRefresh] = useState(0)

  useEffect(() => {
    if (!active) return
    map.setClickMode('none')
    map.onClick.current = null
  }, [map, active])

  // Map: markers for the rotation, colored legs when computed.
  useEffect(() => {
    if (!active) return
    const layers: MapLayers = { ...EMPTY_LAYERS, routes: [], markers: [] }
    ports.forEach((p, i) =>
      layers.markers.push({
        lon: p.lon,
        lat: p.lat,
        color: i === 0 ? '#0d9488' : i === ports.length - 1 ? '#e11d48' : '#64748b',
        label: `${i + 1}. ${p.name}`,
      }),
    )
    if (result) {
      result.legs.forEach((leg, i) =>
        layers.routes.push({
          id: `leg${i}`,
          coords: leg.coords,
          color: LEG_PALETTE[i % LEG_PALETTE.length],
          width: 3,
        }),
      )
      layers.fitKey = `voyage-${result.total_nm}-${result.legs.length}`
    }
    map.setLayers(layers)
  }, [ports, result, map, active])

  const move = (i: number, dir: -1 | 1) => {
    setPorts((ps) => {
      const next = [...ps]
      const j = i + dir
      if (j < 0 || j >= next.length) return ps
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
    setResult(null)
  }

  const run = async (codes?: string[], sp?: number, dw?: number) => {
    const list = codes ?? ports.map((p) => p.unlocode)
    setBusy(true)
    setError('')
    try {
      const r = await api<VoyageResp>('/voyage', {
        ports: list,
        speed: sp ?? speed,
        dwell_hours: dw ?? dwell,
        departure: useDeparture ? new Date(departure).toISOString() : null,
      })
      setResult(r)
      if (codes) {
        // Loaded from a saved rotation — rebuild the port chips from response.
        const locs: Loc[] = [r.legs[0].from, ...r.legs.map((l) => l.to)]
        setPorts(locs)
      }
    } catch (e) {
      setResult(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="space-y-3">
        <SectionTitle>Port rotation</SectionTitle>
        {ports.length > 0 && (
          <ol className="space-y-1.5">
            {ports.map((p, i) => (
              <li
                key={`${p.unlocode}-${i}`}
                className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-navy-900"
              >
                <span className="w-5 shrink-0 text-center font-mono text-xs text-slate-400">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate text-slate-800 dark:text-slate-100">
                  {p.name}, {p.country}
                  <span className="ml-1.5 font-mono text-xs text-slate-400">{p.unlocode}</span>
                </span>
                <button className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30 dark:hover:text-slate-200" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
                <button className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30 dark:hover:text-slate-200" disabled={i === ports.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
                <button
                  className="px-1 text-slate-400 hover:text-rose-500"
                  onClick={() => {
                    setPorts((ps) => ps.filter((_, j) => j !== i))
                    setResult(null)
                  }}
                  aria-label="Remove"
                >
                  ✕
                </button>
              </li>
            ))}
          </ol>
        )}
        <PortSearch
          value={adding}
          onSelect={(l) => {
            if (l) {
              setPorts((ps) => [...ps, l])
              setResult(null)
            }
            setAdding(null)
          }}
          placeholder={ports.length === 0 ? 'Add first port…' : 'Add next port…'}
        />

        <div className="grid grid-cols-2 gap-2">
          <Field
            label="Speed (kn)"
            type="number"
            min={3}
            max={30}
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
          />
          <Field
            label="Port dwell (h)"
            type="number"
            min={0}
            value={dwell}
            onChange={(e) => setDwell(Number(e.target.value))}
          />
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <input
            type="checkbox"
            checked={useDeparture}
            onChange={(e) => setUseDeparture(e.target.checked)}
            className="accent-teal-600"
          />
          Set departure for ETAs
        </label>
        {useDeparture && (
          <Field
            label="Departure"
            type="datetime-local"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
          />
        )}

        <div className="flex gap-2">
          <Btn className="flex-1" disabled={ports.length < 2 || busy} onClick={() => run()}>
            {busy ? <Spinner className="border-white" /> : 'Plan voyage'}
          </Btn>
          {result && session && (
            <Btn
              variant="ghost"
              onClick={async () => {
                await saveItem(
                  session.user.id,
                  'voyage',
                  ports.map((p) => p.unlocode).join(' → '),
                  { ports: ports.map((p) => p.unlocode), speed, dwell_hours: dwell },
                )
                setSavedRefresh((n) => n + 1)
              }}
            >
              Save
            </Btn>
          )}
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      {result && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <SectionTitle>Legs</SectionTitle>
            <button
              className="text-xs text-sea-600 underline-offset-2 hover:underline dark:text-sea-400"
              onClick={() =>
                downloadCsv(
                  'voyage.csv',
                  result.legs.map((l, i) => ({
                    leg: i + 1,
                    from: l.from.unlocode,
                    to: l.to.unlocode,
                    distance_nm: l.distance_nm,
                    hours: l.hours,
                    via: l.passages.join('; '),
                    etd: l.etd ?? '',
                    eta: l.eta ?? '',
                  })),
                )
              }
            >
              Export CSV
            </button>
          </div>
          {result.legs.map((l, i) => (
            <Card key={i} className="!p-3">
              <div className="flex items-center gap-2 text-sm">
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: LEG_PALETTE[i % LEG_PALETTE.length] }} />
                <span className="flex-1 truncate font-medium text-slate-800 dark:text-slate-100">
                  {l.from.name} → {l.to.name}
                </span>
                <span className="font-semibold">{fmtNm(l.distance_nm)}</span>
              </div>
              <div className="mt-1 pl-4.5 text-xs text-slate-500 dark:text-slate-400">
                {fmtTransit(l.hours)}
                {l.passages.length > 0 && <> · via {l.passages.join(', ')}</>}
                {l.eta && (
                  <>
                    {' '}· ETA{' '}
                    {new Date(l.eta).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                  </>
                )}
              </div>
            </Card>
          ))}
          <Card className="!p-3 text-sm">
            <div className="flex justify-between font-medium text-slate-800 dark:text-slate-100">
              <span>Total</span>
              <span>{fmtNm(result.total_nm)}</span>
            </div>
            <div className="mt-0.5 flex justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>{fmtTransit(result.sea_hours)} at sea</span>
              <span>{fmtTransit(result.total_hours)} incl. port time</span>
            </div>
          </Card>
        </div>
      )}

      <Saved
        kind="voyage"
        refreshKey={savedRefresh}
        onLoad={(p) => {
          setSpeed((p.speed as number) ?? 17)
          setDwell((p.dwell_hours as number) ?? 12)
          run(p.ports as string[], p.speed as number, p.dwell_hours as number)
        }}
      />
    </>
  )
}
