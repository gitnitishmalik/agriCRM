/**
 * Dashboard.
 *
 * Layout is the shadcn admin pattern: a stat-tile row, a trend chart, and a
 * recent-records table. The numbers in it are real — `/invoices/summary/`
 * aggregates in the database, and the chart and table are built from the
 * invoice register itself.
 *
 * 🔴 That constraint is the whole design. The predecessor to this page
 * deliberately showed no tiles at all, on the grounds that a row of zeroes
 * reads as "the database is empty" when the truth is "this is not built yet",
 * and the two call for opposite responses from whoever is looking. This page
 * keeps that rule rather than dropping it for a better-looking screenshot: a
 * tile appears when there is something to count, an empty state appears when
 * there is not, and nothing here is ever a placeholder number.
 *
 * The build-status panel below survives for the same reason. Until the
 * registry and farmer modules land there is genuinely more to say about where
 * the build has reached than about the data, and Track P is on it because all
 * four workstreams have multi-week external lead times and each one blocks a
 * later phase.
 */

import { Suspense, lazy } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, FileText, IndianRupee, Landmark, TrendingUp } from 'lucide-react'

import { PageHeader } from '../layout/AppShell'
import { useMe } from '../api/auth'
import { type InvoiceRow, type InvoiceStatus, useInvoices, useInvoiceSummary } from '../api/billing'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'

// Recharts is ~380 kB. Loaded only when there is actually a trend to draw —
// see the note in InvoiceTrendChart.tsx.
const InvoiceTrendChart = lazy(() => import('../components/InvoiceTrendChart'))

// ---------------------------------------------------------------------------
// Build status. Unchanged in substance from the page this replaces.
// ---------------------------------------------------------------------------

const TRACK_P = [
  { code: 'P1', name: 'Data protection lawyer', leadTime: '3–6 weeks', blocks: 'Phase 2 exit, Phase 4 launch' },
  { code: 'P2', name: 'Theta legacy data audit', leadTime: '4–8 weeks', blocks: 'Phase 2 import' },
  { code: 'P3', name: 'Meta business verification', leadTime: '1–3 weeks', blocks: 'Phase 4 entirely' },
  { code: 'P4', name: 'BD partnership outreach', leadTime: 'Continuous', blocks: 'Phase 2 data volume' },
]

const PHASES = [
  { n: 0, name: 'Foundation', state: 'done' },
  { n: 1, name: 'Auth & environment safety', state: 'done' },
  { n: 2, name: 'Organisation registry', state: 'next' },
  { n: 3, name: 'Farmer core & consent', state: 'todo' },
  { n: 4, name: 'Commercial modules', state: 'todo' },
  { n: 5, name: 'Engagement engine', state: 'todo' },
  { n: 6, name: 'Data intelligence', state: 'todo' },
  { n: 7, name: 'Field mobile app', state: 'todo' },
] as const

export function DashboardPage() {
  const { data: me } = useMe()
  const summary = useInvoiceSummary()
  const invoices = useInvoices({ limit: '100' })

  const rows = invoices.data?.results ?? []
  const hasData = (summary.data?.count ?? 0) > 0

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title={me ? `Good to see you, ${me.full_name.split(' ')[0]}` : 'Overview'}
        description="The invoice register is live. Registry and farmer data arrive in the phases below."
        actions={
          <Button variant="primary" asChild>
            <Link to="/invoices/new">New invoice</Link>
          </Button>
        }
      />

      <div className="space-y-6 p-6">
        {hasData ? (
          <>
            <StatRow
              count={summary.data!.count}
              total={summary.data!.display.total}
              received={summary.data!.display.received}
              outstanding={summary.data!.display.outstanding}
              area={summary.data!.total_area_ha}
            />
            <TrendCard rows={rows} />
            <RegisterCard rows={rows} loading={invoices.isLoading} />
          </>
        ) : (
          <NoInvoicesYet loading={summary.isLoading} />
        )}

        <BuildStatus />
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Stat tiles
// ---------------------------------------------------------------------------

function StatRow({
  count,
  total,
  received,
  outstanding,
  area,
}: {
  count: number
  total: string
  received: string
  outstanding: string
  area: string
}) {
  // Collected share is a ratio of two numbers the server already agreed on,
  // so it cannot disagree with the tiles beside it.
  const collected = Number(received.replace(/[^0-9.]/g, ''))
  const invoiced = Number(total.replace(/[^0-9.]/g, ''))
  const share = invoiced > 0 ? Math.round((collected / invoiced) * 100) : null
  const hectares = Number(area)

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        icon={IndianRupee}
        label="Invoiced"
        value={`₹${total}`}
        note={`Across ${count} issued ${count === 1 ? 'invoice' : 'invoices'}`}
      />
      <StatTile
        icon={TrendingUp}
        label="Received"
        value={`₹${received}`}
        note={share === null ? 'Nothing invoiced yet' : `${share}% of invoiced value`}
      />
      <StatTile
        icon={FileText}
        label="Outstanding"
        value={`₹${outstanding}`}
        note="Issued and part-paid invoices"
        // 🔴 Emphasis, not alarm. Money owed is normal; the tile turns only
        // when the figure is non-zero, and it never claims the debt is late —
        // ageing is Phase 4 and inventing it here would be a guess.
        emphasis={Number(outstanding.replace(/[^0-9.]/g, '')) > 0}
      />
      {/* 🔴 Area is genuinely zero on a register of lump-sum work, and "0 ha"
          is the uninformative zero this page exists to avoid — it reads as a
          missing figure rather than as an accurate one. Say which it is. */}
      <StatTile
        icon={Landmark}
        label="Area billed"
        value={
          hectares > 0
            ? `${hectares.toLocaleString('en-IN', { maximumFractionDigits: 2 })} ha`
            : '—'
        }
        note={hectares > 0 ? 'Hectares across all invoice lines' : 'No area-billed lines yet'}
      />
    </div>
  )
}

function StatTile({
  icon: Icon,
  label,
  value,
  note,
  emphasis,
}: {
  icon: typeof IndianRupee
  label: string
  value: string
  note: string
  emphasis?: boolean
}) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex items-center justify-between">
          <span className="label">{label}</span>
          <Icon className="size-4 text-ink-3" />
        </div>
        <div
          className={`mt-2 font-mono text-2xl font-semibold tabular-nums ${
            emphasis ? 'text-bronze' : 'text-ink'
          }`}
        >
          {value}
        </div>
        <p className="mt-1 text-sm text-ink-2">{note}</p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Trend
// ---------------------------------------------------------------------------

/** Monthly invoiced value, oldest first. Derived from the register, not stored. */
function monthlyTotals(rows: InvoiceRow[]) {
  const buckets = new Map<string, number>()

  for (const row of rows) {
    if (row.status === 'cancelled' || row.status === 'discarded' || row.status === 'draft') {
      continue // matches what /summary/ counts, so the chart and the tiles agree
    }
    const month = row.invoice_date.slice(0, 7) // YYYY-MM
    buckets.set(month, (buckets.get(month) ?? 0) + Number(row.total_value))
  }

  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, value]) => ({
      month,
      label: new Date(`${month}-01`).toLocaleDateString('en-IN', {
        month: 'short',
        year: '2-digit',
      }),
      value,
    }))
}

function TrendCard({ rows }: { rows: InvoiceRow[] }) {
  const data = monthlyTotals(rows)

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Invoiced by month</CardTitle>
          <CardDescription>
            Issued value from the register. Drafts and cancellations are excluded, as they
            are in the totals above.
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/invoices">
            Open register
            <ArrowUpRight />
          </Link>
        </Button>
      </CardHeader>

      <CardContent>
        {data.length < 2 ? (
          // One point is not a trend. Saying so is more useful than drawing a
          // line through a single value and letting it imply a direction.
          <p className="py-8 text-center text-base text-ink-3">
            {data.length === 0
              ? 'Nothing issued yet.'
              : 'One month of history so far — a trend needs a second.'}
          </p>
        ) : (
          <Suspense
            fallback={<div className="h-64 animate-pulse rounded-card bg-sunken" />}
          >
            <InvoiceTrendChart data={data} />
          </Suspense>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

/**
 * Invoice status → chip.
 *
 * 🔴 Not the tier inks. An invoice being paid and an organisation being Gold
 * are unrelated facts; painting them alike would teach a relationship that
 * does not exist. Only cancellation borrows quarantine, where both genuinely
 * mean "this record is out of use".
 */
const STATUS_STYLE: Record<InvoiceStatus, { variant: 'neutral' | 'brand' | 'outline' | 'quarantine'; label: string }> = {
  draft: { variant: 'outline', label: 'Draft' },
  issued: { variant: 'neutral', label: 'Issued' },
  on_hold: { variant: 'outline', label: 'On hold' },
  part_paid: { variant: 'neutral', label: 'Part paid' },
  paid: { variant: 'brand', label: 'Paid' },
  cancelled: { variant: 'quarantine', label: 'Cancelled' },
  discarded: { variant: 'quarantine', label: 'Discarded' },
}

function RegisterCard({ rows, loading }: { rows: InvoiceRow[]; loading: boolean }) {
  const recent = rows.slice(0, 8)

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Recent invoices</CardTitle>
          <CardDescription>The eight most recent records in the register.</CardDescription>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link to="/invoices">View all</Link>
        </Button>
      </CardHeader>

      <CardContent className="px-0 pb-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-5">Invoice</TableHead>
              <TableHead>Buyer</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="pr-5 text-right">Outstanding</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && (
              <TableEmpty colSpan={6}>Loading…</TableEmpty>
            )}
            {!loading && recent.length === 0 && (
              <TableEmpty colSpan={6}>No invoices yet.</TableEmpty>
            )}
            {recent.map((row) => {
              const style = STATUS_STYLE[row.status]
              return (
                <TableRow key={row.id}>
                  <TableCell className="pl-5">
                    <Link
                      to={`/invoices/${row.id}`}
                      className="font-mono text-brand hover:underline"
                    >
                      {/* A draft has no number yet — allocation happens at
                          issue, which is what makes the number immutable. */}
                      {row.invoice_no ?? '— draft —'}
                    </Link>
                    <div className="text-2xs uppercase tracking-wide text-ink-3">
                      {row.entity_code}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-56 truncate font-sans">
                    {row.organisation_name ?? row.buyer_name}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-ink-2">
                    {new Date(row.invoice_date).toLocaleDateString('en-IN', {
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })}
                  </TableCell>
                  <TableCell>
                    <Badge variant={style.variant}>{style.label}</Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono">₹{row.display.total}</TableCell>
                  <TableCell className="pr-5 text-right font-mono">
                    {Number(row.amount_outstanding) > 0 ? (
                      <span className="text-bronze">₹{row.display.outstanding}</span>
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------

function NoInvoicesYet({ loading }: { loading: boolean }) {
  return (
    <Card>
      <CardContent className="py-12 text-center">
        <FileText className="mx-auto size-6 text-ink-3" />
        <h2 className="mt-3 text-lg font-semibold text-ink">
          {loading ? 'Reading the register…' : 'No invoices yet'}
        </h2>
        {!loading && (
          <>
            <p className="mx-auto mt-1.5 max-w-md text-base text-ink-2">
              Tiles and the trend chart appear once there is something to count. Showing
              zeroes here would read as “the database is empty” when the truth is that
              nothing has been issued.
            </p>
            <Button variant="primary" className="mt-4" asChild>
              <Link to="/invoices/new">Create the first invoice</Link>
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function BuildStatus() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Build phases</CardTitle>
            <CardDescription>
              A phase ends when its exit gate passes, not when its weeks run out.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1.5">
            {PHASES.map((phase) => (
              <li key={phase.n} className="flex items-center gap-2.5 text-base">
                <span
                  className={`size-1.5 shrink-0 rounded-full ${
                    phase.state === 'done'
                      ? 'bg-brand'
                      : phase.state === 'next'
                        ? 'bg-bronze'
                        : 'bg-line-strong'
                  }`}
                />
                <span className="font-mono text-sm text-ink-3">{phase.n}</span>
                <span className={phase.state === 'todo' ? 'text-ink-3' : 'text-ink'}>
                  {phase.name}
                </span>
                {phase.state === 'next' && (
                  <Badge variant="bronze" className="ml-auto">
                    Next
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Track P — external lead times</CardTitle>
            <CardDescription>
              🔴 All four start in week 1 regardless of engineering phase. Each blocks a
              later phase and each is routinely forgotten until it becomes the reason one
              slips.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Workstream</TableHead>
                <TableHead>Lead time</TableHead>
                <TableHead className="pr-5">Blocks</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {TRACK_P.map((track) => (
                <TableRow key={track.code}>
                  <TableCell className="pl-5 font-sans">
                    <span className="font-mono text-sm text-ink-3">{track.code}</span>{' '}
                    {track.name}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-ink-2">{track.leadTime}</TableCell>
                  <TableCell className="pr-5 font-sans text-ink-2">{track.blocks}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
