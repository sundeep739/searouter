import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase, type Profile } from '../lib/supabase'

// Invite / recovery links land with tokens in the hash; capture the intent
// before supabase-js consumes it so we can prompt for a password.
const arrivedViaInvite =
  window.location.hash.includes('type=invite') ||
  window.location.hash.includes('type=recovery') ||
  window.location.hash.includes('type=signup')

interface AuthState {
  session: Session | null
  profile: Profile | null
  loading: boolean
  needsPassword: boolean
  clearNeedsPassword: () => void
}

const Ctx = createContext<AuthState>({
  session: null,
  profile: null,
  loading: true,
  needsPassword: false,
  clearNeedsPassword: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [needsPassword, setNeedsPassword] = useState(arrivedViaInvite)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s)
      if (event === 'PASSWORD_RECOVERY') setNeedsPassword(true)
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    if (!session) {
      setProfile(null)
      return
    }
    supabase
      .from('profiles')
      .select('*')
      .eq('id', session.user.id)
      .single()
      .then(({ data }) => setProfile(data as Profile | null))
  }, [session?.user.id])

  return (
    <Ctx.Provider
      value={{
        session,
        profile,
        loading,
        needsPassword,
        clearNeedsPassword: () => setNeedsPassword(false),
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

export const useAuth = () => useContext(Ctx)
