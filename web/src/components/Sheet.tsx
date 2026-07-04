import { useRef, useState } from 'react'
import type { ReactNode } from 'react'

/** Desktop: fixed left panel. Mobile: draggable bottom sheet over the map
 *  with three snap heights. */
export default function Sheet({ children, mapless }: { children: ReactNode; mapless?: boolean }) {
  const SNAPS = [0.28, 0.55, 0.88]
  const [snap, setSnap] = useState(1)
  const drag = useRef<{ startY: number; startSnap: number } | null>(null)
  const [dragPx, setDragPx] = useState<number | null>(null)

  const vh = window.innerHeight
  const height = dragPx ?? SNAPS[snap] * vh

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { startY: e.clientY, startSnap: snap }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return
    const delta = drag.current.startY - e.clientY
    const h = Math.min(0.92 * vh, Math.max(120, SNAPS[drag.current.startSnap] * vh + delta))
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

  if (mapless) {
    // Views without map interplay get a plain scrollable surface.
    return (
      <div className="absolute inset-0 bottom-14 z-10 overflow-y-auto bg-slate-50 p-4 pb-8 md:static md:h-full md:w-[420px] md:shrink-0 md:border-r md:border-slate-200 dark:bg-navy-950 dark:md:border-slate-800">
        <div className="mx-auto max-w-3xl space-y-4">{children}</div>
      </div>
    )
  }

  return (
    <>
      {/* Desktop side panel */}
      <div className="hidden h-full w-[420px] shrink-0 overflow-y-auto border-r border-slate-200 bg-slate-50 p-4 md:block dark:border-slate-800 dark:bg-navy-950">
        <div className="space-y-4">{children}</div>
      </div>
      {/* Mobile bottom sheet */}
      <div
        className="sheet-enter absolute inset-x-0 bottom-14 z-10 flex flex-col rounded-t-3xl border-t border-slate-200 bg-slate-50/95 backdrop-blur md:hidden dark:border-slate-700 dark:bg-navy-950/95"
        style={{ height, transition: dragPx == null ? 'height .2s ease-out' : 'none' }}
      >
        <div
          className="flex shrink-0 cursor-grab touch-none items-center justify-center py-2.5"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          <div className="h-1.5 w-10 rounded-full bg-slate-300 dark:bg-slate-600" />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6">
          <div className="space-y-4">{children}</div>
        </div>
      </div>
    </>
  )
}
