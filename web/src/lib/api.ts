import { supabase } from './supabase'

// Backend base URL, decided at RUNTIME from the page's own hostname so a
// deployed site can never accidentally point at a developer's localhost
// (which is unreachable from anyone else's device). Only when the app is
// itself served from localhost do we honour a localhost VITE_API_URL.
const PROD_API = 'https://searouter-api.onrender.com'
function resolveBase(): string {
  const envUrl = import.meta.env.VITE_API_URL as string | undefined
  const servedLocally = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname)
  const envIsLocal = !envUrl || /localhost|127\.0\.0\.1/.test(envUrl)
  if (servedLocally) return envUrl ?? 'http://localhost:8000'
  // Deployed: use an explicitly-set remote backend, else the Render default.
  return envIsLocal ? PROD_API : envUrl!
}
const BASE = resolveBase()

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

// The free-tier backend cold-starts after idle; a subscriber can show a
// "waking the server" notice when a request takes suspiciously long.
let slowNotify: ((slow: boolean) => void) | null = null
export function onApiSlow(fn: (slow: boolean) => void) {
  slowNotify = fn
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  const timer = setTimeout(() => slowNotify?.(true), 3000)
  try {
    const res = await fetch(BASE + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const j = await res.json()
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
      } catch { /* non-JSON error body */ }
      throw new ApiError(detail, res.status)
    }
    return res.json()
  } finally {
    clearTimeout(timer)
    slowNotify?.(false)
  }
}

// ---- API response types (mirror backend/main.py) ----

export interface Loc {
  unlocode: string
  name: string
  country: string
  lat: number
  lon: number
  is_seaport: boolean
  resolved_by?: string
}

export interface Variant {
  name: string
  avoided: string | null
  distance_km: number
  distance_nm: number
  hours: number | null
  passages: string[]
  passage_names: string[]
  coords: [number, number][]
}

export interface RouteResp {
  origin: Loc
  dest: Loc
  avoid: string[]
  speed_knots: number
  variants: Variant[]
  custom?: {
    waypoint_order: [number, number][]
    distance_km: number
    distance_nm: number
    hours: number
    coords: [number, number][]
  }
  schedule?: { target_hours: number; required_speed_knots: number }
}

export interface VoyageLeg {
  from: Loc
  to: Loc
  distance_km: number
  distance_nm: number
  hours: number
  passages: string[]
  coords: [number, number][]
  etd?: string
  eta?: string
}

export interface VoyageResp {
  legs: VoyageLeg[]
  total_nm: number
  sea_hours: number
  total_hours: number
}

export interface MatrixResp {
  origins: Loc[]
  dests: Loc[]
  cells: ({ distance_nm: number; hours: number } | null)[][]
}

export interface Assessment {
  name: string
  distance_km: number
  distance_nm: number
  deviation_pct: number
  within_tolerance: boolean
  via: string[]
}

export interface VerifyResp {
  origin: Loc
  dest: Loc
  reported_nm: number
  tolerance_pct: number
  verdict: 'confirmed' | 'mismatch'
  best_match: Assessment
  assessments: Assessment[]
}

export interface BatchResult {
  origin: string
  dest: string
  reported_nm: number
  verdict: 'confirmed' | 'mismatch' | 'error'
  best_match?: Assessment
  error?: string
}

export interface WeatherLeg {
  from: [number, number]
  to: [number, number]
  eta: string
  distance_nm: number | null
  stw_knots: number | null
  current_assist_kn: number | null
  sog_knots: number | null
  power_kw: number | null
  fuel_t: number | null
  wind_speed_ms: number | null
  wave_height_m: number | null
  current_speed_ms: number | null
  hazard: boolean
}

export interface WeatherSampled {
  lon: number
  lat: number
  eta: string
  wind_speed_ms: number | null
  wave_height_m: number | null
  current_speed_ms: number | null
}

export interface WeatherPlanResp {
  vessel: string
  loading: string
  objective: string
  route_nm: number | null
  sampled_points: number
  plan: {
    success: boolean
    message: string
    nominal_speed_knots: number | null
    baseline_speed_knots: number | null
    total_hours_optimized: number | null
    total_hours_baseline: number | null
    fuel_baseline_t: number | null
    fuel_optimized_t: number | null
    fuel_saved_pct: number | null
    power_std_baseline_kw: number | null
    power_std_optimized_kw: number | null
    sfoc_modeled: boolean
  }
  hazards: {
    count: number
    max_wave_m: number | null
    max_wind_ms: number | null
    wave_threshold_m: number
    wind_threshold_ms: number
  }
  legs: WeatherLeg[]
  sampled: WeatherSampled[]
}

export const PASSAGES: Record<string, string> = {
  suez: 'Suez Canal',
  panama: 'Panama Canal',
  malacca: 'Strait of Malacca',
  babalmandab: 'Bab-el-Mandeb',
  ormuz: 'Strait of Hormuz',
  bosporus: 'Bosporus',
  gibraltar: 'Gibraltar',
  sunda: 'Sunda Strait',
  south_africa: 'Cape of Good Hope',
  chili: 'Chilean Straits',
  bering: 'Bering Strait',
}

export function variantLabel(name: string): string {
  if (name === 'fastest') return 'Fastest route'
  if (name === 'custom') return 'Via waypoints'
  if (name.startsWith('avoiding_')) {
    const p = name.slice('avoiding_'.length)
    return 'Avoiding ' + (PASSAGES[p] ?? p)
  }
  return name
}
