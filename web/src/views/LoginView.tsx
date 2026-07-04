import { useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../state/auth'
import { Btn, ErrorNote, Field, Spinner } from '../components/ui'

export default function LoginView() {
  const { session, needsPassword, clearNeedsPassword } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const doPassword = async () => {
    setBusy(true)
    setError('')
    const { error: err } = await supabase.auth.signInWithPassword({ email, password })
    if (err) setError(err.message)
    setBusy(false)
  }

  const doMagic = async () => {
    setBusy(true)
    setError('')
    setNote('')
    const { error: err } = await supabase.auth.signInWithOtp({
      email,
      options: { shouldCreateUser: false, emailRedirectTo: window.location.origin },
    })
    if (err) setError(err.message)
    else setNote('Check your inbox for a sign-in link.')
    setBusy(false)
  }

  const doSetPassword = async () => {
    setBusy(true)
    setError('')
    const { error: err } = await supabase.auth.updateUser({ password })
    if (err) setError(err.message)
    else clearNeedsPassword()
    setBusy(false)
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-navy-950 p-6">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="text-5xl">🌊</div>
          <h1 className="mt-3 text-2xl font-semibold text-white">SeaRouter</h1>
          <p className="mt-1 text-sm text-slate-400">
            Real sea-route distances, voyages and verification
          </p>
        </div>

        <div className="space-y-3 rounded-3xl border border-slate-700 bg-navy-900 p-5">
          {session && needsPassword ? (
            <>
              <p className="text-sm text-slate-300">
                Welcome! Set a password to finish activating your account.
              </p>
              <Field
                label="New password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
              <Btn className="w-full" disabled={password.length < 8 || busy} onClick={doSetPassword}>
                {busy ? <Spinner className="border-white" /> : 'Set password & continue'}
              </Btn>
              {password.length > 0 && password.length < 8 && (
                <p className="text-xs text-slate-400">At least 8 characters.</p>
              )}
            </>
          ) : (
            <>
              <Field
                label="Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                placeholder="you@company.com"
              />
              <Field
                label="Password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                onKeyDown={(e) => e.key === 'Enter' && doPassword()}
              />
              <Btn className="w-full" disabled={!email || !password || busy} onClick={doPassword}>
                {busy ? <Spinner className="border-white" /> : 'Sign in'}
              </Btn>
              <button
                className="w-full text-center text-xs text-slate-400 underline-offset-2 hover:text-slate-200 hover:underline disabled:opacity-40"
                disabled={!email || busy}
                onClick={doMagic}
              >
                Email me a sign-in link instead
              </button>
            </>
          )}
          {note && <p className="text-xs text-emerald-400">{note}</p>}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>

        <p className="text-center text-xs text-slate-500">
          Access is by invitation. Ask your administrator for an invite.
        </p>
      </div>
    </div>
  )
}
