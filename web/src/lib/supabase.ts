import { createClient } from '@supabase/supabase-js'

// The Supabase URL and publishable anon key are safe to ship in the client —
// they identify the project publicly and all data access is gated by RLS.
// Env vars override these defaults when set (e.g. for a different project).
const SUPABASE_URL =
  import.meta.env.VITE_SUPABASE_URL ?? 'https://hlqqqctwodxpmarmzvdl.supabase.co'
const SUPABASE_ANON_KEY =
  import.meta.env.VITE_SUPABASE_ANON_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhscXFxY3R3b2R4cG1hcm16dmRsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMxNjgwNzksImV4cCI6MjA5ODc0NDA3OX0.SGaggef328lZdzbvXDAZ8hHQstVZjAP95d-s6vXaJAU'

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

export type Role = 'admin' | 'member'

export interface Profile {
  id: string
  email: string
  role: Role
  created_at: string
}
