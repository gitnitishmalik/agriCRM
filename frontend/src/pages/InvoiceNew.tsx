/**
 * Raise an invoice.
 *
 * Form on the left, the actual document on the right, redrawn as you type. The
 * preview is not an approximation — it is the same template and the same
 * arithmetic the issued PDF uses, rendered by the server from unsaved form
 * data. What you see is the document.
 *
 * Two things this screen is careful about:
 *
 * * **It never computes money.** Every figure in the preview and in the totals
 *   strip comes from the server. A second implementation of Indian rounding in
 *   TypeScript is a second one to get wrong, and the two would disagree by a
 *   rupee somewhere nobody looks.
 * * **Uploading fills the form; it does not raise anything.** What comes back
 *   is a reading with warnings attached, and those warnings are shown before
 *   the numbers are, because a confident wrong reading is the failure mode
 *   that matters.
 */

import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { PageHeader } from '../layout/AppShell'
import { CopilotPanel } from '../components/CopilotPanel'
import {
  type DraftInvoice,
  type DraftLine,
  useBillingEntities,
  useCreateInvoice,
  useExtractInvoice,
  usePreview,
} from '../api/billing'

const UNITS = [
  ['acre', 'Acre'],
  ['sq_km', 'Sq. km'],
  ['hectare', 'Hectare'],
  ['each', 'Each'],
  ['lump_sum', 'Lump sum'],
  ['day', 'Day'],
  ['hour', 'Hour'],
] as const

const TAX_TREATMENTS = [
  ['igst', 'IGST — inter-state'],
  ['cgst_sgst', 'CGST + SGST — within Delhi'],
  ['exempt', 'Exempt'],
  ['zero_rated', 'Zero rated'],
  ['grant', 'Grant disbursement'],
] as const

const emptyLine = (n: number): DraftLine => ({
  line_no: n,
  description: 'Drone Spraying Services',
  hsn_sac: '998611',
  quantity: '',
  unit: 'acre',
  rate: '',
  rate_is_tax_inclusive: false,
  location_note: '',
})

const today = () => new Date().toISOString().slice(0, 10)

export function InvoiceNewPage() {
  const navigate = useNavigate()
  const { data: entities } = useBillingEntities()
  const preview = usePreview()
  const extract = useExtractInvoice()
  const create = useCreateInvoice()

  const [draft, setDraft] = useState<DraftInvoice>({
    entity_code: 'TEPL',
    template_code: '',
    invoice_date: today(),
    buyer_name: '',
    buyer_address: '',
    buyer_gstin: '',
    buyer_pan: '',
    buyer_is_govt_uin: false,
    consignee_name: '',
    consignee_address: '',
    buyer_order_no: '',
    work_order_ref: '',
    letter_ref: '',
    data_link_url: '',
    tax_treatment: 'igst',
    tax_rate_pct: '18',
    notes: '',
    lines: [emptyLine(1)],
  })

  // Declared after `draft`, not before it. `const` bindings sit in the
  // temporal dead zone until their initialiser runs, so reading `draft`
  // above this point is a ReferenceError that throws on first render and
  // blanks the page. TypeScript does not catch it — the types are fine,
  // the order is not.
  const selectedEntity = entities?.find((e) => e.code === draft.entity_code)

  const set = <K extends keyof DraftInvoice>(key: K, value: DraftInvoice[K]) =>
    setDraft((d) => ({ ...d, [key]: value }))

  const setLine = (index: number, patch: Partial<DraftLine>) =>
    setDraft((d) => ({
      ...d,
      lines: d.lines.map((line, i) => (i === index ? { ...line, ...patch } : line)),
    }))

  // Debounced preview. Every keystroke would be a round trip; a third of a
  // second after typing stops is fast enough to feel live and slow enough not
  // to hammer the renderer.
  const { mutate: runPreview } = preview
  const timer = useRef<number | undefined>(undefined)

  const schedulePreview = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const usable = draft.lines.filter((l) => l.quantity && l.rate)
      if (!draft.entity_code || !draft.invoice_date) return
      runPreview({ ...draft, lines: usable.length ? usable : [] })
    }, 350)
  }, [draft, runPreview])

  useEffect(() => {
    schedulePreview()
    return () => window.clearTimeout(timer.current)
  }, [schedulePreview])

  /** Upload a document and let the reading fill the form. */
  async function onUpload(file: File) {
    const read = await extract
      .mutateAsync({ file, entityCode: draft.entity_code })
      .catch(() => null)
    if (!read) return

    setDraft((d) => ({
      ...d,
      invoice_date: read.invoice_date || d.invoice_date,
      buyer_name: read.buyer_name ?? d.buyer_name,
      buyer_address: read.buyer_address ?? d.buyer_address,
      buyer_gstin: read.buyer_gstin ?? d.buyer_gstin,
      buyer_pan: read.buyer_pan ?? d.buyer_pan,
      consignee_name: read.consignee_name ?? d.consignee_name,
      consignee_address: read.consignee_address ?? d.consignee_address,
      buyer_order_no: read.buyer_order_no ?? d.buyer_order_no,
      work_order_ref: read.work_order_ref ?? d.work_order_ref,
      letter_ref: read.letter_ref ?? d.letter_ref,
      data_link_url: read.data_link_url ?? d.data_link_url,
      tax_rate_pct: String(read.tax_rate_pct ?? 18),
      lines: read.lines.length
        ? read.lines.map((line, i) => ({
            ...emptyLine(i + 1),
            ...line,
            quantity: line.quantity != null ? String(line.quantity) : '',
            rate: line.rate != null ? String(line.rate) : '',
            hsn_sac: line.hsn_sac ?? '',
            unit: line.unit ?? 'acre',
            location_note: line.location_note ?? '',
          }))
        : d.lines,
    }))
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    const entity = entities?.find((e) => e.code === draft.entity_code)
    if (!entity) return

    const created = await create
      .mutateAsync({
        billing_entity: entity.id,
        invoice_date: draft.invoice_date,
        template_code: draft.template_code || entity.template_code,
        buyer_name: draft.buyer_name,
        buyer_address: draft.buyer_address || null,
        buyer_gstin: draft.buyer_gstin || null,
        buyer_pan: draft.buyer_pan || null,
        buyer_is_govt_uin: draft.buyer_is_govt_uin,
        consignee_name: draft.consignee_name || null,
        consignee_address: draft.consignee_address || null,
        buyer_order_no: draft.buyer_order_no || null,
        work_order_ref: draft.work_order_ref || null,
        letter_ref: draft.letter_ref || null,
        data_link_url: draft.data_link_url || null,
        tax_treatment: draft.tax_treatment,
        tax_rate_pct: draft.tax_rate_pct,
        notes: draft.notes || null,
        lines: draft.lines
          .filter((l) => l.quantity && l.rate)
          .map((l, i) => ({
            line_no: i + 1,
            description: l.description,
            hsn_sac: l.hsn_sac || null,
            quantity: l.quantity,
            unit: l.unit,
            rate: l.rate,
            rate_is_tax_inclusive: l.rate_is_tax_inclusive,
            location_note: l.location_note || null,
          })),
      })
      .catch(() => null)

    if (created) navigate(`/invoices/${created.id}`)
  }

  const fieldErrors = create.error instanceof ApiError ? create.error.fieldErrors() : {}
  const result = preview.data

  return (
    <>
      <PageHeader
        eyebrow="Billing · New"
        title="Raise an invoice"
        description="Fill in the details and the document builds itself. Everything is calculated for you."
      />

      <form onSubmit={onSubmit} className="grid gap-6 p-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* ---------------- left: the form ---------------- */}
        <div className="space-y-4">
          {/*
            🔴 The copilot prepares a draft and stops there. "Create draft" and
            "Issue" are separate actions on separate screens — a single button
            that did both would be the one somebody clicks in a hurry, and
            issuing is the point of no return.
          */}
          {selectedEntity && (
            <CopilotPanel
              billingEntity={selectedEntity.id}
              onApplied={(invoiceId) => navigate(`/invoices/${invoiceId}`)}
            />
          )}

          <Upload
            onFile={onUpload}
            pending={extract.isPending}
            error={extract.error instanceof ApiError ? extract.error.message : null}
            warnings={extract.data?._warnings ?? []}
            needsReview={extract.data?._needs_review ?? false}
            notes={extract.data?._notes ?? null}
          />

          <Card title="Document">
            <Grid>
              <Labelled label="Company">
                <select
                  className="input"
                  value={draft.entity_code}
                  onChange={(e) => set('entity_code', e.target.value)}
                >
                  {entities?.map((entity) => (
                    <option key={entity.id} value={entity.code}>
                      {entity.code} — {entity.legal_name}
                    </option>
                  ))}
                </select>
              </Labelled>
              <Labelled label="Invoice date">
                <input
                  type="date"
                  className="input"
                  value={draft.invoice_date}
                  onChange={(e) => set('invoice_date', e.target.value)}
                  required
                />
              </Labelled>
            </Grid>
            {result && (
              <p className="mt-3 text-sm text-ink-2">
                Will be numbered{' '}
                <span className="font-mono text-ink">{result.next_invoice_no}</span> when you
                issue it. Nothing is allocated until then.
              </p>
            )}
          </Card>

          <Card title="Bill to">
            <Labelled label="Name">
              <input
                className="input"
                value={draft.buyer_name}
                onChange={(e) => set('buyer_name', e.target.value)}
                placeholder="Syngenta India Private Limited"
                required
              />
            </Labelled>
            <Labelled label="Address" className="mt-3">
              <textarea
                className="input"
                rows={2}
                value={draft.buyer_address}
                onChange={(e) => set('buyer_address', e.target.value)}
              />
            </Labelled>
            <Grid className="mt-3">
              <Labelled label="GSTIN" error={fieldErrors.buyer_gstin}>
                <input
                  className="input font-mono"
                  value={draft.buyer_gstin}
                  onChange={(e) => set('buyer_gstin', e.target.value.toUpperCase())}
                  placeholder="09AAECS9424P1ZL"
                  maxLength={15}
                />
              </Labelled>
              <Labelled label="Purchase order no.">
                <input
                  className="input font-mono"
                  value={draft.buyer_order_no}
                  onChange={(e) => set('buyer_order_no', e.target.value)}
                  placeholder="1100644669"
                />
              </Labelled>
            </Grid>
            <label className="mt-3 flex items-center gap-2 text-sm text-ink-2">
              <input
                type="checkbox"
                checked={draft.buyer_is_govt_uin}
                onChange={(e) => set('buyer_is_govt_uin', e.target.checked)}
              />
              Government department UIN — a different format from a company GSTIN
            </label>
          </Card>

          <Card
            title="Lines"
            action={
              <button
                type="button"
                className="btn-quiet text-sm"
                onClick={() =>
                  setDraft((d) => ({ ...d, lines: [...d.lines, emptyLine(d.lines.length + 1)] }))
                }
              >
                Add line
              </button>
            }
          >
            <div className="space-y-4">
              {draft.lines.map((line, index) => (
                <LineFields
                  key={index}
                  line={line}
                  index={index}
                  areaHa={result?.total_area_ha}
                  showArea={draft.lines.length === 1}
                  onChange={(patch) => setLine(index, patch)}
                  onRemove={
                    draft.lines.length > 1
                      ? () =>
                          setDraft((d) => ({
                            ...d,
                            lines: d.lines.filter((_, i) => i !== index),
                          }))
                      : undefined
                  }
                />
              ))}
            </div>
          </Card>

          <Card title="Tax">
            <Grid>
              <Labelled label="Treatment">
                <select
                  className="input"
                  value={draft.tax_treatment}
                  onChange={(e) => set('tax_treatment', e.target.value)}
                >
                  {TAX_TREATMENTS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </Labelled>
              <Labelled label="Rate %">
                <input
                  className="input font-mono"
                  value={draft.tax_rate_pct}
                  onChange={(e) => set('tax_rate_pct', e.target.value)}
                  inputMode="decimal"
                />
              </Labelled>
            </Grid>
          </Card>

          {create.error instanceof ApiError && (
            <p role="alert" className="card border-quarantine-line bg-quarantine-soft p-4 text-sm text-quarantine">
              {create.error.message}
            </p>
          )}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={create.isPending || !draft.buyer_name}
              className="btn-primary"
            >
              {create.isPending ? 'Saving' : 'Save draft'}
            </button>
            <span className="text-sm text-ink-3">
              Saved as a draft. You issue it from the invoice page, and that is what
              allocates the number.
            </span>
          </div>
        </div>

        {/* ---------------- right: the document ---------------- */}
        <div className="xl:sticky xl:top-6 xl:self-start">
          <div className="mb-3 flex items-baseline justify-between">
            <div className="label">Preview</div>
            {preview.isPending && <span className="text-xs text-ink-3">Updating…</span>}
          </div>

          {result && (
            <div className="card mb-3 p-4">
              <dl className="grid grid-cols-3 gap-3 text-center">
                <Total label="Taxable" value={result.display.taxable} />
                <Total label="Tax" value={result.display.tax} />
                <Total label="Total" value={result.display.total} strong />
              </dl>
              <p className="mt-3 border-t border-line pt-3 text-sm text-ink-2">
                {result.amount_in_words}
              </p>
              {Number(result.total_area_ha) > 0 && (
                <p className="mt-1.5 text-xs text-ink-3">
                  Area billed: {result.total_area_ha} hectares
                </p>
              )}
            </div>
          )}

          <div className="card overflow-hidden">
            {result?.html ? (
              <iframe
                title="Invoice preview"
                srcDoc={result.html}
                className="h-[900px] w-full border-0 bg-white"
              />
            ) : (
              <div className="p-10 text-center text-base text-ink-3">
                Fill in a quantity and a rate — the document appears here.
              </div>
            )}
          </div>
        </div>
      </form>
    </>
  )
}

// ---------------------------------------------------------------------------

/**
 * Upload a photo or a PDF and let the reading fill the form.
 *
 * 🔴 Warnings render above everything else and before the fields are touched.
 * A reading that invents a GSTIN is confident by construction, so the warning
 * list is the guard, not the model's own confidence score.
 */
function Upload({
  onFile,
  pending,
  error,
  warnings,
  needsReview,
  notes,
}: {
  onFile: (file: File) => void
  pending: boolean
  error: string | null
  warnings: string[]
  needsReview: boolean
  notes: string | null
}) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          const file = e.dataTransfer.files?.[0]
          if (file) onFile(file)
        }}
        className={`rounded-card border-2 border-dashed p-6 text-center transition-colors ${
          dragging ? 'border-brand bg-brand-soft' : 'border-line bg-surface'
        }`}
      >
        <input
          ref={input}
          type="file"
          accept="application/pdf,image/*"
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) onFile(file)
            e.target.value = ''
          }}
        />
        <p className="text-base text-ink">
          {pending ? 'Reading the document…' : 'Have the invoice already?'}
        </p>
        <p className="mt-1 text-sm text-ink-2">
          Drop a PDF or a photo here and the form fills itself in.
        </p>
        <button
          type="button"
          className="btn-quiet mt-3"
          disabled={pending}
          onClick={() => input.current?.click()}
        >
          {pending ? 'Reading…' : 'Choose a file'}
        </button>
      </div>

      {error && (
        <p role="alert" className="card border-quarantine-line bg-quarantine-soft p-3 text-sm text-quarantine">
          {error}
        </p>
      )}

      {warnings.length > 0 && (
        <div className="card border-gold-line bg-gold-soft p-4">
          <div className="label mb-2 text-gold">
            {needsReview ? 'Check these before issuing' : 'Worth a look'}
          </div>
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          {notes && <p className="mt-2 border-t border-gold-line pt-2 text-sm text-ink-2">{notes}</p>}
        </div>
      )}
    </div>
  )
}

function LineFields({
  line,
  index,
  areaHa,
  showArea,
  onChange,
  onRemove,
}: {
  line: DraftLine
  index: number
  areaHa?: string
  showArea?: boolean
  onChange: (patch: Partial<DraftLine>) => void
  onRemove?: () => void
}) {
  const isArea = ['acre', 'sq_km', 'hectare'].includes(line.unit)

  return (
    <div className="rounded-card border border-line p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="label">Line {index + 1}</span>
        {onRemove && (
          <button type="button" onClick={onRemove} className="text-xs text-ink-3 hover:text-quarantine">
            Remove
          </button>
        )}
      </div>

      <Labelled label="Description">
        <input
          className="input"
          value={line.description}
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </Labelled>

      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        <Labelled label="HSN/SAC">
          <input
            className="input font-mono"
            value={line.hsn_sac}
            onChange={(e) => onChange({ hsn_sac: e.target.value })}
          />
        </Labelled>
        <Labelled label="Quantity">
          <input
            className="input font-mono"
            inputMode="decimal"
            value={line.quantity}
            onChange={(e) => onChange({ quantity: e.target.value })}
          />
        </Labelled>
        <Labelled label="Unit">
          <select
            className="input"
            value={line.unit}
            onChange={(e) => onChange({ unit: e.target.value })}
          >
            {UNITS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Labelled>
        <Labelled label="Rate ₹">
          <input
            className="input font-mono"
            inputMode="decimal"
            value={line.rate}
            onChange={(e) => onChange({ rate: e.target.value })}
          />
        </Labelled>
      </div>

      <label className="mt-3 flex items-center gap-2 text-sm text-ink-2">
        <input
          type="checkbox"
          checked={line.rate_is_tax_inclusive}
          onChange={(e) => onChange({ rate_is_tax_inclusive: e.target.checked })}
        />
        This rate already includes GST
      </label>

      {/* 🔴 The hectare conversion, shown rather than hidden. Acres are what
          the contract says; hectares are what the rest of the system stores. */}
      {isArea && showArea && areaHa && Number(areaHa) > 0 && (
        <p className="mt-2 text-xs text-ink-3">= {areaHa} hectares</p>
      )}
    </div>
  )
}

function Card({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="label">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

function Grid({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`grid gap-3 sm:grid-cols-2 ${className}`}>{children}</div>
}

function Labelled({
  label,
  children,
  error,
  className = '',
}: {
  label: string
  children: React.ReactNode
  error?: string
  className?: string
}) {
  return (
    <div className={className}>
      <div className="label mb-1.5">{label}</div>
      {children}
      {error && <p className="mt-1 text-sm text-quarantine">{error}</p>}
    </div>
  )
}

function Total({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className={`mt-1 font-mono ${strong ? 'text-lg text-ink' : 'text-base text-ink-2'}`}>
        ₹{value}
      </dd>
    </div>
  )
}
