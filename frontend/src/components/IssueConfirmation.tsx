/**
 * The issue confirmation panel.
 *
 * 🔴 **This screen cannot let an invoice out that the server would refuse.**
 * It runs the same checks, shows the same findings, and disables the button on
 * blocking ones — but that is a courtesy, not the control. `issue_invoice`
 * re-runs the checks itself and refuses on its own findings, so a client that
 * skipped this call, or called it and ignored the answer, still cannot issue.
 *
 * 🔴 **"Not checked" is rendered distinctly from "checked and fine".** The
 * area reconciliation cannot run until Phase 3 and the satellite cross-check
 * until Phase 5. Collapsing those into a green tick would be a false
 * assurance about the exact question this system exists to answer.
 *
 * 🔴 **Acknowledging a warning needs a reason, and the reason is stored.** A
 * checkbox that says "I have read this" records nothing anybody can review.
 */

import { useEffect, useState } from 'react'
import {
  type CheckReport,
  type CheckResult,
  useAcknowledgeCheck,
  useRunChecks,
} from '../api/copilot'

interface Props {
  invoiceId: string
  invoiceNo: string | null
  /** Called with the fingerprint the checks ran against, so issue can quote it. */
  onIssue: (invoiceSha256: string, acknowledgements: Acknowledgement[]) => void
  issuing?: boolean
}

export interface Acknowledgement {
  code: string
  reason: string
}

const SEVERITY_STYLE: Record<string, string> = {
  error: 'border-clay-200 bg-clay-50',
  warning: 'border-amber-200 bg-amber-50',
  info: 'border-stone-200 bg-stone-50',
}

const SEVERITY_LABEL: Record<string, string> = {
  error: 'Blocks issue',
  warning: 'Worth a look',
  info: 'For information',
}

function Finding({
  result,
  acknowledged,
  onAcknowledge,
}: {
  result: CheckResult
  acknowledged: boolean
  onAcknowledge: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const [open, setOpen] = useState(false)

  return (
    <div
      className={`rounded-md border px-3 py-2.5 ${
        SEVERITY_STYLE[result.severity] ?? SEVERITY_STYLE.info
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-stone-800">{result.title}</p>
          <p className="mt-0.5 text-xs text-stone-600">{result.explanation}</p>
        </div>

        <span className="shrink-0 rounded-full border border-current/20 px-2 py-0.5 text-[11px] font-medium text-stone-600">
          {result.not_available ? 'Not checked' : SEVERITY_LABEL[result.severity]}
        </span>
      </div>

      {/* 🔴 A check that could not run says so, and says why. It is not a pass. */}
      {result.not_available && (
        <p className="mt-1.5 text-[11px] italic text-stone-500">
          This check could not run — it is reported so the gap is visible rather
          than mistaken for a clean result.
        </p>
      )}

      {result.severity === 'warning' && !result.blocks_issue && (
        <div className="mt-2">
          {acknowledged ? (
            <span className="text-xs text-leaf-700">Acknowledged.</span>
          ) : open ? (
            <div className="flex items-center gap-2">
              <input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Why is this acceptable?"
                className="flex-1 rounded border border-stone-200 px-2 py-1 text-xs"
              />
              <button
                type="button"
                disabled={reason.trim().length < 3}
                onClick={() => onAcknowledge(reason.trim())}
                className="rounded bg-stone-700 px-2 py-1 text-xs text-white disabled:opacity-40"
              >
                Record
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setOpen(true)}
              className="text-xs font-medium text-stone-600 underline"
            >
              Acknowledge with a reason
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export function IssueConfirmation({ invoiceId, invoiceNo, onIssue, issuing }: Props) {
  const [report, setReport] = useState<CheckReport | null>(null)
  const [acknowledgements, setAcknowledgements] = useState<Acknowledgement[]>([])

  const runChecks = useRunChecks()
  const acknowledge = useAcknowledgeCheck()

  useEffect(() => {
    runChecks.mutateAsync(invoiceId).then(setReport).catch(() => setReport(null))
    // Re-run when the invoice changes, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoiceId])

  if (runChecks.isPending && !report) {
    return (
      <div className="rounded-lg border border-stone-200 bg-white p-4 text-sm text-stone-500">
        Running the pre-issue checks…
      </div>
    )
  }

  if (!report) {
    return (
      <div className="rounded-lg border border-clay-200 bg-clay-50 p-4 text-sm text-clay-800">
        The checks could not be run. Issuing is refused until they can — the
        server runs them again anyway and would decline.
      </div>
    )
  }

  const blocking = report.results.filter((r) => r.blocks_issue)
  const warnings = report.results.filter((r) => r.severity === 'warning' && !r.blocks_issue)
  const notes = report.results.filter((r) => r.severity === 'info')
  const acknowledgedCodes = new Set([
    ...report.acknowledged_codes,
    ...acknowledgements.map((a) => a.code),
  ])

  return (
    <section className="rounded-lg border border-stone-200 bg-white">
      <header className="border-b border-stone-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-stone-800">
          Issue {invoiceNo ?? 'this invoice'}
        </h2>
        <p className="mt-0.5 text-xs text-stone-500">
          Issuing allocates a permanent number and freezes the document. The
          number is never reused, even if the invoice is later cancelled.
        </p>
      </header>

      <div className="space-y-2.5 p-4">
        {blocking.length > 0 && (
          <div className="rounded-md border border-clay-200 bg-clay-100 px-3 py-2 text-sm font-medium text-clay-900">
            {blocking.length} check{blocking.length === 1 ? '' : 's'} must be
            resolved first. An invoice with these problems would be wrong in
            someone else&rsquo;s accounts.
          </div>
        )}

        {blocking.map((result) => (
          <Finding
            key={result.code}
            result={result}
            acknowledged={false}
            onAcknowledge={() => undefined}
          />
        ))}

        {warnings.map((result) => (
          <Finding
            key={result.code}
            result={result}
            acknowledged={acknowledgedCodes.has(result.code)}
            onAcknowledge={async (reason) => {
              const refreshed = await acknowledge.mutateAsync({
                invoiceId,
                code: result.code,
                reason,
              })
              setAcknowledgements((prior) => [...prior, { code: result.code, reason }])
              setReport(refreshed)
            }}
          />
        ))}

        {notes.map((result) => (
          <Finding
            key={result.code}
            result={result}
            acknowledged
            onAcknowledge={() => undefined}
          />
        ))}

        {report.results.length === 0 && (
          <p className="text-sm text-stone-500">Every check passes.</p>
        )}
      </div>

      <footer className="flex items-center gap-3 border-t border-stone-200 px-4 py-3">
        <button
          type="button"
          disabled={!report.can_issue || issuing}
          onClick={() => onIssue(report.invoice_sha256, acknowledgements)}
          className="rounded-md bg-leaf-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-leaf-800 disabled:opacity-40"
        >
          {issuing ? 'Issuing…' : 'Issue invoice'}
        </button>

        <button
          type="button"
          onClick={() => runChecks.mutateAsync(invoiceId).then(setReport)}
          className="rounded-md border border-stone-200 px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-50"
        >
          Re-run checks
        </button>

        <span className="text-xs text-stone-400">
          {report.can_issue
            ? 'The number is allocated at this point and cannot be reused.'
            : 'Resolve the blocking findings above.'}
        </span>
      </footer>
    </section>
  )
}
