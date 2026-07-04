import { createContext, useContext, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useIsMobile } from '../lib/useMedia'

const SheetCtx = createContext<{ raise: () => void; collapse: () => void } | null>(null)
/** Lets sheet content nudge the sheet open/closed (mobile only; null on desktop). */
export const useSheet = () => useContext(SheetCtx)

/** Desktop: fixed left panel. Mobile: draggable bottom sheet over the map,
 *  defaulting to a low "peek" so the map stays the hero. Renders children once
 *  (switches on viewport) so views don't double-mount. */
export default function Sheet({ children, mapless }: { children: ReactNode; mapless?: boolean }) {
  const isMobile = useIsMobile()
  const SNAPS = [0.26, 0.6, 0.92]
  const [snap, setSnap] = useState(0)
  const drag = useRef<{ startY: number; startSnap: number } | null>(null)
  const didDrag = useRef(false)
  const [dragPx, setDragPx] = useState<number | null>(null)

  const ctx = {
    raise: () => setSnap((s) => Math.max(s, 1)),
    collapse: () => setSnap(0),
  }

  if (mapless) {
    return (
      <div className="absolute inset-0 bottom-14 z-10 overflow-y-auto bg-slate-50 p-4 pb-8 md:static md:h-full md:w-[420px] md:shrink-0 md:border-r md:border-slate-200 dark:bg-navy-950 dark:md:border-slate-800">
        <div className="mx-auto max-w-3xl space-y-4">{children}</div>
      </div>
    )
  }

  if (!isMobile) {
    return (
      <div className="h-full w-[420px] shrink-0 overflow-y-auto border-r border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-navy-950">
        <div className="space-y-4">{children}</div>
      </div>
    )
  }

  const vh = window.innerHeight
  const height = dragPx ?? SNAPS[snap] * vh

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { startY: e.clientY, startSnap: snap }
    didDrag.current = false
    try {
      ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    } catch {
      /* synthetic events have no pointer to capture */
    }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return
    const delta = drag.current.startY - e.clientY
    if (Math.abs(delta) > 4) didDrag.current = true
    const h = Math.min(0.94 * vh, Math.max(110, SNAPS[drag.current.startSnap] * vh + delta))
    setDragPx(h)
  }
  const onPointerUp = () => {
    if (dragPx != null) {
      const frac = dragPx / vh
      let best = 0
      SNAPS.forEach((s, i) => {
        if (Math.abs(s - frac) < Math.abs(SNAPS[best] - frac)) best = i
      })
      setSnap(best)
    }
    setDragPx(null)
    drag.current = null
  }
  // A tap (mouse or touch fires click too) cycles peek → mid → full, so the
  // map can be minimised to read details and restored with another tap.
  const onHandleClick = () => {
    if (didDrag.current) {
      didDrag.current = false
      return
    }
    setSnap((s) => (s + 1) % SNAPS.length)
  }

  return (
    <SheetCtx.Provider value={ctx}>
      <div
        className="absolute inset-x-0 bottom-14 z-10 flex flex-col rounded-t-3xl border-t border-slate-200 bg-slate-50/97 backdrop-blur dark:border-slate-700 dark:bg-navy-950/97"
        style={{
          height,
          transition: dragPx == null ? 'height .22s cubic-bezier(.32,.72,0,1)' : 'none',
          boxShadow: '0 -6px 24px rgba(0,0,0,0.12)',
        }}
      >
        <div
          className="flex shrink-0 cursor-grab touch-none flex-col items-center justify-center gap-1 py-2.5"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onClick={onHandleClick}
        >
          <div className="h-1.5 w-10 rounded-full bg-slate-300 dark:bg-slate-600" />
          <span className="text-[10px] leading-none text-slate-400">
            {snap === SNAPS.length - 1 ? 'tap to shrink' : 'tap to expand'}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6">
          <div className="space-y-4">{children}</div>
        </div>
      </div>
    </SheetCtx.Provider>
  )
}
