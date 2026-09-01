/**
 * Receivables, delivery and reminders.
 *
 * 🔴 Money is never formatted here. Every figure arrives with a `display`
 * string already grouped the Indian way, because the grouping rule lives in
 * `api/money.py` and a second implementation in TypeScript is a second one to
 * get wrong — the two would disagree by a rupee somewhere nobody looks.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'

// ---------------------------------------------------------------------------
// Ageing
// ---------------------------------------------------------------------------

export interface AgeingRow {
  invoice_id: string
  invoice_no: string | null
  entity_code: string
  organisation_id: string | null
  buyer_name: string
  invoice_date: string
  due_date: string | null
  /** 🔴 True when there was no due date and 30 days was assumed. Say so. */
  due_date_assumed: boolean
  total_value: string
  amount_received: string
  amount_outstanding: string
  days_overdue: number
  bucket: string
  bucket_label: string
  status: string
  promised_on: string | null
  promise_note: string | null
  last_reminder_at: string | null
  reminder_count: number
  display: { total: string; outstanding: string }
}

export interface AgeingReport {
  summary: {
    as_of: string
    invoice_count: number
    total_outstanding: string
    assumed_due_dates: number
    buckets: Array<{
      bucket: string
      label: string
      count: number
      amount: string
      display: string
    }>
    display: { total_outstanding: string }
    note: string
  }
  rows: AgeingRow[]
  by_buyer: Array<{
    organisation_id: string | null
    buyer_name: string
    invoice_count: number
    total_outstanding: string
    oldest_days_overdue: number
    billing_opt_out: boolean
    billing_email: string | null
    display: { total_outstanding: string }
  }>
}

export function useAgeing(filters: Record<string, string> = {}) {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v !== '' && v != null),
  ).toString()

  return useQuery({
    queryKey: ['receivables-ageing', filters],
    queryFn: () => api<AgeingReport>(`/api/v1/receivables/ageing/${query ? `?${query}` : ''}`),
  })
}

export interface PriorityFactor {
  factor: string
  value: unknown
  points: number
  explanation: string
}

export interface Priority {
  invoice_id: string
  score: number
  band: 'low' | 'medium' | 'high'
  /** 🔴 Render these. A score whose inputs are hidden cannot be argued with. */
  factors: PriorityFactor[]
  disclaimer: string
}

export function useCollectionPriority(limit = 25) {
  return useQuery({
    queryKey: ['receivables-priority', limit],
    queryFn: () => api<Priority[]>(`/api/v1/receivables/priority/?limit=${limit}`),
  })
}

// ---------------------------------------------------------------------------
// Payment requests and promises
// ---------------------------------------------------------------------------

export interface PaymentRequest {
  id: string
  invoice_id: string
  provider: string
  provider_reference: string | null
  amount: string
  currency: string
  payload_url: string | null
  qr_svg: string | null
  status: string
  expires_at: string | null
  created_at: string
  /** 🔴 Always false. Present so a screen cannot render this as a payment. */
  is_payment: boolean
  note: string
}

export function usePaymentRequests(invoiceId: string | undefined) {
  return useQuery({
    queryKey: ['payment-requests', invoiceId],
    queryFn: () => api<PaymentRequest[]>(`/api/v1/invoices/${invoiceId}/payment-requests/`),
    enabled: Boolean(invoiceId),
  })
}

export function useCreatePaymentRequest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      invoiceId,
      ...body
    }: {
      invoiceId: string
      provider?: string
      amount?: string
      note?: string
      idempotency_key?: string
    }) =>
      api<PaymentRequest>(`/api/v1/invoices/${invoiceId}/payment-requests/`, {
        method: 'POST',
        body,
      }),
    onSuccess: (_, { invoiceId }) =>
      qc.invalidateQueries({ queryKey: ['payment-requests', invoiceId] }),
  })
}

export function useRecordPromise() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      invoiceId,
      ...body
    }: {
      invoiceId: string
      promised_on: string
      amount?: string
      note?: string
      contact_name?: string
    }) => api(`/api/v1/invoices/${invoiceId}/promises/`, { method: 'POST', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['receivables-ageing'] }),
  })
}

// ---------------------------------------------------------------------------
// Delivery
// ---------------------------------------------------------------------------

export interface DeliveryPreview {
  invoice_id: string
  channel: string
  recipient: string
  recipient_name: string | null
  subject: string | null
  body: string
  pdf_sha256: string | null
  template_version: string
  /** 🔴 Quote this back to send. Covers recipient, subject, body and the PDF. */
  preview_sha256: string
  warnings: string[]
  blocked_reason: string | null
  can_send: boolean
}

export interface Delivery {
  id: string
  invoice_id: string
  channel: string
  recipient: string
  status: string
  attempts: number
  pdf_sha256: string | null
  provider: string | null
  provider_message_id: string | null
  error_code: string | null
  error_detail: string | null
}

export function usePreviewDelivery() {
  return useMutation({
    mutationFn: ({
      invoiceId,
      ...body
    }: {
      invoiceId: string
      channel?: string
      recipient?: string
      subject?: string
      body?: string
      attach_pdf?: boolean
    }) =>
      api<DeliveryPreview>(`/api/v1/invoices/${invoiceId}/deliveries/preview/`, {
        method: 'POST',
        body,
      }),
  })
}

/**
 * 🔴 Requires the preview's hash. A send whose hash does not match the current
 * preview is refused — something changed between seeing it and confirming it.
 */
export function useSendDelivery() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      invoiceId,
      ...body
    }: {
      invoiceId: string
      preview_sha256: string
      channel?: string
      recipient?: string
      subject?: string
      body?: string
      attach_pdf?: boolean
    }) =>
      api<Delivery>(`/api/v1/invoices/${invoiceId}/deliveries/`, { method: 'POST', body }),
    onSuccess: (_, { invoiceId }) =>
      qc.invalidateQueries({ queryKey: ['deliveries', invoiceId] }),
  })
}

export interface DeliveryHistoryRow {
  id: string
  channel: string
  recipient: string
  recipient_name: string | null
  subject: string | null
  body_snapshot: string
  /** 🔴 The artifact this attempt carried, not whatever the invoice holds now. */
  pdf_sha256: string | null
  status: string
  attempts: number
  provider: string | null
  provider_message_id: string | null
  confirmed_at: string
  sent_at: string | null
  delivered_at: string | null
  failed_at: string | null
  error_code: string | null
  error_detail: string | null
  is_reminder: boolean
}

export function useDeliveryHistory(invoiceId: string | undefined) {
  return useQuery({
    queryKey: ['deliveries', invoiceId],
    queryFn: () => api<DeliveryHistoryRow[]>(`/api/v1/invoices/${invoiceId}/deliveries/`),
    enabled: Boolean(invoiceId),
  })
}
