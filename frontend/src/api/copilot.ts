/**
 * The invoice copilot, pre-issue checks and GSTIN verification.
 *
 * 🔴 Two rules this module exists to keep visible in the type system:
 *
 * 1. **No money crosses this boundary as an input.** A proposal carries a
 *    quantity and a rate; every amount comes back computed. There is
 *    deliberately no field here a client could use to send a total.
 *
 * 2. **Confirmation carries a hash.** Confirming a proposal and sending a
 *    delivery both require the digest of the thing being confirmed, so a
 *    screen cannot confirm something it did not render.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'

// ---------------------------------------------------------------------------
// Proposals
// ---------------------------------------------------------------------------

export type ProposalStatus =
  | 'pending'
  | 'confirmed'
  | 'applied'
  | 'rejected'
  | 'expired'
  | 'failed'

export interface ProposalEvidence {
  field: string
  /** `user_provided` means the person said it; anything else links a record. */
  kind: 'user_provided' | 'organisation' | 'contract_rate' | 'tax_code' | 'invoice'
  id?: string
  label: string
  review_status?: string
  citation?: { title: string; url: string | null }
}

export interface ProposalWarning {
  code: string
  severity: 'info' | 'warning' | 'error'
  message: string
  candidates?: Array<{ id: string; name: string; gstin: string | null }>
}

export interface DiffRow {
  field: string
  before: unknown
  after: unknown
}

export interface Proposal {
  id: string
  status: ProposalStatus
  action: string
  billing_entity: string
  invoice: string | null
  /** Quote this back to confirm. Hex of the stored digest. */
  proposal_sha256: string
  model: string | null
  provider: string | null
  prompt_version: string | null
  evidence: ProposalEvidence[]
  before_snapshot: Record<string, unknown>
  proposed_patch: Record<string, unknown>
  warnings: ProposalWarning[]
  missing_fields: string[]
  confidence: string | null
  expires_at: string
  created_at: string
  confirmed_at: string | null
  applied_at: string | null
  error: string | null
  diff: DiffRow[]
}

export function useCreateProposal() {
  return useMutation({
    mutationFn: (body: {
      request: string
      billing_entity: string
      invoice?: string
      action?: string
    }) => api<Proposal>('/api/v1/invoice-copilot/proposals/', { method: 'POST', body }),
  })
}

export function useConfirmProposal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, hash }: { id: string; hash: string }) =>
      api<Proposal>(`/api/v1/invoice-copilot/proposals/${id}/confirm/`, {
        method: 'POST',
        body: { proposal_sha256: hash },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['invoices'] }),
  })
}

export function useApplyProposal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api<{ proposal: Proposal; invoice: string; applied_diff: DiffRow[] }>(
        `/api/v1/invoice-copilot/proposals/${id}/apply/`,
        { method: 'POST' },
      ),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['invoice', result.invoice] })
      qc.invalidateQueries({ queryKey: ['invoices'] })
    },
  })
}

export function useRejectProposal() {
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      api<Proposal>(`/api/v1/invoice-copilot/proposals/${id}/reject/`, {
        method: 'POST',
        body: { reason },
      }),
  })
}

// ---------------------------------------------------------------------------
// Explain this total
// ---------------------------------------------------------------------------

export interface TraceLine {
  line_no: number
  description: string
  quantity: string
  unit: string
  quantity_ha: string | null
  rate: string
  rate_is_tax_inclusive: boolean
  taxable: string
  tax: string
  total: string
  explanation: string
}

export interface CalculationTrace {
  invoice_id: string
  invoice_no: string | null
  tax_treatment: string
  tax_rate_pct: string
  lines: TraceLine[]
  taxable_value: string
  tax_amount: string
  total_value: string
  amount_in_words: string
  rounding: string
  treatment_evidence: {
    supplier_state: string
    buyer_state: string | null
    suggested: string
    selected: string
    note: string
  }
  header_agrees_with_lines: boolean
}

/**
 * 🔴 Every figure here is recomputed server-side. A model may paraphrase this
 * trace; it cannot supply a replacement number, because the numbers never pass
 * through one.
 */
export function useCalculationTrace(invoiceId: string | undefined) {
  return useQuery({
    queryKey: ['invoice-trace', invoiceId],
    queryFn: () =>
      api<CalculationTrace>(`/api/v1/invoice-copilot/invoices/${invoiceId}/explain/`),
    enabled: Boolean(invoiceId),
  })
}

// ---------------------------------------------------------------------------
// Pre-issue checks
// ---------------------------------------------------------------------------

export interface CheckResult {
  code: string
  severity: 'info' | 'warning' | 'error'
  title: string
  explanation: string
  blocks_issue: boolean
  evidence: Record<string, unknown>
  /** 🔴 "not checked" is not "checked and fine". Render it distinctly. */
  not_available: boolean
}

export interface CheckReport {
  invoice_id: string
  /** Quote back when issuing, so a draft edited since is re-checked. */
  invoice_sha256: string
  can_issue: boolean
  blocking_count: number
  warning_count: number
  unacknowledged_warning_count: number
  acknowledged_codes: string[]
  results: CheckResult[]
}

export function useRunChecks() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (invoiceId: string) =>
      api<CheckReport>(`/api/v1/invoices/${invoiceId}/checks/`, { method: 'POST' }),
    onSuccess: (_, invoiceId) =>
      qc.invalidateQueries({ queryKey: ['invoice-checks', invoiceId] }),
  })
}

export function useAcknowledgeCheck() {
  return useMutation({
    mutationFn: ({
      invoiceId,
      code,
      reason,
    }: {
      invoiceId: string
      code: string
      reason: string
    }) =>
      api<CheckReport>(`/api/v1/invoices/${invoiceId}/checks/acknowledge/`, {
        method: 'POST',
        body: { code, reason },
      }),
  })
}

// ---------------------------------------------------------------------------
// GSTIN verification
// ---------------------------------------------------------------------------

export interface LocalGstinCheck {
  supplied: string
  normalised: string | null
  valid: boolean
  is_govt_uin: boolean
  state_code: string | null
  state_name: string | null
  message: string | null
  note: string
}

export interface GstinVerification {
  id: string
  gstin: string
  provider: string
  status: string
  /** 🔴 True only for an active registration. Never derive this yourself. */
  is_verified: boolean
  /** True when the provider could not be reached. Not "invalid", not "valid". */
  is_unavailable: boolean
  legal_name: string | null
  trade_name: string | null
  registration_type: string | null
  taxpayer_status: string | null
  effective_from: string | null
  cancellation_date: string | null
  principal_address: string | null
  state_code: string | null
  checked_at: string
  expires_at: string | null
  age_days: number
  error_detail: string | null
  label: string
}

export interface GstinCheckResult {
  gstin: string | null
  local: LocalGstinCheck
  live: GstinVerification | null
  differences: Array<{ field: string; crm: unknown; registry: unknown; note: string }>
  policy: string
  blocks_issue: boolean
  overridden: boolean
}

/** Layer one. Deterministic, free, and 🔴 not a verification. */
export function useLocalGstinCheck(value: string, govtUin = false) {
  return useQuery({
    queryKey: ['gstin-local', value, govtUin],
    queryFn: () =>
      api<LocalGstinCheck>(
        `/api/v1/gstin/check/?value=${encodeURIComponent(value)}&govt_uin=${govtUin}`,
      ),
    enabled: value.trim().length >= 15,
    staleTime: 5 * 60 * 1000,
  })
}

export function useVerifyGstin() {
  return useMutation({
    mutationFn: (body: {
      billing_entity: string
      gstin: string
      govt_uin?: boolean
      force?: boolean
    }) => api<GstinVerification>('/api/v1/gstin/verifications/', { method: 'POST', body }),
  })
}

export function useInvoiceGstinCheck() {
  return useMutation({
    mutationFn: ({ invoiceId, force }: { invoiceId: string; force?: boolean }) =>
      api<GstinCheckResult>(
        `/api/v1/invoices/${invoiceId}/gstin-check/${force ? '?force=true' : ''}`,
        { method: 'POST' },
      ),
  })
}

export function useApplyVerifiedDetails() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (invoiceId: string) =>
      api<{ invoice_id: string; changes: DiffRow[] }>(
        `/api/v1/invoices/${invoiceId}/gstin-check/use-verified/`,
        { method: 'POST' },
      ),
    onSuccess: (_, invoiceId) => qc.invalidateQueries({ queryKey: ['invoice', invoiceId] }),
  })
}
