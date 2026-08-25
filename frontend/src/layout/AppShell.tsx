/**
 * Application shell.
 *
 * A left rail and a thin top bar. No max-width on the content area: this
 * system's main surfaces are 30-column grids over 100k rows, and centring
 * them in a reading measure would waste the screen they need.
 *
 * The rail shows build phase against each destination. A team working through
 * a 52-week plan benefits from the navigation telling the truth about what is
 * built, rather than presenting dead links as if they were features.
 */

import { NavLink, Outlet } from 'react-router-dom'
import { useLogout, useMe } from '../api/auth'

interface Destination {
  to: string
  label: string
  phase?: string
}

const DESTINATIONS: Destination[] = [
  { to: '/', label: 'Overview' },
  { to: '/organisations', label: 'Organisations', phase: 'Phase 1' },
  { to: '/farmers', label: 'Farmers', phase: 'Phase 2' },
  { to: '/pipeline', label: 'Pipeline', phase: 'Phase 3' },
  { to: '/campaigns', label: 'Campaigns', phase: 'Phase 4' },
  { to: '/quality', label: 'Data health', phase: 'Phase 5' },
]

const SYSTEM: Destination[] = [
  { to: '/design', label: 'Design system' },
  { to: '/account', label: 'Account' },
]

export function AppShell() {
  const { data: me } = useMe()
  const logout = useLogout()

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-surface md:flex">
        <div className="border-b border-line px-4 py-3.5">
          <div className="text-lg font-semibold tracking-tight text-ink">AgriCRM</div>
          <div className="text-2xs uppercase tracking-wide text-ink-3">Theta Analytics</div>
        </div>

        <nav className="flex-1 overflow-y-auto p-2" aria-label="Main">
          {DESTINATIONS.map((d) => (
            <RailLink key={d.to} {...d} />
          ))}

          <div className="mt-4 border-t border-line pt-3">
            {SYSTEM.map((d) => (
              <RailLink key={d.to} {...d} />
            ))}
          </div>
        </nav>

        {me && (
          <div className="border-t border-line p-3">
            <div className="truncate text-sm text-ink">{me.full_name}</div>
            <div className="truncate text-xs text-ink-3">{roleLabel(me.role)}</div>
            <button
              type="button"
              onClick={() => logout.mutate()}
              className="mt-2 text-xs text-ink-3 underline decoration-line underline-offset-2 hover:text-ink"
            >
              Sign out
            </button>
          </div>
        )}
      </aside>

      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}

function RailLink({ to, label, phase }: Destination) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `flex items-center justify-between rounded-card px-2.5 py-1.5 text-base transition-colors ${
          isActive ? 'bg-sunken font-medium text-ink' : 'text-ink-2 hover:bg-sunken hover:text-ink'
        }`
      }
    >
      <span>{label}</span>
      {phase && <span className="text-2xs uppercase text-ink-3">{phase}</span>}
    </NavLink>
  )
}

export function roleLabel(role: string): string {
  return role
    .split('_')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

/**
 * Page header. Takes an eyebrow because most surfaces here are one level down
 * from a section — "Organisations / Bhainswal Kisan Producer Company Limited".
 */
export function PageHeader({
  eyebrow,
  title,
  titleLocal,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  titleLocal?: string
  description?: string
  actions?: React.ReactNode
}) {
  return (
    <header className="border-b border-line bg-surface px-6 py-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow && <div className="label mb-1">{eyebrow}</div>}
          <h1 className="text-2xl font-semibold text-ink">{title}</h1>
          {titleLocal && (
            <div lang="hi" className="mt-0.5 text-lg text-ink-2">
              {titleLocal}
            </div>
          )}
          {description && <p className="mt-1.5 max-w-2xl text-base text-ink-2">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}
