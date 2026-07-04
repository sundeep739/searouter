import { useEffect, useState } from 'react'
import { api, type BatchResult, type Loc, type VerifyResp } from '../lib/api'
import { downloadCsv, parseCsvFile } from '../lib/csv'
import { fmtNm } from '../lib/format'
import PortSearch from '../components/PortSearch'
import { Btn, Card, ErrorNote, Field, SectionTitle, Spinner } from '../components/ui'
import { supabase } from '../lib/supabase'
import { useAuth } from '../state/auth'

function VerdictBadge({ verdict }: { verdict: string }) {
  const style =
    verdict === 'confirmed'
      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300'
      : verdict === 'mismatch'
        ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/50 dark:text-rose-300'
        : 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${style}`}>
      {verdict}
    </span>
  )
}

interface HistoryRow {
  id: string
  origin: string
  dest: string
  reported_nm: number
  verdict: string
  created_at: string
}

export default function VerifyView() {
  const { session } = useAuth()
  const [origin, setOrigin] = useState<Loc | null>(null)
  const [dest, setDest] = useState<Loc | null>(null)
  const [reported, setReported] = useState('')
  const [tolerance, setTolerance] = useState(5)
  const [result, setResult] = useState<VerifyResp | null>(null)
  const [batch, setBatch] = useState<BatchResult[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [history, setHistory] = useState<HistoryRow[]>([])

  const loadHistory = () => {
    supabase
      .from('verifications')
      .select('id,origin,dest,reported_nm,verdict,created_at')
      .order('created_at', { ascending: false })
      .limit(15)
      .then(({ data }) => setHistory((data as HistoryRow[]) ?? []))
  }
  useEffect(() => {
    if (session) loadHistory()
  }, [session])

  const run = async () => {
    if (!origin || !dest) return
    setBusy(true)
    setError('')
    setBatch(null)
    try {
      const r = await api<VerifyResp>('/verify', {
        origin: origin.unlocode,
        dest: dest.unlocode,
        reported_nm: Number(reported),
        tolerance_pct: tolerance,
      })
      setResult(r)
      if (session) {
        await supabase.from('verifications').insert({
          owner_id: session.user.id,
          origin: r.origin.unlocode,
          dest: r.dest.unlocode,
          reported_nm: r.reported_nm,
          tolerance_pct: r.tolerance_pct,
          verdict: r.verdict,
          detail: r.best_match,
        })
        loadHistory()
      }
    } catch (e) {
      setResult(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const runBatch = async (file: File) => {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const rows = await parseCsvFile(file)
      const payload = rows
        .map((r) => ({
          origin: r.origin ?? '',
          dest: r.dest ?? '',
          reported_nm: Number(r.reported_nm ?? r.reported_km ? (r.reported_nm ? r.reported_nm : Number(r.reported_km) / 1.852) : NaN),
        }))
        .filter((r) => r.origin && r.dest && !Number.isNaN(r.reported_nm))
      if (payload.length === 0) throw new Error('CSV needs columns: origin, dest, reported_nm')
      const res = await api<{ results: BatchResult[] }>('/verify/batch', {
        rows: payload,
        tolerance_pct: tolerance,
      })
      setBatch(res.results)
    } catch (e) {
      setBatch(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="space-y-3">
        <SectionTitle>Verify a reported distance</SectionTitle>
        <PortSearch label="From" value={origin} onSelect={setOrigin} />
        <PortSearch label="To" value={dest} onSelect={setDest} />
        <div className="grid grid-cols-2 gap-2">
          <Field
            label="Reported distance (nm)"
            type="number"
            min={1}
            value={reported}
            onChange={(e) => setReported(e.target.value)}
            placeholder="10650"
          />
          <Field
            label="Tolerance (%)"
            type="number"
            min={0.5}
            max={100}
            step={0.5}
            value={tolerance}
            onChange={(e) => setTolerance(Number(e.target.value))}
          />
        </div>
        <Btn className="w-full" disabled={!origin || !dest || !reported || busy} onClick={run}>
          {busy ? <Spinner className="border-white" /> : 'Verify'}
        </Btn>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      {result && (
        <Card className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-800 dark:text-slate-100">
              {result.origin.unlocode} → {result.dest.unlocode} · {fmtNm(result.reported_nm)} reported
            </span>
            <VerdictBadge verdict={result.verdict} />
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Best match: <b>{result.best_match.name}</b> at {fmtNm(result.best_match.distance_nm)} (
            {result.best_match.deviation_pct > 0 ? '+' : ''}
            {result.best_match.deviation_pct}%)
            {result.best_match.via.length > 0 && <> via {result.best_match.via.join(', ')}</>}
          </p>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-slate-400">
                <th className="py-1 font-medium">Route</th>
                <th className="py-1 text-right font-medium">nm</th>
                <th className="py-1 text-right font-medium">Deviation</th>
              </tr>
            </thead>
            <tbody>
              {result.assessments.map((a) => (
                <tr key={a.name} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-1.5 text-slate-700 dark:text-slate-200">{a.name}</td>
                  <td className="py-1.5 text-right">{a.distance_nm.toLocaleString()}</td>
                  <td
                    className={`py-1.5 text-right font-medium ${
                      a.within_tolerance ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-500'
                    }`}
                  >
                    {a.deviation_pct > 0 ? '+' : ''}
                    {a.deviation_pct}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div className="space-y-2">
        <SectionTitle>Batch verify (CSV)</SectionTitle>
        <div className="flex gap-2">
          <label className="flex-1">
            <span className="sr-only">Upload CSV</span>
            <input
              type="file"
              accept=".csv"
              onChange={(e) => e.target.files?.[0] && runBatch(e.target.files[0])}
              className="block w-full cursor-pointer rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500 file:mr-2 file:rounded-lg file:border-0 file:bg-sea-600 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white dark:border-slate-600 dark:bg-navy-800 dark:text-slate-400"
            />
          </label>
          <Btn
            variant="ghost"
            onClick={() =>
              downloadCsv('verify_template.csv', [
                { origin: 'NLRTM', dest: 'CNSGH', reported_nm: 10650 },
                { origin: 'SGSIN', dest: 'AEJEA', reported_nm: 3580 },
              ])
            }
          >
            Template
          </Btn>
        </div>
      </div>

      {batch && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <SectionTitle>
              Results · {batch.filter((b) => b.verdict === 'confirmed').length}/{batch.length} confirmed
            </SectionTitle>
            <button
              className="text-xs text-sea-600 underline-offset-2 hover:underline dark:text-sea-400"
              onClick={() =>
                downloadCsv(
                  'verify_results.csv',
                  batch.map((b) => ({
                    origin: b.origin,
                    dest: b.dest,
                    reported_nm: b.reported_nm,
                    verdict: b.verdict,
                    best_route: b.best_match?.name ?? '',
                    route_nm: b.best_match?.distance_nm ?? '',
                    deviation_pct: b.best_match?.deviation_pct ?? '',
                    note: b.error ?? '',
                  })),
                )
              }
            >
              Export CSV
            </button>
          </div>
          <div className="overflow-hidden rounded-2xl border border-slate-200 dark:border-slate-700">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-100 text-left text-slate-500 dark:bg-navy-800 dark:text-slate-400">
                  <th className="px-3 py-2 font-medium">Voyage</th>
                  <th className="px-3 py-2 text-right font-medium">Reported</th>
                  <th className="px-3 py-2 text-right font-medium">Δ%</th>
                  <th className="px-3 py-2 text-right font-medium">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {batch.map((b, i) => (
                  <tr key={i} className="border-t border-slate-200 bg-white dark:border-slate-700 dark:bg-navy-900">
                    <td className="px-3 py-2 font-mono">{b.origin} → {b.dest}</td>
                    <td className="px-3 py-2 text-right">{b.reported_nm.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right">
                      {b.best_match ? `${b.best_match.deviation_pct > 0 ? '+' : ''}${b.best_match.deviation_pct}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right"><VerdictBadge verdict={b.verdict} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {history.length > 0 && (
        <div className="space-y-2">
          <SectionTitle>Recent checks</SectionTitle>
          <ul className="space-y-1">
            {history.map((h) => (
              <li key={h.id} className="flex items-center justify-between rounded-xl bg-white px-3 py-2 text-xs dark:bg-navy-900">
                <span className="font-mono text-slate-600 dark:text-slate-300">
                  {h.origin} → {h.dest} · {Number(h.reported_nm).toLocaleString()} nm
                </span>
                <VerdictBadge verdict={h.verdict} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
