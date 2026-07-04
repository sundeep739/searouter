import { useEffect, useState } from 'react'
import { api, type Loc, type WeatherPlanResp } from '../lib/api'
import type { Pt, } from '../lib/geometry'
import type { WeatherPoint } from './MapView'
import { supabase } from '../lib/supabase'
import { fmtTransit } from '../lib/format'
import { Btn, Card, ErrorNote, Spinner } from './ui'

interface VesselOption {
  label: string
  builtin: boolean
  spec: Record<string, unknown>
}

interface Props {
  origin: Loc | null
  dest: Loc | null
  avoid: string[]
  waypoints: Pt[]
  mode: 'speed' | 'schedule'
  speed: number
  departure: string
  etd: string
  etaIn: string
  onSampled: (points: WeatherPoint[] | null) => void
}

function waveColor(m: number | null): string {
  if (m == null) return 'var(--text-muted)'
  if (m >= 4) return '#ef4444'
  if (m >= 2) return '#eab308'
  return '#22c55e'
}

function currentColor(assist: number | null): string {
  if (assist == null) return '#94a3b8'
  if (assist > 0.05) return '#059669' // helping
  if (assist < -0.05) return '#e11d48' // hindering
  return '#94a3b8'
}

/** Optimized speed-through-water per leg (teal line) against the constant-
 *  speed baseline (dashed), with wave height shaded behind to show why the
 *  plan eases off. */
function SpeedChart({
  speeds,
  baseline,
  waves,
}: {
  speeds: number[]
  baseline: number
  waves: number[]
}) {
  const W = 300
  const H = 120
  const padL = 26
  const padR = 8
  const padT = 8
  const padB = 16
  const n = speeds.length
  if (n < 2) return null
  const all = [...speeds, baseline]
  const lo = Math.floor(Math.min(...all) - 0.5)
  const hi = Math.ceil(Math.max(...all) + 0.5)
  const x = (i: number) => padL + (i / (n - 1)) * (W - padL - padR)
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo || 1)) * (H - padT - padB)
  const waveMax = Math.max(1, ...waves.map((w) => w || 0))
  const line = speeds.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(s).toFixed(1)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 130 }} role="img" aria-label="Optimized vs constant speed">
      {/* wave-height bars (context) */}
      {waves.map((w, i) => {
        const h = ((w || 0) / waveMax) * (H - padT - padB) * 0.5
        return (
          <rect
            key={i}
            x={x(i) - 3}
            y={H - padB - h}
            width={6}
            height={h}
            fill="#eab308"
            opacity={0.18}
          />
        )
      })}
      {/* y grid labels */}
      {[lo, Math.round((lo + hi) / 2), hi].map((v) => (
        <g key={v}>
          <line x1={padL} y1={y(v)} x2={W - padR} y2={y(v)} stroke="#94a3b8" strokeWidth={0.4} opacity={0.3} />
          <text x={2} y={y(v) + 3} fontSize={9} fill="#94a3b8">
            {v}
          </text>
        </g>
      ))}
      {/* constant-speed baseline */}
      <line x1={padL} y1={y(baseline)} x2={W - padR} y2={y(baseline)} stroke="#94a3b8" strokeWidth={1.4} strokeDasharray="4 3" />
      {/* optimized line */}
      <path d={line} fill="none" stroke="#0d9488" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {speeds.map((s, i) => (
        <circle key={i} cx={x(i)} cy={y(s)} r={2.2} fill="#0d9488" />
      ))}
    </svg>
  )
}

export default function WeatherSection(props: Props) {
  const { origin, dest, avoid, waypoints, mode, speed, departure, etd, etaIn, onSampled } = props
  const [vessels, setVessels] = useState<VesselOption[]>([])
  const [vesselIdx, setVesselIdx] = useState(0)
  const [loading, setLoading] = useState<'laden' | 'ballast'>('laden')
  const [objective, setObjective] = useState<'steady_power' | 'min_fuel'>('steady_power')
  const [plan, setPlan] = useState<WeatherPlanResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showLegs, setShowLegs] = useState(false)

  useEffect(() => {
    // built-in presets + this user's saved vessels
    Promise.all([
      api<{ presets: Record<string, Record<string, unknown>> }>('/vessels/presets').catch(() => ({ presets: {} })),
      supabase.from('vessels').select('name,spec').order('updated_at', { ascending: false }),
    ]).then(([p, saved]) => {
      const opts: VesselOption[] = Object.entries(p.presets).map(([label, spec]) => ({
        label,
        builtin: true,
        spec,
      }))
      ;(saved.data ?? []).forEach((v: { name: string; spec: Record<string, unknown> }) =>
        opts.push({ label: v.name, builtin: false, spec: v.spec }),
      )
      setVessels(opts)
    })
  }, [])

  const run = async () => {
    if (!origin || !dest || vessels.length === 0) return
    setBusy(true)
    setError('')
    try {
      const body: Record<string, unknown> = {
        origin: origin.unlocode,
        dest: dest.unlocode,
        avoid,
        waypoints,
        vessel: vessels[vesselIdx].spec,
        loading,
        objective,
        interval_hours: 12,
      }
      if (mode === 'schedule') {
        body.schedule = { etd: new Date(etd).toISOString(), eta: new Date(etaIn).toISOString() }
      } else {
        body.speed = speed
        body.departure = new Date(departure).toISOString()
      }
      const r = await api<WeatherPlanResp>('/weather/plan', body)
      setPlan(r)
      onSampled(r.sampled.map((s) => ({ lon: s.lon, lat: s.lat, wave: s.wave_height_m })))
    } catch (e) {
      setPlan(null)
      onSampled(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (vessels.length === 0) {
    return (
      <Card className="!p-3 text-sm text-slate-500 dark:text-slate-400">
        Add a vessel in the Vessels tab to enable weather-aware speed planning.
      </Card>
    )
  }

  const p = plan?.plan
  const saved = p?.fuel_saved_pct ?? 0

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-2">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Vessel</span>
          <select
            value={vesselIdx}
            onChange={(e) => setVesselIdx(Number(e.target.value))}
            className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm dark:border-slate-600 dark:bg-navy-800 dark:text-slate-100"
          >
            {vessels.map((v, i) => (
              <option key={i} value={i}>
                {v.label}
                {v.builtin ? ' (preset)' : ''}
              </option>
            ))}
          </select>
        </label>
        <div className="grid grid-cols-2 gap-2">
          <Toggle
            options={[
              ['laden', 'Laden'],
              ['ballast', 'Ballast'],
            ]}
            value={loading}
            onChange={(v) => setLoading(v as 'laden' | 'ballast')}
          />
          <Toggle
            options={[
              ['steady_power', 'Steady power'],
              ['min_fuel', 'Min fuel'],
            ]}
            value={objective}
            onChange={(v) => setObjective(v as 'steady_power' | 'min_fuel')}
          />
        </div>
      </div>

      <Btn className="w-full" disabled={!origin || !dest || busy} onClick={run}>
        {busy ? <Spinner className="border-white" /> : 'Plan with weather'}
      </Btn>
      {error && <ErrorNote>{error}</ErrorNote>}

      {plan && p && (
        <div className="space-y-2">
          {plan.hazards.count > 0 ? (
            <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              ⚠ {plan.hazards.count} leg{plan.hazards.count === 1 ? '' : 's'} in rough weather — up to{' '}
              {plan.hazards.max_wave_m} m seas, {plan.hazards.max_wind_ms} m/s wind.
            </div>
          ) : (
            <div className="rounded-xl border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
              No severe weather along the route (max {plan.hazards.max_wave_m ?? 0} m seas).
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <Metric label="Optimized fuel" value={p.fuel_optimized_t != null ? `${p.fuel_optimized_t} t` : '—'} />
            <Metric
              label={objective === 'min_fuel' ? 'Fuel saved' : 'vs constant speed'}
              value={`${saved > 0 ? '−' : saved < 0 ? '+' : ''}${Math.abs(saved).toFixed(1)}%`}
              accent={saved > 0.1}
            />
          </div>
          <p className="text-[11px] text-slate-400">
            {plan.vessel} · {plan.sampled_points} weather points · wind, waves and currents applied ·
            required avg {p.baseline_speed_knots} kn · {fmtTransit(p.total_hours_optimized ?? 0)}
            {!p.sfoc_modeled && ' · flat SFOC (no engine data)'}
          </p>

          {plan.legs.length >= 2 && p.baseline_speed_knots != null && (
            <div className="rounded-xl border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-navy-900">
              <SpeedChart
                speeds={plan.legs.map((l) => l.stw_knots ?? p.baseline_speed_knots ?? 0)}
                baseline={p.baseline_speed_knots}
                waves={plan.legs.map((l) => l.wave_height_m ?? 0)}
              />
              <div className="mt-1 flex items-center justify-center gap-4 text-[10px] text-slate-500 dark:text-slate-400">
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0.5 w-4" style={{ background: '#0d9488' }} /> optimized STW
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0.5 w-4 border-t border-dashed border-slate-400" /> constant speed
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2" style={{ background: '#eab308', opacity: 0.4 }} /> waves
                </span>
              </div>
            </div>
          )}

          <button
            className="text-xs text-sea-600 underline-offset-2 hover:underline dark:text-sea-400"
            onClick={() => setShowLegs((v) => !v)}
          >
            {showLegs ? 'Hide' : 'Show'} per-leg detail ({plan.legs.length})
          </button>

          {showLegs && (
            <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
              <table className="w-full min-w-max text-[11px]">
                <thead>
                  <tr className="bg-slate-100 text-left text-slate-500 dark:bg-navy-800 dark:text-slate-400">
                    <th className="px-2 py-1.5 font-medium">ETA</th>
                    <th className="px-2 py-1.5 text-right font-medium">STW</th>
                    <th className="px-2 py-1.5 text-right font-medium">SOG</th>
                    <th className="px-2 py-1.5 text-right font-medium">Wave</th>
                    <th className="px-2 py-1.5 text-right font-medium">Wind</th>
                    <th className="px-2 py-1.5 text-right font-medium" title="Current along track (+ helps)">Cur</th>
                    <th className="px-2 py-1.5 text-right font-medium">kW</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.legs.map((leg, i) => (
                    <tr
                      key={i}
                      className={`border-t border-slate-100 dark:border-slate-800 ${leg.hazard ? 'bg-amber-50 dark:bg-amber-950/30' : ''}`}
                    >
                      <td className="px-2 py-1.5 text-slate-600 dark:text-slate-300">
                        {new Date(leg.eta).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' })}
                      </td>
                      <td className="px-2 py-1.5 text-right font-medium">{leg.stw_knots}</td>
                      <td className="px-2 py-1.5 text-right text-slate-500">{leg.sog_knots}</td>
                      <td className="px-2 py-1.5 text-right font-medium" style={{ color: waveColor(leg.wave_height_m) }}>
                        {leg.wave_height_m ?? '—'}
                      </td>
                      <td className="px-2 py-1.5 text-right text-slate-500">{leg.wind_speed_ms ?? '—'}</td>
                      <td
                        className="px-2 py-1.5 text-right font-medium"
                        style={{ color: currentColor(leg.current_assist_kn) }}
                      >
                        {leg.current_assist_kn == null
                          ? '—'
                          : `${leg.current_assist_kn > 0 ? '+' : leg.current_assist_kn < 0 ? '−' : ''}${Math.abs(leg.current_assist_kn).toFixed(1)}`}
                      </td>
                      <td className="px-2 py-1.5 text-right text-slate-500">{leg.power_kw ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Toggle({
  options,
  value,
  onChange,
}: {
  options: [string, string][]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex gap-1 rounded-xl bg-slate-200/70 p-1 text-xs dark:bg-navy-800">
      {options.map(([v, label]) => (
        <button
          key={v}
          className={`flex-1 rounded-lg py-1.5 font-medium transition ${
            value === v
              ? 'bg-white text-slate-800 shadow-sm dark:bg-navy-950 dark:text-slate-100'
              : 'text-slate-500 dark:text-slate-400'
          }`}
          onClick={() => onChange(v)}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl bg-slate-100 p-2.5 dark:bg-navy-800">
      <p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p>
      <p className={`text-lg font-medium ${accent ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-800 dark:text-slate-100'}`}>
        {value}
      </p>
    </div>
  )
}
