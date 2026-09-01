/**
 * Routes and the auth boundary.
 *
 * Routes whose backend does not exist yet are present and honest about it —
 * they name the phase and what unblocks it. Hiding them would make the plan
 * invisible; faking them would make the app lie.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { useMe, useSession } from './api/auth'
import { AppShell } from './layout/AppShell'
import { LoginPage } from './pages/Login'
import { MfaPage } from './pages/Mfa'
import { AccountPage } from './pages/Account'
import { DesignSystemPage } from './pages/DesignSystem'
import { NotBuiltYet } from './pages/Overview'
import { DashboardPage } from './pages/Dashboard'
import { OrganisationsPage } from './pages/Organisations'
import { FarmersPage } from './pages/Farmers'
import { InvoicesPage } from './pages/Invoices'
import { InvoiceNewPage } from './pages/InvoiceNew'
import { InvoiceDetailPage } from './pages/InvoiceDetail'
import ReceivablesPage from './pages/Receivables'

/**
 * 🔴 Development-only: skip the login screen.
 *
 * Set `VITE_NO_AUTH=1` in `frontend/.env.development` alongside
 * `DEV_NO_AUTH=1` on the backend. It exists because the system has no data in
 * it yet and a login wall in front of an empty database helps nobody.
 *
 * A switch, not a deletion — the login and MFA screens are still routed and
 * still work, so turning it back on is deleting one file.
 *
 * 🔴 `.env.development`, never `.env`. Vite loads `.env` for every mode
 * including `npm run build`, so the flag there would compile the bypass into a
 * production bundle — measured: that build had no login page in it at all.
 * `.env.development` is dev-mode only.
 *
 * This is compile-time, so a normal production build has no bypass in it to
 * enable by accident. The backend has its own independent guard, so flipping
 * only this flag gets a UI that renders and an API that 401s everything.
 */
const NO_AUTH = import.meta.env.VITE_NO_AUTH === '1'

function RequireAuth({ children }: { children: React.ReactNode }) {
  // Subscribed, not read once. Signing out clears the tokens, which notifies
  // this hook, which redirects — immediately, rather than at the next reload.
  const hasToken = useSession()
  const { data: me, isLoading, isError } = useMe()

  if (NO_AUTH) return <>{children}</>

  if (!hasToken) return <Navigate to="/login" replace />
  if (isLoading) {
    return <div className="p-6 text-base text-ink-3">Loading…</div>
  }
  if (isError) return <Navigate to="/login" replace />

  // 🔴 A privileged session that has not cleared the second factor.
  //
  // The redirect at sign-in (Login.tsx) only covers the sign-in. Reload the
  // page, follow a bookmark, or navigate away from /mfa and you arrive here
  // holding a token the server refuses: /auth/me/ answers — it is reachable
  // before MFA on purpose — so the shell renders and then every single data
  // call comes back 403 with nothing on screen explaining why or where to go.
  //
  // Read off the server's answer rather than decoded from the token here, so
  // the browser and the API cannot disagree about what state the session is
  // in. This is a nicety, not the control: IsMFAVerified is the control, and
  // it does not care what this component decides.
  if (me && me.mfa_enforced && !me.mfa_satisfied) {
    return <Navigate to="/mfa" replace />
  }

  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      {/* With the bypass on these two redirect home rather than 404 — a stale
          bookmark or a `/login` typed out of habit should land somewhere
          useful, not on a sign-in form for auth that is not running. */}
      <Route path="/login" element={NO_AUTH ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/mfa" element={NO_AUTH ? <Navigate to="/" replace /> : <MfaPage />} />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/design" element={<DesignSystemPage />} />

        {/* Billing. Built and working — see INVOICE.md. */}
        <Route path="/invoices" element={<InvoicesPage />} />
        <Route path="/invoices/new" element={<InvoiceNewPage />} />
        <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
        <Route path="/receivables" element={<ReceivablesPage />} />

        <Route path="/organisations" element={<OrganisationsPage />} />
        <Route path="/farmers" element={<FarmersPage />} />
        <Route
          path="/pipeline"
          element={
            <NotBuiltYet
              title="Pipeline"
              phase="Phase 3"
              holds="Leads, opportunities and projects, with stage-ageing alerts and a forecast produced from the system rather than a spreadsheet."
            />
          }
        />
        <Route
          path="/campaigns"
          element={
            <NotBuiltYet
              title="Campaigns"
              phase="Phase 4"
              holds="WhatsApp and email, with the exclusion breakdown shown before every send and consent re-checked at dispatch."
              blockedBy="Meta business verification (Track P3), which is an external review and can stall on documentation."
            />
          }
        />
        <Route
          path="/quality"
          element={
            <NotBuiltYet
              title="Data health"
              phase="Phase 5"
              holds="Tier distribution over time, the source scorecard, the contradiction queue, and the satellite cross-check that turns Theta's existing analytics into a verification loop."
            />
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
