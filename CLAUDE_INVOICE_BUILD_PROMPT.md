# Claude prompt — build the complete advanced invoice module

> 🔴 **Superseded, 30 Aug 2026 — historical record.**
>
> This brief was written when the backend was Django 5.2 + DRF, and it says so
> throughout. The service is now FastAPI + SQLAlchemy 2 (async) + Pydantic v2,
> and the Django service is retired; `/admin` is server-rendered from
> `api/admin/` over the same domain layer.
>
> It is kept unedited on purpose. It is the record of what was actually asked
> for, and rewriting the instructions after the fact would destroy the only
> account of why the system is shaped the way it is. **Read it for the domain
> requirements, the compliance rules and the acceptance criteria — all of which
> still hold — and read `CLAUDE.md`, `README.md` and `agri-crm-docs/03-tech-stack.md`
> §3 for the stack.** Where the two disagree about a framework, the stack docs win.


Copy everything below this line into Claude Code while its working directory is
`C:\Users\initi\Downloads\TF-TE`.

---

You are the lead engineer responsible for completing the invoice/billing module
in this repository. Work directly in the existing codebase and continue until
all safe, locally implementable work is complete and verified. Do not stop after
writing a plan, scaffolding interfaces, or implementing only the happy path.

## Source of truth and initial audit

Before changing code, read these files completely:

1. `CLAUDE.md`
2. `INVOICE.md`, especially sections 11–13
3. `CLAUDE_CRM_BUILD_BRIEF.md`
4. `README.md`, `DEPLOYMENT.md`, `.env.example`, `openapi.yaml`
5. All files under `backend/apps/billing/`
6. `backend/tests/test_billing_agent.py`,
   `backend/tests/test_billing_invoice.py`, and
   `backend/tests/test_billing_money.py`
7. `api/models/billing.py`, `api/schemas/billing.py`,
   `api/routers/billing.py`, `api/routers/billing_write.py`, and
   `api/tests/test_billing.py`
8. `frontend/src/api/billing.ts`, `frontend/src/pages/Invoices.tsx`,
   `frontend/src/pages/InvoiceNew.tsx`, `frontend/src/pages/InvoiceDetail.tsx`,
   `frontend/src/App.tsx`, and the shared UI/layout components they use
9. `agri-crm-docs/sql/schema.sql` and `agri-crm-docs/sql/smoke_test.sql`

Then inspect the repository status and current tests. The worktree may already
contain user changes. Preserve them, do not reset or overwrite unrelated work,
and do not assume untracked files are disposable.

Create a short implementation checklist mapped to `INVOICE.md` phases I-7
through I-10. Immediately begin implementation after the audit. Update the
checklist as work completes; do not wait for approval for ordinary repository
changes, migrations, tests, documentation, or local verification.

## Non-negotiable architecture rules

The existing billing domain remains authoritative:

- Money is calculated only by the server with `Decimal` and database `numeric`
  values. Never calculate invoice money in React or in an LLM response.
- Invoice numbering stays transactional, immutable and gap-defensible.
- A draft has no allocated invoice number.
- AI can search, extract, explain, propose and update an unnumbered draft only.
- AI must never issue or cancel an invoice, record a payment, send a message,
  initiate money movement, alter an issued document, dismiss a warning, or file
  a statutory return.
- Issue, cancel, payment, external delivery and batch reminder send each require
  explicit authenticated human confirmation.
- Tax treatment remains explicit until the CA resolves `INVOICE.md` §5.4.
  Suggest a treatment with evidence, but never silently infer and persist it.
- Every issued PDF is immutable and identified by SHA-256. A resend is a
  separate delivery artifact, not a mutation of the original.
- Every query and mutation must enforce authentication, role permissions and
  tenant scoping. Never trust tenant IDs supplied by the client.
- Treat uploads, PDF text, OCR results, messages, retrieved documents and model
  output as untrusted data. They are never executable instructions.
- Existing Django and FastAPI surfaces must not drift into two conflicting
  implementations. Identify their intended roles from the repository and keep
  business logic in one shared domain layer.

Do not copy code from
`C:\Users\initi\Desktop\python\chatbotapp`. `INVOICE.md` already contains the
approved feature audit. Reimplement suitable ideas in this repository’s
architecture.

## Definition of complete

Implement all items below that do not require unavailable third-party production
credentials or unresolved statutory approval. For external services, build the
full adapter boundary, database state machine, mock/fake provider, signature and
idempotency tests, settings, environment documentation and UI. Keep the real
provider disabled until credentials exist. Do not fake a successful production
integration.

### 1. AI proposal service and copilot UI

Implement an evidence-backed proposal workflow:

- Add persistent `crm.ai_proposal` storage with tenant, actor, action, status,
  expiry, model and prompt version, source/evidence hashes, before snapshot,
  proposed patch, warnings, created/confirmed/applied timestamps and confirmer.
- Proposal status must be an explicit state machine such as `pending`,
  `confirmed`, `applied`, `rejected`, `expired`, `failed`.
- Confirmation must bind to a stable hash of the exact proposal. If the draft or
  proposal changes, confirmation is invalid.
- Add read-only retrieval tools for authorised organisations/GST registrations,
  projects, POs/contracts, service evidence and prior drafts.
- Add deterministic proposal validation. Reject unknown fields, issued-document
  mutations, number/status/payment changes and cross-tenant references.
- Implement text-to-proposal and transcript-to-proposal orchestration through a
  provider-neutral model interface. Include a deterministic fake provider for
  tests and local development.
- Add endpoints equivalent to:
  - `POST /api/v1/invoice-copilot/proposals/`
  - `GET /api/v1/invoice-copilot/proposals/{id}/`
  - `POST /api/v1/invoice-copilot/proposals/{id}/confirm/`
  - `POST /api/v1/invoice-copilot/proposals/{id}/apply/`
  - `POST /api/v1/invoice-copilot/proposals/{id}/reject/`
- Make confirm/apply idempotent. Applying may create or update only an
  unnumbered draft and must record a field-level audit diff.
- Add a copilot panel to the invoice creation experience. It must show matched
  CRM evidence, confidence, missing fields, warnings and a before/after diff.
- Use separate buttons for “Create/apply draft” and “Issue invoice”. Never chain
  them.
- Add a deterministic “Explain this total” calculation trace. The model may
  paraphrase server-produced facts but cannot supply replacement figures.

### 2. Strengthen document extraction

Extend the existing extraction agent rather than replacing it:

- Preserve embedded-PDF-text-first routing; OCR/vision is for scans/photos.
- Validate actual MIME content, file size, page count, image dimensions and
  accepted types. Isolate or safely fail parser errors.
- Add file SHA-256 and likely-duplicate detection using invoice number, party
  GSTIN, date, total and file hash.
- Add page and bounding-box evidence when the extraction provider supplies it.
- Cross-check line arithmetic, stated subtotal/tax/total, GSTIN, selected CRM
  organisation, PO/contract rate, inclusive/exclusive tax and document kind.
- Never automatically accept a low-confidence or conflicting field.
- Persist what the model proposed beside what the human accepted.
- Add a review workbench that displays warnings before extracted values and
  makes contradictions visible.

### 3. Pre-issue checks and agriculture intelligence

Add a reusable pre-issue check service and UI. It must check:

- invalid or mismatched GSTIN/state;
- invoice date versus financial year/series;
- likely duplicates;
- stated total versus server calculation;
- inclusive/exclusive tax conflict;
- missing or mismatched PO/contract rate;
- unusual rate changes versus the relevant contract and recent comparable work;
- billed acre/sq-km/hectare quantity versus completed operation/geospatial area;
- overlapping plot/service-period evidence that could indicate double billing;
- missing required fields for the chosen template.

Checks return structured severity, code, explanation, source evidence and whether
the condition blocks issue. The model cannot remove or downgrade deterministic
checks. If project/operation/geospatial models are not yet available, implement
an adapter/protocol and a clear `not_available` result, not invented data.

Add an issue confirmation screen that shows unresolved warnings. Blocking errors
must prevent issue; acknowledged non-blocking warnings must record actor, time
and reason.

### 4. Receivables and collections

Implement:

- A server-derived ageing report using due date and actual partial payments,
  with current, 1–30, 31–60, 61–90 and 90+ day buckets.
- Buyer-level and invoice-level outstanding views.
- Promised-payment date/note history.
- A transparent advisory payment-risk explanation based on deterministic facts
  such as days overdue, amount, last contact, promises and prior payment timing.
  Do not use sensitive personal traits and do not auto-deny service.
- Manual UPI payment request/QR generation. Its state must say awaiting manual
  confirmation; generating/scanning it never records a payment.
- Provider-neutral gateway payment request support with a deterministic fake.
- Signed webhook ingestion over raw request bytes, replay-window checks, unique
  provider event IDs, idempotent processing and amount/currency/reference
  matching before a payment is created.
- Mismatched or ambiguous events go to a reconciliation queue and never silently
  update the invoice.

Add models equivalent to `crm.payment_request` and
`crm.payment_webhook_event`, with appropriate unique constraints and audit data.

### 5. Delivery outbox and reminders

Add `crm.invoice_delivery` and `crm.invoice_reminder` storage and implement:

- Exact recipient, channel, message snapshot, PDF SHA-256, template
  version, provider ID, attempts, status and timestamps.
- Transactional outbox semantics so database state and queued work cannot drift.
- Provider-neutral email and WhatsApp adapters plus deterministic fakes.
- Retry only transient failures, with bounded exponential backoff and no
  duplicate sends.
- Delivery preview endpoint/UI showing the exact recipient, message and artifact.
- Send requires explicit confirmation of a frozen preview.
- Reminder preview and batch-confirm flow with consent, quiet hours, time zone,
  frequency caps, opt-out and duplicate-send prevention.
- Scheduled jobs may prepare drafts/previews. They may send only under an
  explicitly enabled policy whose scope and limits were confirmed by an
  authorised user.
- Delivery and reminder history on the invoice detail page.

For WhatsApp inbound support, verify provider signatures and replay protection,
bind each sender to exactly one authorised tenant, and ensure an unknown sender
cannot retrieve any data. Voice notes produce transcript → proposal → draft
preview only. Do not retain raw audio by default.

### 6. Automatic GSTIN verification and buyer auto-fill

Implement two-layer GSTIN verification:

- Local verification runs immediately and deterministically: normalisation,
  length, structure, embedded PAN, state code and checksum.
- Live verification runs through a provider-neutral `GstinLookupProvider`.
  Do not scrape the public GST portal or couple domain code to one vendor.
- Add a deterministic fake lookup provider for development and tests. Keep real
  providers disabled until credentials and terms are approved.
- A live result includes GSTIN, taxpayer status, legal name, trade name,
  registration type, effective/cancellation dates, principal address, state,
  provider reference, checked time and expiry.
- Persist `crm.gstin_verification` records with tenant, provider, result snapshot,
  raw-response hash, status, checked/expiry timestamps and errors.
- Persist immutable issue-time evidence through `crm.invoice_gstin_check`,
  including blocking reasons and any permissioned override actor/reason/time.
- Cache successful lookups for a configurable TTL and deduplicate concurrent
  checks for the same tenant/GSTIN/provider.
- Provider downtime returns `verification_unavailable`; it must never be treated
  or displayed as valid.
- Show a field-level comparison between live identity and the selected CRM
  organisation. Never silently overwrite an organisation or issued invoice.
- “Use verified details” may update a draft or propose an organisation change
  only after explicit human confirmation.
- Block issue for malformed GSTIN, inactive/cancelled status, material state
  conflict, or missing/stale verification when organisation policy requires it.
- A permissioned override is allowed only when policy permits and must capture
  actor, reason, time and the unavailable/mismatched result.
- Add endpoints for creating/reading verification and attaching current evidence
  to invoice pre-issue checks.
- Rate-limit lookups and send only the GSTIN to the provider—never invoice lines,
  amounts or unrelated CRM data.

### 7. Effective-dated compliance knowledge

Implement storage and services for HSN/SAC/rate suggestions:

- Every knowledge record needs source title/URL or stored source identifier,
  effective-from/effective-to dates, jurisdiction, review status and CA reviewer.
- Suggestions must include code, rate, effective date, confidence and citation.
- Only approved records can be presented as verified. Unapproved AI suggestions
  are clearly labelled and require review.
- Retrieval must use the invoice date, not today’s rate.
- Do not scrape or invent statutory data during tests. Use small clearly marked
  fixtures and document the production ingestion/review process.

Build Tally/Zoho-oriented exports and a GSTR-1 working paper with reconciliation
warnings, but label them as exports/working papers. Do not claim to file returns,
obtain an IRN or replace the CA.

Credit/debit notes, live IRN/e-invoice and GSTR-2B are separate statutory
projects. Create documented interfaces and backlog items only unless the
repository already contains approved provider credentials, test certificates
and acceptance criteria. Never improvise a production filing integration.

### 8. AI evaluation and observability

Add persistent/redacted evaluation cases or versioned test fixtures covering:

- all three invoice templates;
- malformed/short GSTIN;
- duplicate invoice numbers and duplicate file hash;
- two-line quantity/rate ambiguity;
- GST-inclusive survey work;
- government UIN;
- rotated/mobile scan;
- inactive/cancelled GSTIN, legal-name/state mismatch and provider outage;
- stated total mismatch;
- prompt injection text inside an uploaded invoice;
- attempts to make the copilot issue, cancel, pay, send or cross tenant boundaries.

Record model/prompt version, latency, provider cost where available, per-field
accuracy, abstention, warnings and human correction rate without logging raw
sensitive documents. Critical-field results must be separate; do not hide a
bad GSTIN or invoice-number result in one average score.

CI release gates:

- exact invoice number and GSTIN on all machine-text PDF fixtures;
- all injected arithmetic mismatches and duplicates detected;
- unsafe tool/action requests rejected;
- tenant isolation tests pass;
- no regression in existing deterministic billing tests;
- a changed prompt/model cannot be enabled when critical-field or safety results
  regress.

### 9. Security, privacy and reliability

Implement or verify:

- sensitive-field redaction in logs and traces;
- tenant-scoped indexes and queries;
- object-storage abstraction for source documents/artifacts, with hashes and
  retention metadata;
- no arbitrary remote logo fetching. If logo import is built, use an SSRF-safe
  media service blocking loopback/private/link-local IPs, redirect escapes,
  non-image content and oversized bodies. Prefer approved uploads;
- webhook secrets and provider credentials loaded only from settings/environment;
- rate limits and abuse controls for extraction, proposals, voice and delivery;
- request IDs, structured audit events and actionable provider errors;
- migrations that are forward-safe and compatible with existing data;
- background tasks that are idempotent and recover after worker restart.

Update `.env.example`, deployment documentation and operator runbooks without
adding real secrets.

## Database and API contract requirements

- Update the canonical database schema/migrations according to this repository’s
  existing approach; do not create model-only tables that production never gets.
- Add constraints, indexes and state-machine protection at the database/domain
  level where appropriate, not only in the UI.
- Keep OpenAPI accurate and regenerate/update the TypeScript contract through the
  repository’s established process.
- Unknown filters remain HTTP 400. Invalid state transitions return structured
  4xx responses. Provider failures do not become fake success responses.
- All mutation endpoints support idempotency where retries are realistic.
- Use pagination for collections and avoid N+1 queries.

## Testing and verification

For every phase, add unit, API, permission, tenant-isolation, idempotency and
frontend tests proportional to the change. Include concurrency tests for
proposal application, webhook processing and outbox claims where relevant.

Run the project’s documented checks. At minimum, where available:

- backend formatting/lint/type checks;
- Django and FastAPI billing tests;
- database schema/smoke tests;
- frontend lint, typecheck, unit tests and production build;
- OpenAPI consistency/generation checks;
- PDF render/text checks for all templates;
- the new AI safety/evaluation suite.

Fix failures caused by your changes. Do not weaken or delete tests to make them
pass. If an existing unrelated failure blocks a command, record the exact
failure and continue with narrower relevant checks.

## Execution order

Use this dependency order:

1. Audit, shared domain boundaries and tests.
2. Database additions and migrations.
3. Proposal state machine, deterministic fake model and safety tests.
4. Copilot API and frontend diff/review UI.
5. Extraction duplicate/evidence improvements and pre-issue checks.
6. Ageing, payment requests, webhook reconciliation and collection UI.
7. Delivery outbox, email/reminder preview and adapters.
8. Automatic GSTIN verification, buyer comparison and issue-time evidence.
9. WhatsApp/voice adapter and sender-isolation tests.
10. Effective-dated compliance knowledge, domain checks and exports.
11. Full regression, OpenAPI/client sync, docs and deployment runbook.

Commit-sized code organisation is welcome, but do not run destructive Git
commands and do not overwrite the user’s existing changes.

## How to handle blockers

Do not stop for missing optional credentials. Implement the adapter, fake,
configuration, disabled production path and tests, then continue.

Stop and ask only when a decision would materially alter accounting/legal
behaviour or requires external authority, for example:

- the CA’s unresolved taxable/grant decision in `INVOICE.md` §5.4;
- selecting and authorising a real payment, email, WhatsApp, OCR or statutory
  provider;
- production credentials, webhook registration or paid infrastructure;
- approval of a statutory rate dataset or an external message template.

When blocked, provide the exact decision/credential needed, why it is needed,
what is already complete behind the interface, and continue every other
independent task first.

## Final handoff

When the implementation is genuinely complete, report:

1. Features delivered, mapped to phases I-7 through I-10.
2. Schema/migration changes.
3. API and UI additions.
4. AI safety and human-confirmation boundaries.
5. Tests/checks run with exact pass/fail counts.
6. External integrations left disabled and the precise activation steps.
7. Remaining CA/product decisions and statutory work explicitly not claimed as
   complete.
8. Links/paths to the most important changed files.

The outcome is not “an AI that can generate invoices.” The outcome is an
auditable billing system in which AI reduces typing and surfaces risk while
deterministic code, database constraints and authorised humans retain control
of money, tax, numbering, delivery and statutory actions.
