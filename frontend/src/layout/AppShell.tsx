/**
 * Application shell.
 *
 * Layout follows the shadcn admin-dashboard pattern: a grouped, collapsible
 * left rail, a thin top bar carrying search and account, and an unconstrained
 * content column. What it does not follow is that template's palette — the
 * rail stays the deep green of turned field and the working surface stays
 * unbleached paper, because the identity is the part of this application that
 * is actually ours.
 *
 * No max-width on the content area: this system's main surfaces are 30-column
 * grids over 100k rows, and centring them in a reading measure would waste the
 * screen they need.
 *
 * The rail groups destinations and shows build phase against the ones that do
 * not exist yet. A team working through a 52-week plan benefits from the
 * navigation telling the truth about what is built, rather than presenting
 * dead links as if they were features.
 *
 * Collapsing is a real preference rather than a flourish — a data-ops analyst
 * reading a wide grid wants those 224 pixels back, and they want them still
 * gone tomorrow morning, so the choice is persisted.
 */

import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Building2,
  ChevronLeft,
  FileText,
  IndianRupee,
  Gauge,
  LayoutGrid,
  LogOut,
  Megaphone,
  Search,
  Send,
  ShieldAlert,
  Sprout,
  Palette,
  UserRound,
} from 'lucide-react'

import { useLogout, useMe } from '../api/auth'
import { roleLabel } from '../lib/roles'
import { cn } from '../lib/cn'
import { BrandLockup, BrandMark } from '../components/Brand'
import {
  Menu,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
} from '../components/ui/menu'

/** 🔴 Development-only sign-in bypass. See the note in App.tsx. */
const NO_AUTH = import.meta.env.VITE_NO_AUTH === '1'

const RAIL_KEY = 'agricrm.rail.collapsed'

interface Destination {
  to: string
  label: string
  icon: typeof LayoutGrid
  /** Set when the destination is a placeholder, naming the phase that fills it. */
  phase?: string
}

const GROUPS: { heading: string; items: Destination[] }[] = [
  {
    heading: 'Operate',
    items: [
      { to: '/', label: 'Overview', icon: LayoutGrid },
      { to: '/invoices', label: 'Invoices', icon: FileText },
      { to: '/receivables', label: 'Receivables', icon: IndianRupee },
      { to: '/organisations', label: 'Organisations', icon: Building2, phase: 'P1' },
      { to: '/farmers', label: 'Farmers', icon: Sprout },
    ],
  },
  {
    heading: 'Commercial',
    items: [
      { to: '/pipeline', label: 'Pipeline', icon: Send, phase: 'P3' },
      { to: '/campaigns', label: 'Campaigns', icon: Megaphone, phase: 'P4' },
      { to: '/quality', label: 'Data health', icon: Gauge, phase: 'P5' },
    ],
  },
  {
    heading: 'System',
    items: [
      { to: '/design', label: 'Design system', icon: Palette },
      { to: '/account', label: 'Account', icon: UserRound },
    ],
  },
]

export function AppShell() {
  const { data: me } = useMe()
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) === '1'
    } catch {
      return false // private mode, or storage blocked. Expanded is the safe default.
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(RAIL_KEY, collapsed ? '1' : '0')
    } catch {
      /* the preference simply does not persist; the app is unaffected */
    }
  }, [collapsed])

  return (
    <div className="flex min-h-screen bg-canvas">
      <aside
        className={cn(
          'hidden shrink-0 flex-col bg-rail transition-[width] duration-150 md:flex',
          collapsed ? 'w-14' : 'w-56',
        )}
      >
        <div
          className={cn(
            'flex h-12 items-center border-b border-rail-line',
            collapsed ? 'justify-center px-2' : 'px-4',
          )}
        >
          {collapsed ? (
            <BrandMark on="dark" className="h-6 w-6" />
          ) : (
            <BrandLockup on="dark" layout="inline" size="sm" />
          )}
        </div>

        <nav className="flex-1 overflow-y-auto p-2" aria-label="Main">
          {GROUPS.map((group, index) => (
            <div key={group.heading} className={cn(index > 0 && 'mt-4')}>
              {/* The heading is hidden rather than removed when collapsed, so
                  the rail keeps its grouping for a screen reader even when the
                  grouping is no longer visible. */}
              <div
                className={cn(
                  'px-2.5 pb-1 text-2xs uppercase tracking-wide text-rail-ink-2',
                  collapsed && 'sr-only',
                )}
              >
                {group.heading}
              </div>
              {group.items.map((item) => (
                <RailLink key={item.to} collapsed={collapsed} {...item} />
              ))}
            </div>
          ))}
        </nav>

        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          className={cn(
            'flex h-10 items-center gap-2 border-t border-rail-line px-4 text-sm',
            'text-rail-ink-2 transition-colors hover:bg-rail-raised hover:text-rail-ink',
            collapsed && 'justify-center px-0',
          )}
        >
          <ChevronLeft className={cn('size-4 transition-transform', collapsed && 'rotate-180')} />
          {!collapsed && <span>Collapse</span>}
        </button>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar me={me} />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function TopBar({ me }: { me: ReturnType<typeof useMe>['data'] }) {
  const logout = useLogout()
  const navigate = useNavigate()

  return (
    <header className="sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-line bg-surface px-4">
      {/* Search is chrome, not a feature: it is disabled and says so. A box
          that looks live and silently does nothing is worse than no box —
          people type into it, get nothing, and conclude the data is missing. */}
      <div className="relative hidden min-w-0 max-w-sm flex-1 sm:block">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-3" />
        <input
          type="search"
          disabled
          placeholder="Search — Phase 5"
          aria-label="Search (not built yet)"
          className="h-8 w-full rounded-card border border-line bg-canvas pl-8 pr-3 text-base text-ink placeholder:text-ink-3 disabled:cursor-not-allowed"
        />
      </div>

      <div className="ml-auto flex items-center gap-2">
        {NO_AUTH && (
          // 🔴 Loud on every screen, not only in the rail. An instance running
          // with authentication off must never be mistaken for a real one.
          <span className="rounded-chip border border-quarantine-line bg-quarantine-soft px-1.5 py-0.5 text-2xs uppercase tracking-wide text-quarantine">
            Sign-in off · dev
          </span>
        )}

        {me && (
          <Menu>
            <MenuTrigger
              className="flex items-center gap-2 rounded-card px-1.5 py-1 text-left transition-colors hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              aria-label="Account menu"
            >
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-semibold text-brand">
                {initials(me.full_name)}
              </span>
              <span className="hidden min-w-0 leading-tight sm:block">
                <span className="block truncate text-sm font-medium text-ink">{me.full_name}</span>
                <span className="block truncate text-2xs text-ink-3">{roleLabel(me.role)}</span>
              </span>
            </MenuTrigger>

            <MenuContent>
              <MenuLabel>{me.email}</MenuLabel>
              <MenuSeparator />
              {/* 🔴 `onSelect`, not an <a> inside the item.
                  Radix closes the menu on select, which unmounts the portal
                  the anchor lives in — so the link can be removed from the DOM
                  before the router's click handler runs, and clicking Account
                  does nothing at all. Navigating from the callback is not
                  subject to that race. */}
              <MenuItem onSelect={() => navigate('/account')}>
                <UserRound />
                Account
              </MenuItem>
              {me.mfa_enforced && !me.mfa_satisfied && (
                <MenuItem onSelect={() => navigate('/mfa')}>
                  <ShieldAlert />
                  Finish MFA
                </MenuItem>
              )}
              <MenuSeparator />
              {NO_AUTH ? (
                // Signing out while the bypass is on would clear a token that
                // is not being used and land you straight back here.
                <MenuItem disabled>
                  <LogOut />
                  Sign-out disabled in dev
                </MenuItem>
              ) : (
                <MenuItem onSelect={() => logout.mutate()}>
                  <LogOut />
                  Sign out
                </MenuItem>
              )}
            </MenuContent>
          </Menu>
        )}
      </div>
    </header>
  )
}

function RailLink({ to, label, icon: Icon, phase, collapsed }: Destination & { collapsed: boolean }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        cn(
          // ring rather than border on the active state: a border would change
          // the box size and shunt the rail by a pixel as you navigate.
          'flex items-center gap-2.5 rounded-card px-2.5 py-1.5 text-base transition-colors',
          collapsed && 'justify-center px-0',
          isActive
            ? 'bg-rail-raised font-medium text-rail-ink ring-1 ring-rail-line'
            : 'text-rail-ink-2 hover:bg-rail-raised hover:text-rail-ink',
        )
      }
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed && (
        <>
          <span className="min-w-0 flex-1 truncate">{label}</span>
          {phase && <span className="text-2xs uppercase text-rail-ink-2 opacity-70">{phase}</span>}
        </>
      )}
    </NavLink>
  )
}

function initials(name: string): string {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
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
  // A node, not a string: on a detail page the eyebrow is usually the link
  // back up to its section.
  eyebrow?: React.ReactNode
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
