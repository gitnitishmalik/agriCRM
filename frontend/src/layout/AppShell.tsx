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
 *
 * The rail is the one dark region in the application, and it is the deep
 * green of turned field. It carries the whole agricultural identity in a band
 * 224px wide, which leaves the working surface — the grids people read for
 * whole shifts — as unbleached paper with no colour on it but data.
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
      <aside className="hidden w-56 shrink-0 flex-col bg-rail md:flex">
        <div className="flex items-center gap-2.5 border-b border-rail-line px-4 py-3.5">
          <RailMark />
          <div className="min-w-0">
            <div className="text-lg font-semibold tracking-tight text-rail-ink">AgriCRM</div>
            <div className="text-2xs uppercase tracking-wide text-rail-ink-2">Theta Analytics</div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto p-2" aria-label="Main">
          {DESTINATIONS.map((d) => (
            <RailLink key={d.to} {...d} />
          ))}

          <div className="mt-4 border-t border-rail-line pt-3">
            {SYSTEM.map((d) => (
              <RailLink key={d.to} {...d} />
            ))}
          </div>
        </nav>

        {me && (
          <div className="border-t border-rail-line p-3">
            <div className="truncate text-sm text-rail-ink">{me.full_name}</div>
            <div className="truncate text-xs text-rail-ink-2">{roleLabel(me.role)}</div>
            <button
              type="button"
              onClick={() => logout.mutate()}
              className="mt-2 text-xs text-rail-ink-2 underline decoration-rail-line underline-offset-2 hover:text-rail-ink"
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
        // ring rather than border on the active state: a border would change
        // the box size and shunt the rail by a pixel as you navigate.
        `flex items-center justify-between rounded-card px-2.5 py-1.5 text-base transition-colors ${
          isActive
            ? 'bg-rail-raised font-medium text-rail-ink ring-1 ring-rail-line'
            : 'text-rail-ink-2 hover:bg-rail-raised hover:text-rail-ink'
        }`
      }
    >
      <span>{label}</span>
      {phase && <span className="text-2xs uppercase text-rail-ink-2 opacity-70">{phase}</span>}
    </NavLink>
  )
}

/**
 * The mark: the decay curve, the same function the freshness meter draws and
 * the favicon carries. Three scales, one shape — it is the only motif this
 * app repeats, and repeating it is how a curve becomes a logo.
 */
function RailMark() {
  return (
    <svg aria-hidden viewBox="0 0 32 32" className="h-7 w-7 shrink-0">
      {/* The tile is brand green, not rail green: on the rail's own colour
          the mark lost its edges and read as a scratch rather than a logo. */}
      <rect width="32" height="32" rx="6" className="fill-brand" />
      <path
        d="M5 7 C 12 7, 15 16, 18 21 C 21 25, 24 25.6, 27 25.8"
        fill="none"
        strokeWidth="2.75"
        strokeLinecap="round"
        className="stroke-rail-ink-2"
      />
      <circle cx="18" cy="21" r="2.75" className="fill-rail-ink" />
    </svg>
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
