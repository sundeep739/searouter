// Small geometry helpers — no turf dependency needed.

export type Pt = [number, number] // [lon, lat]

/** Shift successive longitudes so a route never jumps across the
 *  antimeridian (MapLibre renders longitudes beyond ±180 correctly). */
export function unwrap(coords: Pt[]): Pt[] {
  if (coords.length === 0) return coords
  const out: Pt[] = [coords[0]]
  for (let i = 1; i < coords.length; i++) {
    let lon = coords[i][0]
    const prev = out[i - 1][0]
    while (lon - prev > 180) lon -= 360
    while (lon - prev < -180) lon += 360
    out.push([lon, coords[i][1]])
  }
  return out
}

function pointInPolygon(p: Pt, poly: Pt[]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]
    const [xj, yj] = poly[j]
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) {
      inside = !inside
    }
  }
  return inside
}

function segmentsIntersect(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const cross = (o: Pt, p: Pt, q: Pt) =>
    (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
  const d1 = cross(c, d, a)
  const d2 = cross(c, d, b)
  const d3 = cross(a, b, c)
  const d4 = cross(a, b, d)
  return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0))
}

/** True if any route polyline enters or crosses any polygon. */
export function routeCrossesPolygons(lines: Pt[][], polygons: Pt[][]): boolean {
  for (const poly of polygons) {
    for (const line of lines) {
      for (const p of line) if (pointInPolygon(p, poly)) return true
      for (let i = 1; i < line.length; i++) {
        for (let j = 0, k = poly.length - 1; j < poly.length; k = j++) {
          if (segmentsIntersect(line[i - 1], line[i], poly[j], poly[k])) return true
        }
      }
    }
  }
  return false
}
