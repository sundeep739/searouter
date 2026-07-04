import { supabase } from './supabase'

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

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
