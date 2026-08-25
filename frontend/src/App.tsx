/**
 * Routes and the auth boundary.
 *
 * Routes whose backend does not exist yet are present and honest about it —
 * they name the phase and what unblocks it. Hiding them would make the plan
 * invisible; faking them would make the app lie.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { useMe } from './api/auth'
import { tokens } from './api/client'
import { AppShell } from './layout/AppShell'
import { LoginPage } from './pages/Login'
import { MfaPage } from './pages/Mfa'
import { AccountPage } from './pages/Account'
import { DesignSystemPage } from './pages/DesignSystem'
import { NotBuiltYet, OverviewPage } from './pages/Overview'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const hasToken = Boolean(tokens.access || tokens.refresh)
  const { isLoading, isError } = useMe()

  if (!hasToken) return <Navigate to="/login" replace />
  if (isLoading) {
    return <div className="p-6 text-base text-ink-3">Loading…</div>
  }
  if (isError) return <Navigate to="/login" replace />

  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/mfa" element={<MfaPage />} />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/design" element={<DesignSystemPage />} />

        <Route
          path="/organisations"
          element={
            <NotBuiltYet
              title="Organisations"
              phase="Phase 1"
              weeks="4–9"
              holds="FPOs, sugar mills, cooperative societies and the people inside them — with duplicate blocking at creation, bulk import, and the collectors that load MCA, SFAC, ISMA and NFCSF data."
              blockedBy="LGD geography has to load first. Everything joins to it."
            />
          }
        />
        <Route
          path="/farmers"
          element={
            <NotBuiltYet
              title="Farmers"
              phase="Phase 2"
              weeks="10–15"
              holds="Farmer master, land parcels, crops and the consent ledger. Every query carries state_id, because core.farmer is partitioned by state."
              blockedBy="The Theta legacy data audit (Track P2) and a lawyer-reviewed privacy notice (P1). Importing before both is a liability, not a delay."
            />
          }
        />
        <Route
          path="/pipeline"
          element={
            <NotBuiltYet
              title="Pipeline"
              phase="Phase 3"
              weeks="16–22"
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
              weeks="23–29"
              holds="WhatsApp and email, with the exclusion breakdown shown before every send and consent re-checked at dispatch."
              blockedBy="Meta business verification (Track P3), which takes one to three weeks and can stall on documentation."
            />
          }
        />
        <Route
          path="/quality"
          element={
            <NotBuiltYet
              title="Data health"
              phase="Phase 5"
              weeks="30–36"
              holds="Tier distribution over time, the source scorecard, the contradiction queue, and the satellite cross-check that turns Theta's existing analytics into a verification loop."
            />
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
