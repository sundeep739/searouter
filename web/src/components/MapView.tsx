import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import { unwrap, type Pt } from '../lib/geometry'
import type { GeoJSON, Feature } from 'geojson'

const LIGHT = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json'
const DARK = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

export interface RouteLine {
  id: string
  coords: Pt[]
  color: string
  width: number
  dashed?: boolean
}

export interface MapMarker {
  lon: number
  lat: number
  color: string
  label?: string
}

export interface MapLayers {
  routes: RouteLine[]
  markers: MapMarker[]
  polygons: Pt[][]
  draft: Pt[]
  /** Changes to this key trigger fitBounds over all route coords. */
  fitKey?: string
}

export const EMPTY_LAYERS: MapLayers = { routes: [], markers: [], polygons: [], draft: [] }

interface Props {
  layers: MapLayers
  showSeamap: boolean
  clickMode: 'none' | 'waypoint' | 'polygon'
  onMapClick?: (lonlat: Pt) => void
}

export default function MapView({ layers, showSeamap, clickMode, onMapClick }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  const clickCb = useRef(onMapClick)
  const clickModeRef = useRef(clickMode)
  const layersRef = useRef(layers)
  const seamapRef = useRef(showSeamap)
  const lastFit = useRef<string | undefined>(undefined)
  clickCb.current = onMapClick
  clickModeRef.current = clickMode

  useEffect(() => {
    if (!el.current) return
    const dark = window.matchMedia('(prefers-color-scheme: dark)')
    const m = new maplibregl.Map({
      container: el.current,
      style: dark.matches ? DARK : LIGHT,
      center: [15, 30],
      zoom: 1.6,
      attributionControl: { compact: true },
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    m.on('click', (e) => {
      if (clickModeRef.current !== 'none') {
        clickCb.current?.([e.lngLat.lng, e.lngLat.lat])
      }
    })
    // Overlays are wiped on every setStyle — re-add after each style load.
    m.on('style.load', () => syncOverlays(m, layersRef.current, seamapRef.current))
    const onScheme = (ev: MediaQueryListEvent) => m.setStyle(ev.matches ? DARK : LIGHT)
    dark.addEventListener('change', onScheme)
    map.current = m
    return () => {
      dark.removeEventListener('change', onScheme)
      m.remove()
      map.current = null
    }
  }, [])

  useEffect(() => {
    layersRef.current = layers
    seamapRef.current = showSeamap
    const m = map.current
    if (!m) return
    const apply = () => {
      syncOverlays(m, layers, showSeamap)
      syncMarkers(m, markers.current, layers.markers)
      if (layers.fitKey && layers.fitKey !== lastFit.current) {
        lastFit.current = layers.fitKey
        const pts = layers.routes.flatMap((r) => unwrap(r.coords))
        layers.markers.forEach((mk) => pts.push([mk.lon, mk.lat]))
        if (pts.length > 1) {
          const b = pts.reduce(
            (acc, p) => acc.extend(p as [number, number]),
            new maplibregl.LngLatBounds(pts[0] as [number, number], pts[0] as [number, number]),
          )
          m.fitBounds(b, { padding: { top: 90, bottom: 60, left: 40, right: 40 }, maxZoom: 9 })
        }
      }
    }
    if (m.isStyleLoaded()) apply()
    else m.once('style.load', apply)
  }, [layers, showSeamap])

  return (
    <div
      ref={el}
      className={`h-full w-full ${clickMode !== 'none' ? 'cursor-crosshair' : ''}`}
    />
  )
}

function ensureGeojson(m: maplibregl.Map, id: string, data: GeoJSON) {
  const src = m.getSource(id) as maplibregl.GeoJSONSource | undefined
  if (src) src.setData(data)
  else m.addSource(id, { type: 'geojson', data })
}

function syncOverlays(m: maplibregl.Map, layers: MapLayers, showSeamap: boolean) {
  // OpenSeaMap nautical overlay
  if (showSeamap && !m.getSource('seamap')) {
    m.addSource('seamap', {
      type: 'raster',
      tiles: ['https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenSeaMap',
    })
    m.addLayer({ id: 'seamap', type: 'raster', source: 'seamap', paint: { 'raster-opacity': 0.9 } })
  } else if (!showSeamap && m.getLayer('seamap')) {
    m.removeLayer('seamap')
    m.removeSource('seamap')
  }

  const routeFc = (dashed: boolean): GeoJSON => ({
    type: 'FeatureCollection',
    features: layers.routes
      .filter((r) => !!r.dashed === dashed)
      .map((r) => ({
        type: 'Feature',
        properties: { color: r.color, width: r.width },
        geometry: { type: 'LineString', coordinates: unwrap(r.coords) },
      })),
  })
  ensureGeojson(m, 'routes-solid', routeFc(false))
  ensureGeojson(m, 'routes-dashed', routeFc(true))
  const paint = {
    'line-color': ['get', 'color'],
    'line-width': ['get', 'width'],
  } as never
  if (!m.getLayer('routes-dashed')) {
    m.addLayer({
      id: 'routes-dashed',
      type: 'line',
      source: 'routes-dashed',
      paint: { ...(paint as object), 'line-dasharray': [1.4, 1.8], 'line-opacity': 0.85 } as never,
      layout: { 'line-cap': 'round' },
    })
  }
  if (!m.getLayer('routes-solid')) {
    m.addLayer({
      id: 'routes-solid',
      type: 'line',
      source: 'routes-solid',
      paint: paint,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
    })
  }

  const polyFc: GeoJSON = {
    type: 'FeatureCollection',
    features: layers.polygons.map((p) => ({
      type: 'Feature',
      properties: {},
      geometry: { type: 'Polygon', coordinates: [[...p, p[0]]] },
    })),
  }
  ensureGeojson(m, 'areas', polyFc)
  if (!m.getLayer('areas-fill')) {
    m.addLayer({
      id: 'areas-fill',
      type: 'fill',
      source: 'areas',
      paint: { 'fill-color': '#e11d48', 'fill-opacity': 0.14 },
    })
    m.addLayer({
      id: 'areas-line',
      type: 'line',
      source: 'areas',
      paint: { 'line-color': '#e11d48', 'line-width': 1.5, 'line-dasharray': [2, 2] },
    })
  }

  const draftFc: GeoJSON = {
    type: 'FeatureCollection',
    features: layers.draft.length
      ? [
          {
            type: 'Feature',
            properties: {},
            geometry: { type: 'LineString', coordinates: layers.draft },
          },
          ...layers.draft.map(
            (p): Feature => ({
              type: 'Feature',
              properties: {},
              geometry: { type: 'Point', coordinates: p },
            }),
          ),
        ]
      : [],
  }
  ensureGeojson(m, 'draft', draftFc)
  if (!m.getLayer('draft-line')) {
    m.addLayer({
      id: 'draft-line',
      type: 'line',
      source: 'draft',
      paint: { 'line-color': '#f59e0b', 'line-width': 2, 'line-dasharray': [1, 1.5] },
    })
    m.addLayer({
      id: 'draft-pts',
      type: 'circle',
      source: 'draft',
      paint: { 'circle-radius': 4.5, 'circle-color': '#f59e0b', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' },
    })
  }
}

function syncMarkers(m: maplibregl.Map, pool: maplibregl.Marker[], wanted: MapMarker[]) {
  pool.forEach((mk) => mk.remove())
  pool.length = 0
  wanted.forEach((w) => {
    const mk = new maplibregl.Marker({ color: w.color, scale: 0.85 })
      .setLngLat([w.lon, w.lat])
      .addTo(m)
    if (w.label) mk.setPopup(new maplibregl.Popup({ closeButton: false }).setText(w.label))
    pool.push(mk)
  })
}
