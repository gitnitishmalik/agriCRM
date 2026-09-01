/**
 * The invoice register.
 *
 * The screen someone keeps open all day, so it is a table and a filter bar and
 * nothing else. Totals sit above the list because "what is outstanding" is the
 * question people actually arrive with; the rows are how they answer the
 * follow-up.
 *
 * Money arrives pre-formatted from the server. Indian digit grouping
 * (6,45,519.00, not 645,519.00) is implemented once, in `money.py`, and a
 * second implementation here would be a second one to get wrong.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../layout/AppShell'
import {
  type InvoiceRow,
  type InvoiceStatus,
  useBillingEntities,
  useInvoiceSummary,
  useInvoices,
} from '../api/billing'

/** Status chips. Colour is never the only signal — the label always reads. */
const STATUS_STYLE: Record<InvoiceStatus, string> = {
  draft: 'bg-sunken text-ink-3 border-line',
  issued: 'bg-silver-soft text-silver border-silver-line',
  on_hold: 'bg-gold-soft text-gold border-gold-line',
  part_paid: 'bg-bronze-soft text-bronze border-bronze-line',
  paid: 'bg-brand-soft text-brand border-brand-line',
  cancelled: 'bg-quarantine-soft text-quarantine border-quarantine-line',
  discarded: 'bg-sunken text-ink-3 border-line',
}

const STATUS_LABEL: Record<InvoiceStatus, string> = {
  draft: 'Draft',
  issued: 'Issued',
  on_hold: 'On hold',
  part_paid: 'Part paid',
  paid: 'Paid',
  cancelled: 'Cancelled',
  discarded: 'Discarded',
}

export function InvoicesPage() {
  const [filters, setFilters] = useState<Record<string, string>>({})
  const { data: entities } = useBillingEntities()
  const { data, isLoading, error } = useInvoices(filters)
  const { data: summary } = useInvoiceSummary(filters)

  const set = (key: string, value: string) =>
    setFilters((f) => {
      const next = { ...f }
      if (value) next[key] = value
      else delete next[key]
      return next
    })

  const rows = data?.results ?? []

  return (
    <>
      <PageHeader
        eyebrow="Billing"
        title="Invoices"
        description="Every invoice raised by Theta Foundation and Theta Enerlytics, with what is still owed."
        actions={
          <Link to="/invoices/new" className="btn-primary">
            New invoice
          </Link>
        }
      />

      <div className="p-6">
        {/* Totals first: the question people arrive with. */}
        {summary && (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Figure label="Invoices" value={String(summary.count)} />
            <Figure label="Billed" value={`₹${summary.display.total}`} />
            <Figure label="Received" value={`₹${summary.display.received}`} />
            <Figure
              label="Outstanding"
              value={`₹${summary.display.outstanding}`}
              tone={Number(summary.amount_outstanding) > 0 ? 'owed' : 'clear'}
            />
          </div>
        )}

        <div className="card mb-4 flex flex-wrap items-end gap-3 p-4">
          <Field label="Company">
            <select
              className="input"
              value={filters.entity_code ?? ''}
              onChange={(e) => set('entity_code', e.target.value)}
            >
              <option value="">All</option>
              {entities?.map((entity) => (
                <option key={entity.id} value={entity.code}>
                  {entity.code} — {entity.legal_name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Status">
            <select
              className="input"
              value={filters.status ?? ''}
              onChange={(e) => set('status', e.target.value)}
            >
              <option value="">All</option>
              {(Object.keys(STATUS_LABEL) as InvoiceStatus[]).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Financial year">
            <select
              className="input"
              value={filters.financial_year ?? ''}
              onChange={(e) => set('financial_year', e.target.value)}
            >
              <option value="">All</option>
              {['2026-27', '2025-26', '2024-25'].map((fy) => (
                <option key={fy} value={fy}>
                  {fy}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Search" grow>
            <input
              className="input"
              placeholder="Invoice number or buyer"
              value={filters.search ?? ''}
              onChange={(e) => set('search', e.target.value)}
            />
          </Field>

          <label className="flex items-center gap-2 pb-2 text-sm text-ink-2">
            <input
              type="checkbox"
              checked={filters.outstanding === 'true'}
              onChange={(e) => set('outstanding', e.target.checked ? 'true' : '')}
            />
            Unpaid only
          </label>
        </div>

        {error && (
          <p role="alert" className="card border-quarantine-line bg-quarantine-soft p-4 text-sm text-quarantine">
            {error instanceof Error ? error.message : 'Could not load invoices.'}
          </p>
        )}

        {isLoading ? (
          <p className="text-base text-ink-3">Loading…</p>
        ) : rows.length === 0 ? (
          <Empty hasFilters={Object.keys(filters).length > 0} />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-base">
              <thead>
                <tr className="border-b border-line bg-sunken">
                  <Th>Invoice</Th>
                  <Th>Date</Th>
                  <Th>Buyer</Th>
                  <Th right>Total</Th>
                  <Th right>Outstanding</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Row key={row.id} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}

function Row({ row }: { row: InvoiceRow }) {
  const outstanding = Number(row.amount_outstanding)
  return (
    <tr className="border-b border-line last:border-0 hover:bg-canvas">
      <td className="px-4 py-2.5">
        <Link to={`/invoices/${row.id}`} className="link font-mono">
          {row.invoice_no ?? 'Draft'}
        </Link>
        <div className="text-xs text-ink-3">{row.entity_code}</div>
      </td>
      <td className="px-4 py-2.5 font-mono text-sm text-ink-2">{row.invoice_date}</td>
      <td className="max-w-xs truncate px-4 py-2.5">{row.buyer_name}</td>
      <td className="px-4 py-2.5 text-right font-mono">₹{row.display.total}</td>
      <td className="px-4 py-2.5 text-right font-mono">
        {outstanding > 0 ? (
          <span className="text-quarantine">₹{row.display.outstanding}</span>
        ) : (
          <span className="text-ink-3">—</span>
        )}
      </td>
      <td className="px-4 py-2.5">
        <span
          className={`inline-block rounded-chip border px-2 py-0.5 text-2xs uppercase tracking-wide ${
            STATUS_STYLE[row.status]
          }`}
        >
          {STATUS_LABEL[row.status]}
        </span>
      </td>
    </tr>
  )
}

function Figure({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'owed' | 'clear'
}) {
  return (
    <div className="card p-4">
      <div className="label">{label}</div>
      <div
        className={`mt-1 font-mono text-xl ${
          tone === 'owed' ? 'text-quarantine' : tone === 'clear' ? 'text-brand' : 'text-ink'
        }`}
      >
        {value}
      </div>
    </div>
  )
}

function Field({
  label,
  children,
  grow,
}: {
  label: string
  children: React.ReactNode
  grow?: boolean
}) {
  return (
    <div className={grow ? 'min-w-[200px] flex-1' : ''}>
      <div className="label mb-1.5">{label}</div>
      {children}
    </div>
  )
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`label px-4 py-2.5 ${right ? 'text-right' : 'text-left'}`}>{children}</th>
  )
}

/**
 * An empty register is the normal state on day one, so it says what to do
 * rather than looking broken. Distinguishing "no invoices" from "no matches"
 * matters — one needs a first invoice, the other needs the filters cleared.
 */
function Empty({ hasFilters }: { hasFilters: boolean }) {
  return (
    <div className="card p-10 text-center">
      <p className="text-lg text-ink-2">
        {hasFilters ? 'No invoices match these filters.' : 'No invoices yet.'}
      </p>
      {!hasFilters && (
        <>
          <p className="mx-auto mt-2 max-w-md text-base text-ink-3">
            Raise one by hand, or upload an existing invoice and let it fill the form in
            for you.
          </p>
          <Link to="/invoices/new" className="btn-primary mt-5 inline-flex">
            New invoice
          </Link>
        </>
      )}
    </div>
  )
}
