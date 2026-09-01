/**
 * The invoice copilot panel.
 *
 * 🔴 **Two buttons, never chained.** "Create draft" applies a confirmed
 * proposal to an unnumbered draft. "Issue invoice" lives on the detail screen,
 * behind the pre-issue checks. A single button that did both would be the one
 * somebody clicks in a hurry, and issuing is the point of no return.
 *
 * 🔴 **Warnings render above the values.** A panel that shows the proposed
 * figures first and the contradictions underneath is a panel where somebody
 * accepts before scrolling.
 *
 * 🔴 **Every populated field shows where it came from.** A field sourced from
 * a customer record links to it; a field the user said is labelled as such.
 * The difference between a suggestion and an assertion is that the suggestion
 * shows its working.
 */

import { useState } from 'react'
import {
  type DiffRow,
  type Proposal,
  useApplyProposal,
  useConfirmProposal,
  useCreateProposal,
  useRejectProposal,
} from '../api/copilot'

interface Props {
  billingEntity: string
  /** Set when refining an existing draft; omitted when creating one. */
  invoiceId?: string
  onApplied?: (invoiceId: string, diff: DiffRow[]) => void
}

const SEVERITY_STYLE: Record<string, string> = {
  error: 'border-clay-200 bg-clay-50 text-clay-800',
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  info: 'border-stone-200 bg-stone-50 text-stone-600',
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return `${value.length} line${value.length === 1 ? '' : 's'}`
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function CopilotPanel({ billingEntity, invoiceId, onApplied }: Props) {
  const [request, setRequest] = useState('')
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)

  const create = useCreateProposal()
  const confirm = useConfirmProposal()
  const apply = useApplyProposal()
  const reject = useRejectProposal()

  const busy = create.isPending || confirm.isPending || apply.isPending

  async function propose() {
    setRefusal(null)
    setProposal(null)
    try {
      const result = await create.mutateAsync({
        request,
        billing_entity: billingEntity,
        invoice: invoiceId,
        action: invoiceId ? 'update_draft' : 'create_draft',
      })
      setProposal(result)
    } catch (error) {
      // 🔴 A refusal is not an error to be swallowed. The copilot declining to
      // issue an invoice is the trust boundary working, and the person who
      // asked should be told why rather than left wondering.
      setRefusal(error instanceof Error ? error.message : 'That request was refused.')
    }
  }

  async function createDraft() {
    if (!proposal) return
    const confirmed = await confirm.mutateAsync({
      id: proposal.id,
      // 🔴 The hash of exactly what is on screen. If the draft moved
      // underneath, the server refuses rather than applying a diff nobody saw.
      hash: proposal.proposal_sha256,
    })
    setProposal(confirmed)
    const applied = await apply.mutateAsync(proposal.id)
    setProposal(applied.proposal)
    onApplied?.(applied.invoice, applied.applied_diff)
  }

  const blocking = proposal?.warnings.filter((w) => w.severity === 'error') ?? []
  const advisories = proposal?.warnings.filter((w) => w.severity !== 'error') ?? []
  const evidenceFor = (field: string) =>
    proposal?.evidence.filter((e) => e.field === field) ?? []

  return (
    <section className="rounded-lg border border-stone-200 bg-white">
      <header className="border-b border-stone-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-stone-800">Describe the work</h2>
        <p className="mt-0.5 text-xs text-stone-500">
          Say who was served and what was done. The copilot prepares a draft from
          CRM evidence — it never issues one.
        </p>
      </header>

      <div className="space-y-3 p-4">
        <textarea
          value={request}
          onChange={(event) => setRequest(event.target.value)}
          rows={3}
          placeholder="Invoice Syngenta UP for 215 acres of drone spraying at the contracted rate, PO 1100644669"
          className="w-full rounded-md border border-stone-200 px-3 py-2 text-sm focus:border-leaf-600 focus:outline-none focus:ring-2 focus:ring-leaf-100"
        />

        <button
          type="button"
          onClick={propose}
          disabled={busy || request.trim().length < 8}
          className="rounded-md bg-leaf-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-leaf-800 disabled:opacity-40"
        >
          {create.isPending ? 'Reading…' : 'Prepare a draft'}
        </button>

        {refusal && (
          <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-sm text-clay-800">
            {refusal}
          </div>
        )}
      </div>

      {proposal && (
        <div className="space-y-4 border-t border-stone-200 p-4">
          {/* 🔴 Warnings first, above the values they qualify. */}
          {blocking.map((warning) => (
            <div
              key={warning.code}
              className={`rounded-md border px-3 py-2 text-sm ${SEVERITY_STYLE.error}`}
            >
              <strong className="font-medium">{warning.message}</strong>
              {warning.candidates && (
                <ul className="mt-1.5 space-y-0.5 text-xs">
                  {warning.candidates.map((candidate) => (
                    <li key={candidate.id}>
                      {candidate.name} — {candidate.gstin ?? 'no GSTIN on file'}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}

          {advisories.map((warning, index) => (
            <div
              key={`${warning.code}-${index}`}
              className={`rounded-md border px-3 py-2 text-sm ${
                SEVERITY_STYLE[warning.severity] ?? SEVERITY_STYLE.info
              }`}
            >
              {warning.message}
            </div>
          ))}

          {proposal.missing_fields.length > 0 && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <strong className="font-medium">Still needed:</strong>{' '}
              {proposal.missing_fields.join(', ')}.
              <p className="mt-1 text-xs">
                The copilot left these blank rather than guessing. Fill them in on
                the form.
              </p>
            </div>
          )}

          {/* The diff, with evidence per field. */}
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-stone-500">
              Proposed changes
            </h3>
            {proposal.diff.length === 0 ? (
              <p className="text-sm text-stone-500">
                Nothing to change — the copilot could not determine anything from
                that request.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-stone-200 text-left text-xs uppercase tracking-wide text-stone-500">
                    <th className="py-1.5 pr-3 font-medium">Field</th>
                    <th className="py-1.5 pr-3 font-medium">Before</th>
                    <th className="py-1.5 pr-3 font-medium">After</th>
                    <th className="py-1.5 font-medium">Because</th>
                  </tr>
                </thead>
                <tbody>
                  {proposal.diff.map((row) => (
                    <tr key={row.field} className="border-b border-stone-100 align-top">
                      <td className="py-1.5 pr-3 font-mono text-xs text-stone-700">
                        {row.field}
                      </td>
                      <td className="py-1.5 pr-3 text-stone-400">
                        {renderValue(row.before)}
                      </td>
                      <td className="py-1.5 pr-3 font-medium text-stone-800">
                        {renderValue(row.after)}
                      </td>
                      <td className="py-1.5 text-xs text-stone-500">
                        {evidenceFor(row.field).map((item, index) => (
                          <div key={index}>
                            {item.kind === 'user_provided' ? (
                              <span className="italic">you said so</span>
                            ) : (
                              <span>{item.label}</span>
                            )}
                            {item.review_status && item.review_status !== 'approved' && (
                              <span className="ml-1 rounded bg-amber-100 px-1 text-amber-800">
                                not CA-reviewed
                              </span>
                            )}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <p className="text-xs text-stone-500">
            {proposal.provider === 'fake'
              ? 'Prepared by the deterministic rule-based reader.'
              : `Prepared by ${proposal.model ?? 'a model'}`}
            {proposal.confidence && ` · confidence ${proposal.confidence}`}
            {' · '}Totals are computed by the server when the draft is created.
          </p>

          {/* 🔴 Two buttons, never chained. Issue is on the detail screen. */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={createDraft}
              disabled={busy || proposal.status === 'applied' || blocking.length > 0}
              className="rounded-md bg-leaf-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-leaf-800 disabled:opacity-40"
            >
              {proposal.status === 'applied'
                ? 'Draft created'
                : apply.isPending || confirm.isPending
                  ? 'Creating…'
                  : invoiceId
                    ? 'Apply to this draft'
                    : 'Create draft'}
            </button>

            <button
              type="button"
              onClick={() => {
                void reject.mutateAsync({ id: proposal.id, reason: 'declined in the panel' })
                setProposal(null)
              }}
              disabled={busy}
              className="rounded-md border border-stone-200 px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-50"
            >
              Discard
            </button>

            <span className="text-xs text-stone-400">
              Issuing is a separate action on the invoice.
            </span>
          </div>
        </div>
      )}
    </section>
  )
}
