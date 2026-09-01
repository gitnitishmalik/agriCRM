/**
 * One invoice: the document, its money, and the two actions that change it.
 *
 * Issue and Cancel are both one-way, so both say so before you press them.
 * Issue allocates the number; cancelling keeps it, forever, and the series
 * moves on — which is the constraint that stops the historical defect where
 * a cancelled number was reissued to a different document.
 */

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, apiBlob, apiText } from '../api/client'
import { PageHeader } from '../layout/AppShell'
import {
  type InvoiceStatus,
  useCancelInvoice,
  useBillingEntities,
  useInvoice,
  useIssueInvoice,
  useRecordPayment,
} from '../api/billing'
import { IssueConfirmation } from '../components/IssueConfirmation'
import { DeliveryHistory } from '../components/DeliveryHistory'

const STATUS_LABEL: Record<InvoiceStatus, string> = {
  draft: 'Draft',
  issued: 'Issued',
  on_hold: 'On hold',
  part_paid: 'Part paid',
  paid: 'Paid',
  cancelled: 'Cancelled',
  discarded: 'Discarded',
}

export function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: invoice, isLoading } = useInvoice(id)
  const issue = useIssueInvoice()
  const [confirming, setConfirming] = useState(false)
  const cancel = useCancelInvoice()
  const { data: entities } = useBillingEntities()
  // Falls back to the code, which is what the API actually returns. A row
  // reading "TEPL" is honest; one that throws takes the page down.
  const entityName =
    entities?.find((e) => e.code === invoice?.entity_code)?.legal_name ?? invoice?.entity_code
  const entityBank = entities?.find((e) => e.code === invoice?.entity_code)?.bank_name

  // The rendered document, fetched with the bearer token rather than pointed
  // at by the iframe. See `apiText` for why.
  const [documentHtml, setDocumentHtml] = useState<string | null>(null)
  const [documentError, setDocumentError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    setDocumentHtml(null)
    setDocumentError(null)
    apiText(`/api/v1/invoices/${id}/html/`)
      .then((html) => {
        if (!cancelled) setDocumentHtml(html)
      })
      .catch((e: unknown) => {
        if (!cancelled) setDocumentError(e instanceof ApiError ? e.message : 'Could not load the document.')
      })
    return () => {
      cancelled = true
    }
  }, [id])

  const openDocument = () => {
    if (!documentHtml) return
    const url = URL.createObjectURL(new Blob([documentHtml], { type: 'text/html' }))
    window.open(url, '_blank', 'noreferrer')
    // Revoked on a timer rather than immediately: the new tab has to load it
    // first, and revoking synchronously races that load.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

  const [pdfState, setPdfState] = useState<'idle' | 'working' | 'unavailable'>('idle')
  const [pdfError, setPdfError] = useState<string | null>(null)

  /**
   * Download the server-rendered PDF.
   *
   * 🔴 The server's copy, not the browser's. `crm.invoice_delivery` stores a
   * `pdf_sha256`, so the file a customer receives has to be the one the server
   * produced and hashed — a browser print is a different set of bytes every
   * time and cannot be reconciled against that record afterwards.
   *
   * Fetched with the token rather than linked to: an `<a href>` at this
   * endpoint downloads the API's 401 body as a file named like an invoice,
   * which is worse than an error because it looks like it worked.
   */
  const downloadPdf = async () => {
    if (!id) return
    setPdfState('working')
    setPdfError(null)
    try {
      const blob = await apiBlob(`/api/v1/invoices/${id}/pdf/`)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${(invoice?.invoice_no ?? 'draft').replace(/\//g, '-')}.pdf`
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
      setPdfState('idle')
    } catch (e) {
      // 501 is the documented answer from a server without WeasyPrint's GTK
      // libraries — every Windows box and most slim images. That is not a
      // failure the person can act on, so offer the route that always works
      // instead of showing them a stack of words about libgobject.
      if (e instanceof ApiError && e.status === 501) {
        setPdfState('unavailable')
      } else {
        setPdfState('idle')
        setPdfError(e instanceof ApiError ? e.message : 'Could not download the PDF.')
      }
    }
  }

  /**
   * Print the document, which produces a PDF through the browser.
   *
   * Always available — it needs nothing installed on the server — and it is
   * the same HTML, so what prints is what the screen shows. The invoice
   * template carries its own `@page` A4 rules at the original margins.
   */
  const printDocument = () => {
    if (!documentHtml) return
    const frame = document.createElement('iframe')
    frame.style.position = 'fixed'
    frame.style.right = '0'
    frame.style.bottom = '0'
    frame.style.width = '0'
    frame.style.height = '0'
    frame.style.border = '0'
    document.body.appendChild(frame)
    const doc = frame.contentDocument
    if (!doc) return
    doc.open()
    doc.write(documentHtml)
    doc.close()
    // The load has to finish before print, or an empty page goes to the queue.
    frame.onload = () => {
      frame.contentWindow?.focus()
      frame.contentWindow?.print()
      setTimeout(() => frame.remove(), 60_000)
    }
  }
  const payment = useRecordPayment()

  const [cancelling, setCancelling] = useState(false)
  const [reason, setReason] = useState('')
  const [paying, setPaying] = useState(false)
  const [amount, setAmount] = useState('')
  const [receivedOn, setReceivedOn] = useState(new Date().toISOString().slice(0, 10))

  if (isLoading) return <div className="p-6 text-base text-ink-3">Loading…</div>
  if (!invoice) return <div className="p-6 text-base text-ink-3">Not found.</div>

  const outstanding = Number(invoice.amount_outstanding)
  const canIssue = invoice.status === 'draft'
  const canCancel = !['draft', 'discarded', 'cancelled'].includes(invoice.status)
  const canPay = ['issued', 'part_paid'].includes(invoice.status)

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/invoices" className="link">
            Invoices
          </Link>
        }
        title={invoice.invoice_no ?? 'Draft invoice'}
        description={`${invoice.buyer_name} · ${invoice.invoice_date}`}
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={openDocument}
              disabled={!documentHtml}
              className="btn-quiet"
            >
              Open document
            </button>
            {/* 🔴 The server PDF first, print as the fallback. `crm.invoice_
                delivery` records a `pdf_sha256`, and only the server's bytes
                can be reconciled against it — a browser print differs on every
                run. Print appears once the server says it cannot render, which
                on Windows is always. */}
            {pdfState === 'unavailable' ? (
              <button
                type="button"
                onClick={printDocument}
                disabled={!documentHtml}
                className="btn-quiet"
                title="This server has no PDF engine. Print, then choose Save as PDF."
              >
                Print / Save as PDF
              </button>
            ) : (
              <button
                type="button"
                onClick={downloadPdf}
                disabled={!documentHtml || pdfState === 'working'}
                className="btn-quiet"
              >
                {pdfState === 'working' ? 'Preparing…' : 'Download PDF'}
              </button>
            )}
            {canIssue && !confirming && (
              <button
                type="button"
                className="btn-primary"
                onClick={() => setConfirming(true)}
              >
                Review and issue
              </button>
            )}
          </div>
        }
      />

      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <div className="space-y-4">
          {issue.error instanceof ApiError && (
            <Alert tone="bad">{issue.error.message}</Alert>
          )}

          {/*
            🔴 Issuing goes through the pre-issue checks, never straight from a
            button. The panel shows what would block it and what is merely
            worth a look — and the server re-runs the same checks anyway, so
            skipping this screen changes nothing except what the person saw.
          */}
          {canIssue && confirming && id && (
            <IssueConfirmation
              invoiceId={id}
              invoiceNo={invoice.invoice_no}
              issuing={issue.isPending}
              onIssue={(invoiceSha256, acknowledgements) =>
                issue.mutate(
                  {
                    id,
                    invoice_sha256: invoiceSha256,
                    acknowledge: acknowledgements,
                  },
                  { onSuccess: () => setConfirming(false) },
                )
              }
            />
          )}

          {canIssue && !confirming && (
            <Alert tone="note">
              This is a draft — it has no number yet. Issuing allocates one, and from that
              moment the number is permanent.
            </Alert>
          )}

          {invoice.status === 'cancelled' && (
            <Alert tone="bad">
              <strong>Cancelled.</strong> {invoice.cancellation_reason}
              <br />
              <span className="text-xs">
                {invoice.invoice_no} stays used and will not be issued again.
              </span>
            </Alert>
          )}

          <section className="card p-4">
            <div className="label mb-3">Money</div>
            <dl className="space-y-2 text-base">
              <Row label="Taxable" value={`₹${invoice.display.taxable}`} />
              <Row label="Tax" value={`₹${invoice.display.tax}`} />
              <Row label="Total" value={`₹${invoice.display.total}`} strong />
              <Row label="Received" value={`₹${invoice.display.received}`} />
              <Row
                label="Outstanding"
                value={`₹${invoice.display.outstanding}`}
                tone={outstanding > 0 ? 'owed' : 'clear'}
                strong
              />
            </dl>
            {invoice.amount_in_words && (
              <p className="mt-3 border-t border-line pt-3 text-sm text-ink-2">
                {invoice.amount_in_words}
              </p>
            )}
            {invoice.total_area_ha && Number(invoice.total_area_ha) > 0 && (
              <p className="mt-1.5 text-xs text-ink-3">
                Area billed: {invoice.total_area_ha} hectares
              </p>
            )}
          </section>

          <section className="card p-4">
            <div className="label mb-3">Details</div>
            <dl className="space-y-2 text-sm">
              <Row label="Status" value={STATUS_LABEL[invoice.status]} />
              <Row label="Company" value={entityName} />
              <Row label="Buyer GSTIN" value={invoice.buyer_gstin ?? '—'} mono />
              {invoice.buyer_order_no && (
                <Row label="Purchase order" value={invoice.buyer_order_no} mono />
              )}
              {invoice.work_order_ref && (
                <Row label="Work order" value={invoice.work_order_ref} mono />
              )}
              <Row label="Bank" value={entityBank ?? '—'} />
            </dl>
          </section>

          {invoice.payments.length > 0 && (
            <section className="card p-4">
              <div className="label mb-3">Payments</div>
              <ul className="space-y-2 text-sm">
                {invoice.payments.map((p) => (
                  <li key={p.id} className="flex justify-between">
                    <span className="text-ink-2">
                      {p.received_on}
                      {p.mode ? ` · ${p.mode}` : ''}
                    </span>
                    <span className="font-mono">₹{p.amount_display}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/*
            🔴 Each attempt names the PDF hash it carried, not whatever the
            invoice holds now. A resend after a re-render is a different
            artifact, and that is the only reason "which document did they
            receive" has an answer.
          */}
          {id && !canIssue && (
            <DeliveryHistory invoiceId={id} canSend={invoice.status !== 'cancelled'} />
          )}

          {canPay && (
            <section className="card p-4">
              {paying ? (
                <form
                  className="space-y-3"
                  onSubmit={async (e) => {
                    e.preventDefault()
                    if (!id) return
                    await payment.mutateAsync({ id, received_on: receivedOn, amount }).catch(() => null)
                    setPaying(false)
                    setAmount('')
                  }}
                >
                  <div className="label">Record a payment</div>
                  <input
                    className="input font-mono"
                    placeholder="Amount"
                    inputMode="decimal"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    required
                  />
                  <input
                    type="date"
                    className="input"
                    value={receivedOn}
                    onChange={(e) => setReceivedOn(e.target.value)}
                  />
                  <div className="flex gap-2">
                    <button type="submit" className="btn-primary" disabled={payment.isPending}>
                      Record
                    </button>
                    <button type="button" className="btn-quiet" onClick={() => setPaying(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <button type="button" className="btn-quiet w-full" onClick={() => setPaying(true)}>
                  Record a payment
                </button>
              )}
            </section>
          )}

          {canCancel && (
            <section className="card border-quarantine-line p-4">
              {cancelling ? (
                <form
                  className="space-y-3"
                  onSubmit={async (e) => {
                    e.preventDefault()
                    if (!id) return
                    await cancel.mutateAsync({ id, reason }).catch(() => null)
                    setCancelling(false)
                  }}
                >
                  <div className="label text-quarantine">Why is this being cancelled?</div>
                  <p className="text-sm text-ink-2">
                    {invoice.invoice_no} stays used forever and the series has already moved
                    past it. Billing this work again means a new invoice with a new number.
                  </p>
                  <textarea
                    className="input"
                    rows={2}
                    required
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Wrong GSTIN — reissued against the Karnataka registration"
                  />
                  <div className="flex gap-2">
                    <button
                      type="submit"
                      className="btn bg-quarantine text-white hover:opacity-90"
                      disabled={cancel.isPending || !reason.trim()}
                    >
                      Cancel invoice
                    </button>
                    <button type="button" className="btn-quiet" onClick={() => setCancelling(false)}>
                      Go back
                    </button>
                  </div>
                </form>
              ) : (
                <button
                  type="button"
                  className="text-sm text-quarantine underline decoration-quarantine-line underline-offset-2"
                  onClick={() => setCancelling(true)}
                >
                  Cancel this invoice
                </button>
              )}
            </section>
          )}
        </div>

        <div className="space-y-4">
          {/* Why the button changed. Silently swapping "Download PDF" for
              "Print" would read as the feature being broken. */}
          {pdfState === 'unavailable' && (
            <p className="card p-4 text-sm leading-relaxed text-ink-2">
              <span className="label">No PDF engine on this server</span>
              <span className="mt-1 block">
                WeasyPrint needs its GTK system libraries, which Windows and slim container
                images do not ship. Use <strong>Print / Save as PDF</strong> — it prints this
                same document from your browser. To restore server-side PDFs, install the GTK
                runtime and restart the API.
              </span>
            </p>
          )}
          {pdfError && <p className="card p-4 text-sm text-quarantine">{pdfError}</p>}

        <div className="card overflow-hidden">
          {documentError ? (
            <p className="p-6 text-base text-quarantine">{documentError}</p>
          ) : documentHtml === null ? (
            <p className="p-6 text-base text-ink-3">Loading the document…</p>
          ) : (
            <iframe
              title="Invoice document"
              srcDoc={documentHtml}
              sandbox=""
              className="h-[1000px] w-full border-0 bg-white"
            />
          )}
        </div>
        </div>
      </div>
    </>
  )
}

function Row({
  label,
  value,
  strong,
  mono,
  tone,
}: {
  label: string
  value: string
  strong?: boolean
  mono?: boolean
  tone?: 'owed' | 'clear'
}) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-2">{label}</dt>
      <dd
        className={[
          mono ? 'font-mono' : '',
          strong ? 'font-medium' : '',
          tone === 'owed' ? 'text-quarantine' : tone === 'clear' ? 'text-brand' : 'text-ink',
        ].join(' ')}
      >
        {value}
      </dd>
    </div>
  )
}

function Alert({ tone, children }: { tone: 'bad' | 'note'; children: React.ReactNode }) {
  const style =
    tone === 'bad'
      ? 'border-quarantine-line bg-quarantine-soft text-quarantine'
      : 'border-gold-line bg-gold-soft text-ink-2'
  return <div className={`card p-4 text-sm ${style}`}>{children}</div>
}
