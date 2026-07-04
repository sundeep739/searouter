import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

export function Btn({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const styles = {
    primary:
      'bg-sea-600 text-white hover:bg-sea-500 disabled:opacity-40 disabled:hover:bg-sea-600',
    ghost:
      'bg-slate-200/70 text-slate-700 hover:bg-slate-300 dark:bg-navy-800 dark:text-slate-200 dark:hover:bg-slate-700 disabled:opacity-40',
    danger: 'bg-rose-600/90 text-white hover:bg-rose-500 disabled:opacity-40',
  }[variant]
  return (
    <button
      className={`rounded-xl px-4 py-2.5 text-sm font-medium transition active:scale-[.98] ${styles} ${className}`}
      {...props}
    />
  )
}

export function Chip({
  active,
  children,
  onClick,
}: {
  active: boolean
  children: ReactNode
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full px-3 py-1.5 text-xs font-medium transition whitespace-nowrap ${
        active
          ? 'bg-rose-600 text-white'
          : 'bg-slate-200/80 text-slate-600 hover:bg-slate-300 dark:bg-navy-800 dark:text-slate-300 dark:hover:bg-slate-700'
      }`}
    >
      {children}
    </button>
  )
}

export function Field({
  label,
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  return (
    <label className={`block ${className}`}>
      {label && (
        <span className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
          {label}
        </span>
      )}
      <input
        className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none focus:border-sea-500 focus:ring-2 focus:ring-sea-500/30 dark:border-slate-600 dark:bg-navy-800 dark:text-slate-100"
        {...props}
      />
    </label>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-navy-900 ${className}`}
    >
      {children}
    </div>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      className={`inline-block size-4 animate-spin rounded-full border-2 border-slate-400 border-t-transparent ${className}`}
      role="status"
      aria-label="Loading"
    />
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <div className="rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
      {children}
    </div>
  )
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
    </h2>
  )
}
