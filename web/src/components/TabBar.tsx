export type Tab = 'route' | 'voyage' | 'matrix' | 'verify' | 'vessels' | 'admin'

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'route', label: 'Route', icon: '⇄' },
  { id: 'voyage', label: 'Voyage', icon: '⚓' },
  { id: 'matrix', label: 'Matrix', icon: '▦' },
  { id: 'verify', label: 'Verify', icon: '✓' },
  { id: 'vessels', label: 'Vessels', icon: '⛴' },
]

export default function TabBar({
  tab,
  setTab,
  isAdmin,
}: {
  tab: Tab
  setTab: (t: Tab) => void
  isAdmin: boolean
}) {
  const tabs = isAdmin ? [...TABS, { id: 'admin' as Tab, label: 'Admin', icon: '⚙' }] : TABS
  return (
    <nav className="absolute inset-x-0 bottom-0 z-20 flex h-14 items-stretch border-t border-slate-200 bg-white/95 backdrop-blur md:inset-y-0 md:right-auto md:h-full md:w-16 md:flex-col md:justify-start md:gap-1 md:border-r md:border-t-0 md:py-3 dark:border-slate-800 dark:bg-navy-950/95">
      <div className="hidden pb-4 text-center md:block" title="SeaRouter">
        <span className="text-xl">🌊</span>
      </div>
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => setTab(t.id)}
          className={`flex flex-1 flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition md:flex-none md:rounded-xl md:py-2 ${
            tab === t.id
              ? 'text-sea-600 dark:text-sea-400'
              : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
          }`}
          aria-current={tab === t.id ? 'page' : undefined}
        >
          <span className="text-lg leading-none">{t.icon}</span>
          {t.label}
        </button>
      ))}
    </nav>
  )
}
