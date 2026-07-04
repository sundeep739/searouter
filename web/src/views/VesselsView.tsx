import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../state/auth'
import { Btn, Card, ErrorNote, Field, SectionTitle } from '../components/ui'

/** Vessel spec matches backend/weather/fleet.py vessel_to_dict, so Phase 2
 *  weather-aware planning can consume saved vessels unchanged. */
interface VesselSpec {
  name: string
  lpp_m: number
  beam_m: number
  windage_area_m2: number
  displacement_laden_t: number
  displacement_ballast_t: number
  curve_laden:
    | { coefficient: number; exponent: number }
    | { speeds_kn: number[]; powers_kw: number[] }
  curve_ballast: null
  engine: { name: string; mcr_kw: number; sfoc_loads: number[]; sfoc_g_per_kwh: number[] } | null
}

interface VesselRow {
  id: string
  name: string
  spec: VesselSpec
}

const PRESETS: { name: string; note: string }[] = [
  { name: 'Handysize bulker', note: '60 m Lpp · sea-trial power table' },
  { name: 'Panamax container ship', note: '290 m Lpp · 55 MW at 26 kn' },
]

const GENERIC_SFOC = { loads: [25, 50, 75, 85, 100], sfoc: [196, 181, 175, 174, 178] }

interface FormState {
  name: string
  lpp: string
  beam: string
  windage: string
  dispLaden: string
  dispBallast: string
  curveMode: 'reference' | 'table'
  refSpeed: string
  refPower: string
  exponent: string
  tableSpeeds: string
  tablePowers: string
  engineName: string
  mcr: string
}

const EMPTY_FORM: FormState = {
  name: '',
  lpp: '',
  beam: '',
  windage: '',
  dispLaden: '',
  dispBallast: '',
  curveMode: 'reference',
  refSpeed: '14',
  refPower: '6000',
  exponent: '3.0',
  tableSpeeds: '',
  tablePowers: '',
  engineName: '',
  mcr: '',
}

export default function VesselsView() {
  const { session } = useAuth()
  const [vessels, setVessels] = useState<VesselRow[]>([])
  const [editing, setEditing] = useState<string | 'new' | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [error, setError] = useState('')

  const refresh = () => {
    supabase
      .from('vessels')
      .select('id,name,spec')
      .order('updated_at', { ascending: false })
      .then(({ data }) => setVessels((data as VesselRow[]) ?? []))
  }
  useEffect(() => {
    if (session) refresh()
  }, [session])

  const set = (k: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  const parseList = (s: string) => s.split(/[,\s]+/).filter(Boolean).map(Number)

  const save = async () => {
    setError('')
    try {
      let curve: VesselSpec['curve_laden']
      if (form.curveMode === 'reference') {
        // cubic-law: P = c * v^exp fitted through the reference point
        curve = {
          coefficient: Number(form.refPower) / Math.pow(Number(form.refSpeed), Number(form.exponent)),
          exponent: Number(form.exponent),
        }
      } else {
        const speeds_kn = parseList(form.tableSpeeds)
        const powers_kw = parseList(form.tablePowers)
        if (speeds_kn.length < 2 || speeds_kn.length !== powers_kw.length) {
          throw new Error('Power table needs matching speed and power lists (2+ points)')
        }
        curve = { speeds_kn, powers_kw }
      }
      const spec: VesselSpec = {
        name: form.name,
        lpp_m: Number(form.lpp),
        beam_m: Number(form.beam),
        windage_area_m2: Number(form.windage),
        displacement_laden_t: Number(form.dispLaden),
        displacement_ballast_t: Number(form.dispBallast),
        curve_laden: curve,
        curve_ballast: null,
        engine: form.mcr
          ? {
              name: form.engineName || 'Main engine',
              mcr_kw: Number(form.mcr),
              sfoc_loads: GENERIC_SFOC.loads,
              sfoc_g_per_kwh: GENERIC_SFOC.sfoc,
            }
          : null,
      }
      if (!spec.name || !spec.lpp_m || !spec.beam_m) throw new Error('Name, Lpp and beam are required')
      if (editing === 'new') {
        await supabase.from('vessels').insert({
          owner_id: session!.user.id,
          name: spec.name,
          spec,
        })
      } else {
        await supabase
          .from('vessels')
          .update({ name: spec.name, spec, updated_at: new Date().toISOString() })
          .eq('id', editing)
      }
      setEditing(null)
      setForm(EMPTY_FORM)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const startEdit = (v: VesselRow) => {
    const s = v.spec
    setForm({
      name: s.name,
      lpp: String(s.lpp_m),
      beam: String(s.beam_m),
      windage: String(s.windage_area_m2),
      dispLaden: String(s.displacement_laden_t),
      dispBallast: String(s.displacement_ballast_t),
      curveMode: 'coefficient' in s.curve_laden ? 'reference' : 'table',
      refSpeed: '14',
      refPower:
        'coefficient' in s.curve_laden
          ? String(Math.round(s.curve_laden.coefficient * Math.pow(14, s.curve_laden.exponent)))
          : '6000',
      exponent: 'coefficient' in s.curve_laden ? String(s.curve_laden.exponent) : '3.0',
      tableSpeeds: 'speeds_kn' in s.curve_laden ? s.curve_laden.speeds_kn.join(', ') : '',
      tablePowers: 'powers_kw' in s.curve_laden ? s.curve_laden.powers_kw.join(', ') : '',
      engineName: s.engine?.name ?? '',
      mcr: s.engine ? String(s.engine.mcr_kw) : '',
    })
    setEditing(v.id)
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <SectionTitle>My fleet</SectionTitle>
        <Btn
          variant="ghost"
          className="!px-3 !py-1.5 text-xs"
          onClick={() => {
            setForm(EMPTY_FORM)
            setEditing('new')
          }}
        >
          + Add vessel
        </Btn>
      </div>

      {vessels.length === 0 && editing === null && (
        <Card className="text-sm text-slate-500 dark:text-slate-400">
          No vessels yet. Add your ship's particulars once — speed-power curve, engine — and
          weather-aware voyage planning (coming next) will use them automatically.
        </Card>
      )}

      {vessels.map((v) => (
        <Card key={v.id} className="!p-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{v.name}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Lpp {v.spec.lpp_m} m · beam {v.spec.beam_m} m
                {v.spec.engine && <> · {v.spec.engine.mcr_kw.toLocaleString()} kW MCR</>}
              </p>
            </div>
            <div className="flex gap-2 text-xs">
              <button className="text-sea-600 hover:underline dark:text-sea-400" onClick={() => startEdit(v)}>
                Edit
              </button>
              <button
                className="text-slate-400 hover:text-rose-500"
                onClick={async () => {
                  await supabase.from('vessels').delete().eq('id', v.id)
                  refresh()
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </Card>
      ))}

      {editing !== null && (
        <Card className="space-y-3">
          <SectionTitle>{editing === 'new' ? 'New vessel' : 'Edit vessel'}</SectionTitle>
          <Field label="Name" value={form.name} onChange={set('name')} placeholder="MV Example" />
          <div className="grid grid-cols-3 gap-2">
            <Field label="Lpp (m)" type="number" value={form.lpp} onChange={set('lpp')} />
            <Field label="Beam (m)" type="number" value={form.beam} onChange={set('beam')} />
            <Field label="Windage (m²)" type="number" value={form.windage} onChange={set('windage')} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Displacement laden (t)" type="number" value={form.dispLaden} onChange={set('dispLaden')} />
            <Field label="Displacement ballast (t)" type="number" value={form.dispBallast} onChange={set('dispBallast')} />
          </div>

          <div className="flex gap-1 rounded-xl bg-slate-200/70 p-1 text-xs dark:bg-navy-800">
            {(['reference', 'table'] as const).map((m) => (
              <button
                key={m}
                className={`flex-1 rounded-lg py-1.5 font-medium ${
                  form.curveMode === m
                    ? 'bg-white text-slate-800 shadow-sm dark:bg-navy-950 dark:text-slate-100'
                    : 'text-slate-500 dark:text-slate-400'
                }`}
                onClick={() => setForm((f) => ({ ...f, curveMode: m }))}
              >
                {m === 'reference' ? 'Reference point' : 'Power table'}
              </button>
            ))}
          </div>
          {form.curveMode === 'reference' ? (
            <div className="grid grid-cols-3 gap-2">
              <Field label="Speed (kn)" type="number" value={form.refSpeed} onChange={set('refSpeed')} />
              <Field label="Power (kW)" type="number" value={form.refPower} onChange={set('refPower')} />
              <Field label="Exponent" type="number" step={0.1} value={form.exponent} onChange={set('exponent')} />
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2">
              <Field label="Speeds (kn, comma-separated)" value={form.tableSpeeds} onChange={set('tableSpeeds')} placeholder="10, 12, 14, 16" />
              <Field label="Powers (kW, comma-separated)" value={form.tablePowers} onChange={set('tablePowers')} placeholder="2100, 3600, 6000, 9500" />
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <Field label="Engine name (optional)" value={form.engineName} onChange={set('engineName')} placeholder="MAN 6S50" />
            <Field label="MCR (kW, optional)" type="number" value={form.mcr} onChange={set('mcr')} />
          </div>

          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="flex gap-2">
            <Btn className="flex-1" onClick={save}>
              Save vessel
            </Btn>
            <Btn variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Btn>
          </div>
        </Card>
      )}

      <div className="space-y-2">
        <SectionTitle>Built-in presets</SectionTitle>
        {PRESETS.map((p) => (
          <Card key={p.name} className="!p-3">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{p.name}</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">{p.note} · available in weather planning</p>
          </Card>
        ))}
      </div>
    </>
  )
}
