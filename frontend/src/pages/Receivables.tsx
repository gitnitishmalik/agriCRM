/**
 * Receivables — ageing, and who to chase first.
 *
 * 🔴 Every figure comes from the server pre-formatted. Outstanding is derived
 * from payment rows rather than stored (INVOICE.md §4.5), and the Indian
 * grouping lives in `api/money.py` — a second implementation here would
 * disagree by a rupee somewhere nobody looks.
 *
 * 🔴 The priority score renders its factors. A score whose inputs are hidden
 * is a score nobody can argue with, and this one exists to start a
 * conversation about who to call, not to settle it.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgeing, useCollectionPriority } from '../api/collections'

const BUCKET_TONE: Record<string, string> = {
  current: 'bg-leaf-50 text-leaf-800 border-leaf-200',
  '1_30': 'bg-amber-50 text-amber-800 border-amber-200',
  '31_60': 'bg-amber-50 text-amber-900 border-amber-300',
  '61_90': 'bg-clay-50 text-clay-800 border-clay-200',
  '90_plus': 'bg-clay-100 text-clay-900 border-clay-300',
}

const BAND_TONE: Record<string, string> = {
  high: 'bg-clay-100 text-clay-900',
  medium: 'bg-amber-100 text-amber-900',
  low: 'bg-stone-100 text-stone-600',
}

export default function ReceivablesPage() {
  const [expanded, setExpanded] = useState<string | null>(null)

  const ageing = useAgeing()
  const priority = useCollectionPriority(50)

  if (ageing.isLoading) {
    return <p className="p-6 text-sm text-stone-500">Loading receivables…</p>
  }

  if (ageing.isError || !ageing.data) {
    return (
      <p className="p-6 text-sm text-clay-700">
        The receivables report could not be loaded.
      </p>
    )
  }

  const { summary, rows, by_buyer: byBuyer } = ageing.data
  const scores = new Map((priority.data ?? []).map((p) => [p.invoice_id, p]))

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-stone-900">Receivables</h1>
        <p className="mt-1 max-w-2xl text-sm text-stone-500">
          Outstanding is derived from payment rows, never stored — a stored
          balance and a payment ledger disagree the first time somebody
          backdates a receipt, and the one people trust is the wrong one.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {summary.buckets.map((bucket) => (
          <div
            key={bucket.bucket}
            className={`rounded-lg border px-4 py-3 ${
              BUCKET_TONE[bucket.bucket] ?? 'border-stone-200 bg-white'
            }`}
          >
            <p className="text-lg font-semibold tabular-nums">{bucket.display}</p>
            <p className="mt-0.5 text-xs">
              {bucket.label} · {bucket.count} invoice{bucket.count === 1 ? '' : 's'}
            </p>
          </div>
        ))}
      </div>

      {summary.assumed_due_dates > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <strong className="font-medium">{summary.assumed_due_dates}</strong>{' '}
          invoice{summary.assumed_due_dates === 1 ? '' : 's'} had no due date and{' '}
          {summary.assumed_due_dates === 1 ? 'was' : 'were'} aged from 30 days
          after the invoice date. A report that silently invents a due date looks
          authoritative and is partly fiction — those rows are marked below.
        </div>
      )}

      <section className="rounded-lg border border-stone-200 bg-white">
        <h2 className="border-b border-stone-200 px-4 py-2.5 text-sm font-semibold text-stone-800">
          By customer
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left text-xs uppercase tracking-wide text-stone-500">
              <tr>
                <th className="px-4 py-2 font-medium">Customer</th>
                <th className="px-4 py-2 text-right font-medium">Invoices</th>
                <th className="px-4 py-2 text-right font-medium">Outstanding</th>
                <th className="px-4 py-2 text-right font-medium">Oldest</th>
                <th className="px-4 py-2 font-medium">Contactable</th>
              </tr>
            </thead>
            <tbody>
              {byBuyer.map((buyer) => (
                <tr key={buyer.buyer_name} className="border-t border-stone-100">
                  <td className="px-4 py-2 text-stone-800">{buyer.buyer_name}</td>
                  <td className="px-4 py-2 text-right tabular-nums">
                    {buyer.invoice_count}
                  </td>
                  <td className="px-4 py-2 text-right font-medium tabular-nums">
                    {buyer.display.total_outstanding}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-stone-600">
                    {buyer.oldest_days_overdue} d
                  </td>
                  <td className="px-4 py-2">
                    {buyer.billing_opt_out ? (
                      <span className="rounded bg-clay-100 px-1.5 py-0.5 text-xs text-clay-800">
                        opted out
                      </span>
                    ) : buyer.billing_email ? (
                      <span className="text-xs text-stone-500">email on file</span>
                    ) : (
                      <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
                        no contact
                      </span>
                    )}
                  </td>
                </tr>
              ))}
              {byBuyer.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-stone-500">
                    Nothing outstanding.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white">
        <div className="border-b border-stone-200 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-stone-800">Who to chase first</h2>
          <p className="mt-0.5 text-xs text-stone-500">
            Advisory and deterministic: days overdue, amount outstanding,
            promised payments and reminders already sent. No model produced it,
            it uses no personal characteristics, and it denies nobody service.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left text-xs uppercase tracking-wide text-stone-500">
              <tr>
                <th className="px-4 py-2 font-medium">Invoice</th>
                <th className="px-4 py-2 font-medium">Customer</th>
                <th className="px-4 py-2 text-right font-medium">Overdue</th>
                <th className="px-4 py-2 text-right font-medium">Outstanding</th>
                <th className="px-4 py-2 font-medium">Bucket</th>
                <th className="px-4 py-2 text-right font-medium">Priority</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const score = scores.get(row.invoice_id)
                const open = expanded === row.invoice_id
                return (
                  <>
                    <tr
                      key={row.invoice_id}
                      className="cursor-pointer border-t border-stone-100 hover:bg-stone-50"
                      onClick={() => setExpanded(open ? null : row.invoice_id)}
                    >
                      <td className="px-4 py-2">
                        <Link
                          to={`/invoices/${row.invoice_id}`}
                          className="text-leaf-700 hover:underline"
                          onClick={(event) => event.stopPropagation()}
                        >
                          {row.invoice_no ?? '— draft —'}
                        </Link>
                      </td>
                      <td className="px-4 py-2 text-stone-700">{row.buyer_name}</td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {row.days_overdue} d
                        {row.due_date_assumed && (
                          <span className="ml-1 text-xs text-stone-400">assumed</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right font-medium tabular-nums">
                        {row.display.outstanding}
                      </td>
                      <td className="px-4 py-2">
                        <span
                          className={`rounded border px-1.5 py-0.5 text-xs ${
                            BUCKET_TONE[row.bucket] ?? ''
                          }`}
                        >
                          {row.bucket_label}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right">
                        {score && (
                          <span
                            className={`rounded px-1.5 py-0.5 text-xs font-medium tabular-nums ${
                              BAND_TONE[score.band]
                            }`}
                          >
                            {score.score}
                          </span>
                        )}
                      </td>
                    </tr>

                    {/* 🔴 The arithmetic behind the score, on demand. */}
                    {open && score && (
                      <tr key={`${row.invoice_id}-why`} className="bg-stone-50">
                        <td colSpan={6} className="px-4 py-3">
                          <ul className="space-y-1 text-xs text-stone-600">
                            {score.factors.map((factor, index) => (
                              <li key={index}>
                                <span className="inline-block w-12 text-right font-medium tabular-nums">
                                  {factor.points > 0 ? `+${factor.points}` : factor.points}
                                </span>
                                <span className="ml-3">{factor.explanation}</span>
                              </li>
                            ))}
                          </ul>
                          <p className="mt-2 text-[11px] italic text-stone-400">
                            {score.disclaimer}
                          </p>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-stone-500">
                    Nothing outstanding.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <p className="text-xs text-stone-400">{summary.note}</p>
    </div>
  )
}
