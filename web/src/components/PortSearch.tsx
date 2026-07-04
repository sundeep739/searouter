import { useEffect, useRef, useState } from 'react'
import { api, type Loc } from '../lib/api'
import { Spinner } from './ui'

interface Props {
  label?: string
  value: Loc | null
  onSelect: (loc: Loc | null) => void
  placeholder?: string
}

export function portLabel(l: Loc) {
  return `${l.name}, ${l.country} (${l.unlocode})`
}

export default function PortSearch({ label, value, onSelect, placeholder }: Props) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Loc[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const seq = useRef(0)

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([])
      return
    }
    const mine = ++seq.current
    setBusy(true)
    const t = setTimeout(() => {
      api<{ results: Loc[] }>(`/search?q=${encodeURIComponent(q)}&limit=8`)
        .then((r) => {
          if (seq.current === mine) {
            setResults(r.results)
            setOpen(true)
          }
        })
        .catch(() => {})
        .finally(() => seq.current === mine && setBusy(false))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  return (
    <div ref={box} className="relative">
      {label && (
        <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
          {label}
        </span>
      )}
      <div className="relative">
        <input
          value={value ? portLabel(value) : q}
          placeholder={placeholder ?? 'Port or city…'}
          onChange={(e) => {
            // First keystroke over a selected port clears it (the field text
            // is selected on focus, so typing replaces rather than appends).
            if (value) {
              onSelect(null)
              setQ(e.target.value.replace(portLabel(value), ''))
            } else {
              setQ(e.target.value)
            }
          }}
          onFocus={(e) => {
            if (value) e.target.select()
            else if (results.length) setOpen(true)
          }}
          className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 pr-8 text-sm text-slate-900 outline-none focus:border-sea-500 focus:ring-2 focus:ring-sea-500/30 dark:border-slate-600 dark:bg-navy-800 dark:text-slate-100"
        />
        <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400">
          {busy ? <Spinner /> : value ? '✓' : '⌕'}
        </span>
      </div>
      {open && results.length > 0 && (
        <ul className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-600 dark:bg-navy-800">
          {results.map((r) => (
            <li key={r.unlocode}>
              <button
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-sea-500/10"
                onClick={() => {
                  onSelect(r)
                  setQ('')
                  setOpen(false)
                }}
              >
                <span className="text-slate-800 dark:text-slate-100">
                  {r.name}, {r.country}
                </span>
                <span className="ml-2 shrink-0 font-mono text-xs text-slate-400">
                  {r.unlocode}
                  {r.is_seaport ? ' ⚓' : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
