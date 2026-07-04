import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { supabase, type Profile, type Role } from '../lib/supabase'
import { useAuth } from '../state/auth'
import { Btn, Card, ErrorNote, Field, SectionTitle, Spinner } from '../components/ui'

export default function AdminView() {
  const { profile } = useAuth()
  const [users, setUsers] = useState<Profile[] | null>(null)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('member')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  const refresh = () => {
    supabase
      .from('profiles')
      .select('*')
      .order('created_at')
      .then(({ data }) => setUsers((data as Profile[]) ?? []))
  }
  useEffect(refresh, [])

  const invite = async () => {
    setBusy(true)
    setError('')
    setNote('')
    try {
      await api('/admin/invite', { email: email.trim(), role })
      setNote(`Invitation sent to ${email.trim()}`)
      setEmail('')
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const setUserRole = async (id: string, newRole: Role) => {
    await supabase.from('profiles').update({ role: newRole }).eq('id', id)
    refresh()
  }

  return (
    <>
      <div className="space-y-3">
        <SectionTitle>Invite a user</SectionTitle>
        <Card className="space-y-3">
          <Field
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@company.com"
          />
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300">
              <input type="radio" checked={role === 'member'} onChange={() => setRole('member')} className="accent-teal-600" />
              Member
            </label>
            <label className="flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300">
              <input type="radio" checked={role === 'admin'} onChange={() => setRole('admin')} className="accent-teal-600" />
              Admin
            </label>
            <Btn className="ml-auto" disabled={!email.includes('@') || busy} onClick={invite}>
              {busy ? <Spinner className="border-white" /> : 'Send invite'}
            </Btn>
          </div>
          {note && <p className="text-xs text-emerald-600 dark:text-emerald-400">{note}</p>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </Card>
      </div>

      <div className="space-y-2">
        <SectionTitle>Users</SectionTitle>
        {!users ? (
          <Spinner />
        ) : (
          users.map((u) => (
            <Card key={u.id} className="!p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{u.email}</p>
                  <p className="text-xs text-slate-400">
                    since {new Date(u.created_at).toLocaleDateString()}
                  </p>
                </div>
                <select
                  value={u.role}
                  disabled={u.id === profile?.id}
                  onChange={(e) => setUserRole(u.id, e.target.value as Role)}
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs dark:border-slate-600 dark:bg-navy-800 dark:text-slate-200"
                >
                  <option value="member">member</option>
                  <option value="admin">admin</option>
                </select>
              </div>
            </Card>
          ))
        )}
        <p className="text-[11px] text-slate-400">
          Removing a user entirely is done from the Supabase dashboard (Authentication → Users).
        </p>
      </div>
    </>
  )
}
