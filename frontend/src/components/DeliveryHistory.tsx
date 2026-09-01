/**
 * Delivery history and the send flow, for the invoice detail screen.
 *
 * 🔴 **Preview, then confirm the preview's hash.** The send button quotes back
 * the digest of exactly what was rendered — recipient, subject, body and PDF
 * hash together. If anything changed between seeing it and confirming it, the
 * server refuses rather than delivering to whoever the address resolves to now.
 *
 * 🔴 **Each past attempt shows the PDF hash it carried**, not whatever the
 * invoice holds today. A resend after a re-render is a different artifact, and
 * "which document did they actually receive" is only answerable because every
 * attempt recorded what it sent.
 */

import { useState } from 'react'
import {
  type DeliveryPreview,
  useDeliveryHistory,
  usePreviewDelivery,
  useSendDelivery,
} from '../api/collections'

interface Props {
  invoiceId: string
  /** Sending is refused for a draft; the panel says so rather than failing. */
  canSend: boolean
}

const STATUS_TONE: Record<string, string> = {
  queued: 'bg-amber-100 text-amber-800',
  claimed: 'bg-amber-100 text-amber-800',
  sent: 'bg-leaf-100 text-leaf-800',
  delivered: 'bg-leaf-100 text-leaf-800',
  failed: 'bg-clay-100 text-clay-800',
  cancelled: 'bg-stone-100 text-stone-600',
}

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 12)}…` : '—'
}

export function DeliveryHistory({ invoiceId, canSend }: Props) {
  const [preview, setPreview] = useState<DeliveryPreview | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)

  const history = useDeliveryHistory(invoiceId)
  const buildPreview = usePreviewDelivery()
  const send = useSendDelivery()

  async function openPreview() {
    setSendError(null)
    const result = await buildPreview.mutateAsync({
      invoiceId,
      channel: 'email',
      attach_pdf: true,
    })
    setPreview(result)
  }

  async function confirmSend() {
    if (!preview) return
    setSendError(null)
    try {
      await send.mutateAsync({
        invoiceId,
        // 🔴 The digest of exactly what is on screen.
        preview_sha256: preview.preview_sha256,
        channel: preview.channel,
        attach_pdf: true,
      })
      setPreview(null)
      await history.refetch()
    } catch (error) {
      setSendError(
        error instanceof Error ? error.message : 'The send was refused.',
      )
    }
  }

  return (
    <section className="rounded-lg border border-stone-200 bg-white">
      <header className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-stone-800">Deliveries</h2>
          <p className="mt-0.5 text-xs text-stone-500">
            Every attempt, with the document hash it carried.
          </p>
        </div>

        {canSend && !preview && (
          <button
            type="button"
            onClick={openPreview}
            disabled={buildPreview.isPending}
            className="rounded-md border border-stone-200 px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-50"
          >
            {buildPreview.isPending ? 'Preparing…' : 'Send to customer'}
          </button>
        )}
      </header>

      {preview && (
        <div className="space-y-3 border-b border-stone-200 bg-stone-50 p-4">
          {preview.blocked_reason ? (
            <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-sm text-clay-800">
              {preview.blocked_reason}
            </div>
          ) : (
            <>
              {preview.warnings.map((warning, index) => (
                <div
                  key={index}
                  className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
                >
                  {warning}
                </div>
              ))}

              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-stone-500">To</dt>
                <dd className="font-medium text-stone-800">
                  {preview.recipient}
                  {preview.recipient_name && (
                    <span className="ml-1 text-stone-500">({preview.recipient_name})</span>
                  )}
                </dd>

                {preview.subject && (
                  <>
                    <dt className="text-stone-500">Subject</dt>
                    <dd className="text-stone-800">{preview.subject}</dd>
                  </>
                )}

                <dt className="text-stone-500">Attachment</dt>
                <dd className="font-mono text-xs text-stone-600">
                  {shortHash(preview.pdf_sha256)}
                </dd>
              </dl>

              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded border border-stone-200 bg-white p-3 text-xs text-stone-700">
                {preview.body}
              </pre>
            </>
          )}

          {sendError && (
            <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-sm text-clay-800">
              {sendError}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={confirmSend}
              disabled={!preview.can_send || send.isPending}
              className="rounded-md bg-leaf-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-leaf-800 disabled:opacity-40"
            >
              {send.isPending ? 'Sending…' : 'Send exactly this'}
            </button>
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="rounded-md border border-stone-200 px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-50"
            >
              Cancel
            </button>
            <span className="text-xs text-stone-400">
              Consent is re-checked at the moment of sending.
            </span>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-xs uppercase tracking-wide text-stone-500">
            <tr>
              <th className="px-4 py-2 font-medium">Channel</th>
              <th className="px-4 py-2 font-medium">Recipient</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Document sent</th>
              <th className="px-4 py-2 font-medium">When</th>
            </tr>
          </thead>
          <tbody>
            {(history.data ?? []).map((row) => (
              <tr key={row.id} className="border-t border-stone-100 align-top">
                <td className="px-4 py-2">
                  {row.channel}
                  {row.is_reminder && (
                    <span className="ml-1 rounded bg-stone-100 px-1 text-xs text-stone-600">
                      reminder
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-stone-700">{row.recipient}</td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      STATUS_TONE[row.status] ?? 'bg-stone-100 text-stone-600'
                    }`}
                  >
                    {row.status}
                  </span>
                  {row.error_detail && (
                    <p className="mt-0.5 text-xs text-stone-500">{row.error_detail}</p>
                  )}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-stone-600">
                  {shortHash(row.pdf_sha256)}
                </td>
                <td className="px-4 py-2 text-xs text-stone-500">
                  {row.sent_at
                    ? new Date(row.sent_at).toLocaleString('en-IN')
                    : new Date(row.confirmed_at).toLocaleString('en-IN')}
                </td>
              </tr>
            ))}
            {(history.data ?? []).length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-sm text-stone-500">
                  This invoice has not been sent.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
