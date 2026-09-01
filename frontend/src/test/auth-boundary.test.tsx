/**
 * Signing out, and reaching the account page.
 *
 * Both of these shipped broken, and both were found by a person clicking
 * rather than by anything here — which is why this file exists.
 *
 *   Sign out did nothing until the page was reloaded. `tokens` was a plain
 *   module object, so `RequireAuth` read it during render and clearing it
 *   changed nothing React could see. A signed-out person kept looking at data.
 *
 *   Account did nothing at all. The menu item was a router link nested inside
 *   a Radix item; Radix closes the menu on select, unmounting the portal the
 *   anchor lives in, and the link could be gone before the router's click
 *   handler ran.
 *
 * Neither is visible in a type check or a build, which is exactly the gap this
 * closes.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { tokens } from '../api/client'
import { useSession } from '../api/auth'

const ME = {
  id: 1,
  email: 'agent@agricrm.local',
  full_name: 'Anil Sharma',
  role: 'field_agent',
  district_ids: [9001, 9002],
  is_cross_territory: false,
  mfa_enforced: false,
  mfa_satisfied: true,
  permissions: [],
}

beforeEach(() => {
  tokens.clear()
  vi.restoreAllMocks()
})

afterEach(() => {
  tokens.clear()
})

/** A fetch that answers the handful of calls these screens make. */
function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/auth/me/')) {
        return new Response(JSON.stringify(ME), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/auth/logout/')) {
        return new Response(null, { status: 205 })
      }
      return new Response(JSON.stringify({ results: [], next: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
}

function withProviders(ui: React.ReactNode, initial = '/') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// The session is reactive state
// ---------------------------------------------------------------------------

function SessionProbe() {
  return <span data-testid="session">{useSession() ? 'in' : 'out'}</span>
}

describe('useSession', () => {
  it('re-renders when the tokens are cleared', async () => {
    tokens.set('access-token', 'refresh-token')
    withProviders(<SessionProbe />)

    expect(screen.getByTestId('session')).toHaveTextContent('in')

    // 🔴 The regression. Before the store took subscribers, this changed the
    // module variable and nothing on screen; the user stayed signed in until
    // they reloaded.
    tokens.clear()

    await waitFor(() => expect(screen.getByTestId('session')).toHaveTextContent('out'))
  })

  it('re-renders when a session begins', async () => {
    withProviders(<SessionProbe />)
    expect(screen.getByTestId('session')).toHaveTextContent('out')

    tokens.set('access-token', 'refresh-token')

    await waitFor(() => expect(screen.getByTestId('session')).toHaveTextContent('in'))
  })

  it('reports a session held only by a refresh token', () => {
    // The access token lives in memory and is gone after a reload; the refresh
    // token in localStorage is what makes the session survive one.
    localStorage.setItem('agricrm.refresh', 'refresh-token')
    withProviders(<SessionProbe />)

    expect(screen.getByTestId('session')).toHaveTextContent('in')
  })
})

// ---------------------------------------------------------------------------
// Signing out, through the real shell
// ---------------------------------------------------------------------------

describe('the account menu', () => {
  it('signs out immediately, without a reload', async () => {
    stubApi()
    tokens.set('access-token', 'refresh-token')

    const { AppShell } = await import('../layout/AppShell')
    const user = userEvent.setup()

    withProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<div>protected content</div>} />
        </Route>
      </Routes>,
    )

    await screen.findByRole('button', { name: /account menu/i })
    await user.click(screen.getByRole('button', { name: /account menu/i }))

    await user.click(await screen.findByRole('menuitem', { name: /sign out/i }))

    // The tokens are gone the moment the mutation settles — which is what
    // `RequireAuth` subscribes to.
    await waitFor(() => expect(tokens.hasSession()).toBe(false))
  })

  it('navigates to the account page when Account is chosen', async () => {
    stubApi()
    tokens.set('access-token', 'refresh-token')

    const { AppShell } = await import('../layout/AppShell')
    const user = userEvent.setup()

    withProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<div>dashboard</div>} />
          <Route path="account" element={<div>account page</div>} />
        </Route>
      </Routes>,
    )

    await user.click(await screen.findByRole('button', { name: /account menu/i }))
    // By role, not by text: the rail carries an 'Account' link of its own,
    // and a bare text match finds both.
    await user.click(await screen.findByRole('menuitem', { name: /account/i }))

    // 🔴 The regression. With the link nested inside the Radix item, this
    // stayed on the dashboard and the click appeared to do nothing.
    await waitFor(() => expect(screen.getByText('account page')).toBeInTheDocument())
  })
})

// ---------------------------------------------------------------------------
// The account page itself
// ---------------------------------------------------------------------------

describe('the account page', () => {
  it('renders the signed-in user, their role and their territory', async () => {
    stubApi()
    tokens.set('access-token', 'refresh-token')

    const { AccountPage } = await import('../pages/Account')
    withProviders(<AccountPage />)

    expect(await screen.findByText('Anil Sharma')).toBeInTheDocument()
    expect(screen.getByText('agent@agricrm.local')).toBeInTheDocument()
    expect(screen.getByText('Field Agent')).toBeInTheDocument()
    expect(screen.getByText('9001')).toBeInTheDocument()
  })
})
