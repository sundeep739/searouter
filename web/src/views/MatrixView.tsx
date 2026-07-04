import { useState } from 'react'
import { api, type Loc, type MatrixResp } from '../lib/api'
import { downloadCsv } from '../lib/csv'
import { fmtTransit } from '../lib/format'
import PortSearch from '../components/PortSearch'
import { Btn, ErrorNote, Field, SectionTitle, Spinner } from '../components/ui'

function PortChips({
  title,
  ports,
  setPorts,
}: {
  title: string
  ports: Loc[]
  setPorts: (fn: (p: Loc[]) => Loc[]) => void
}) {
  const [adding, setAdding] = useState<Loc | null>(null)
  return (
    <div className="space-y-2">
      <SectionTitle>{title}</SectionTitle>
      {ports.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {ports.map((p, i) => (
            <span
              key={`${p.unlocode}-${i}`}
              className="inline-flex items-center gap-1.5 rounded-full bg-slate-200/80 py-1 pl-3 pr-1.5 text-xs text-slate-700 dark:bg-navy-800 dark:text-slate-200"
            >
              {p.name} ({p.unlocode})
              <button
                className="rounded-full px-1 text-slate-400 hover:text-rose-500"
                onClick={() => setPorts((ps) => ps.filter((_, j) => j !== i))}
                aria-label={`Remove ${p.name}`}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}
      <PortSearch
        value={adding}
        onSelect={(l) => {
          if (l) setPorts((ps) => [...ps, l])
          setAdding(null)
        }}
        placeholder="Add port…"
      />
    </div>
  )
}

export default function MatrixView() {
  const [origins, setOrigins] = useState<Loc[]>([])
  const [dests, setDests] = useState<Loc[]>([])
  const [speed, setSpeed] = useState(24)
  const [result, setResult] = useState<MatrixResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async () => {
    setBusy(true)
    setError('')
    try {
      setResult(
        await api<MatrixResp>('/matrix', {
          origins: origins.map((p) => p.unlocode),
          dests: dests.map((p) => p.unlocode),
          speed,
        }),
      )
    } catch (e) {
      setResult(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PortChips title="Origins" ports={origins} setPorts={setOrigins} />
      <PortChips title="Destinations" ports={dests} setPorts={setDests} />
      <div className="flex items-end gap-2">
        <Field
          label="Speed (kn)"
          type="number"
          min={1}
          max={60}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="w-28"
        />
        <Btn className="flex-1" disabled={origins.length === 0 || dests.length === 0 || busy} onClick={run}>
          {busy ? <Spinner className="border-white" /> : 'Compute matrix'}
        </Btn>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}

      {result && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <SectionTitle>Distances (nm)</SectionTitle>
            <button
              className="text-xs text-sea-600 underline-offset-2 hover:underline dark:text-sea-400"
              onClick={() =>
                downloadCsv(
                  'distance_matrix.csv',
                  result.origins.map((o, i) => ({
                    origin: o.unlocode,
                    ...Object.fromEntries(
                      result.dests.map((d, j) => [d.unlocode, result.cells[i][j]?.distance_nm ?? 'no route']),
                    ),
                  })),
                )
              }
            >
              Export CSV
            </button>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-700">
            <table className="w-full min-w-max text-sm">
              <thead>
                <tr className="bg-slate-100 text-left text-xs text-slate-500 dark:bg-navy-800 dark:text-slate-400">
                  <th className="px-3 py-2 font-medium">from \ to</th>
                  {result.dests.map((d) => (
                    <th key={d.unlocode} className="px-3 py-2 font-mono font-medium" title={d.name}>
                      {d.unlocode}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.origins.map((o, i) => (
                  <tr key={o.unlocode} className="border-t border-slate-200 bg-white dark:border-slate-700 dark:bg-navy-900">
                    <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400" title={o.name}>
                      {o.unlocode}
                    </td>
                    {result.dests.map((d, j) => {
                      const c = result.cells[i][j]
                      return (
                        <td key={d.unlocode} className="px-3 py-2">
                          {c ? (
                            <>
                              <span className="font-medium text-slate-800 dark:text-slate-100">
                                {c.distance_nm.toLocaleString()}
                              </span>
                              <span className="ml-1 text-xs text-slate-400">{fmtTransit(c.hours)}</span>
                            </>
                          ) : (
                            <span className="text-xs text-rose-400">no route</span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
