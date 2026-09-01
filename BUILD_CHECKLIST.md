# Advanced invoice module — build checklist

Scope: `CLAUDE_INVOICE_BUILD_PROMPT.md` against `INVOICE.md` phases I-7 → I-10.

🔴 **Owner decision, 29 Aug 2026: every new surface is FastAPI.** No new Django
code; new domain logic lives in `api/` only, so there is one implementation of
every rule rather than two that drift.

**Completed, 30 Aug 2026.** The Django service is retired outright. `/admin` is
server-rendered from `api/admin/` over the same domain layer, nothing installs
Django, and `backend/` is reference material. The carve-out above — "the Django
billing app stays where it is, serving Admin and the routes the frontend has
not been moved off yet" — no longer applies.

**Tenant = billing entity.** This deployment has one customer (Theta) and two
issuing companies, TFD and TEPL. Everywhere the spec says "tenant", the isolation
boundary implemented here is `crm.billing_entity`, plus the caller's role and
district scope. Stated once, here, because a scoping rule that means different
things in different files is not a scoping rule.

## 1 · Foundations
- [x] DDL: `sql/schema_invoice_advanced.sql`, idempotent, applied by `db-apply.sh`
- [x] Smoke assertions for the new invariants
- [x] SQLAlchemy models, `managed = False` in spirit: mapping only
- [x] Object-storage abstraction with hash + retention metadata
- [x] Log redaction, request ids, idempotency keys, rate limits

## 2 · Proposal service (I-7)
- [x] `crm.ai_proposal` state machine: pending → confirmed → applied / rejected / expired / failed
- [x] Confirmation bound to a proposal hash; a changed draft invalidates it
- [x] Deterministic validator: unknown fields, issued documents, number/status/payment, cross-entity
- [x] Read-only CRM retrieval tools
- [x] Provider-neutral model interface + deterministic fake
- [x] `/api/v1/invoice-copilot/proposals/` create / get / confirm / apply / reject
- [x] Deterministic calculation trace — "explain this total"

## 3 · Extraction hardening
- [x] MIME by content, size, page and pixel caps, isolated parse failure
- [x] File SHA-256 + likely-duplicate detection
- [ ] Page / bbox evidence where the provider gives it — 🔴 **not done.** `crm.invoice_extraction.evidence` and the `evidence` field on the extraction response exist and are empty. Neither configured provider returns bounding boxes today: Anthropic reads the PDF natively without emitting coordinates, and the NVIDIA text path has no page geometry at all. Populating it needs a provider that returns spans — a provider decision, not a code one.
- [x] Cross-checks: arithmetic, stated totals, GSTIN, organisation, PO rate, inclusive tax
- [x] Persist proposed beside accepted

## 4 · Pre-issue checks (I-7/I-10)
- [x] Check service returning severity, code, explanation, evidence, blocking
- [x] Agriculture adapters: operation area, geospatial area, contract rate — `not_available`, never invented
- [x] Issue confirmation records acknowledgement actor/time/reason
- [x] Blocking errors prevent issue in the domain, not only in the UI

## 5 · Receivables and collections (I-8)
- [x] Ageing from due date and real partial payments
- [x] Buyer-level and invoice-level outstanding
- [x] Promised-payment history
- [x] Advisory risk explanation from deterministic facts only
- [x] Manual UPI request + QR, "awaiting manual confirmation"
- [x] Gateway adapter + fake; signed webhook over raw bytes, replay window, idempotent
- [x] Reconciliation queue for mismatched events

## 6 · Delivery and reminders (I-8)
- [x] `crm.invoice_delivery` transactional outbox
- [x] Email + WhatsApp adapters and fakes
- [x] Bounded backoff, transient-only retry, no duplicate sends
- [x] Preview → frozen-hash confirm → send
- [x] Reminder policy: consent, quiet hours, caps, opt-out

## 7 · GSTIN verification (I-9)
- [x] Local layer already in `api/gstin.py`; live layer behind `GstinLookupProvider`
- [x] `crm.gstin_verification` cache + dedupe + TTL
- [x] `crm.invoice_gstin_check` immutable issue-time evidence
- [x] Buyer comparison; "use verified details" only after confirmation
- [x] Downtime is `verification_unavailable`, never valid

## 8 · WhatsApp inbound (I-9)
- [x] Signature + replay verification, sender bound to one entity
- [x] Unknown sender reads nothing
- [x] Voice → transcript → proposal → preview; no raw audio retained

## 9 · Compliance knowledge and exports (I-10)
- [x] Effective-dated HSN/SAC with citation and CA review status
- [x] Retrieval by invoice date, not today
- [x] Tally / Zoho exports, GSTR-1 working paper — labelled working papers

## 10 · Evaluation and gates
- [x] Fixture set covering the listed hard cases
- [x] Per-field accuracy, abstention, latency; critical fields reported separately
- [x] CI gates on invoice number, GSTIN, injected mismatches, unsafe actions, isolation

## 11 · Frontend
- [x] Copilot panel with evidence, diff, warnings
- [x] Extraction review workbench
- [x] Issue confirmation screen
- [x] Collections / ageing
- [x] Delivery + reminder history
- [x] GSTIN comparison


---

## Post-build defect fixes (30 Aug 2026)

Found by running the service rather than the tests. Each passed CI and failed
in a browser, which is the shape worth remembering: **the suite exercised the
JSON paths; the breakage lived in the paths nothing called.**

- [x] **Trailing-slash redirects** — `GET /invoices/{id}/` answered 307 to an
      absolute backend URL. Browsers strip `Authorization` across origins, so
      the retry arrived unauthenticated and the client looped on token
      refresh. It read in the log like an expiring session. Both forms are now
      registered; `test_no_api_route_redirects_on_a_trailing_slash` walks every
      route and immediately caught two more (`/healthz`, `/readyz`).
- [x] **Invoice templates were still Django** — `|default:"—"`,
      `{% extends "billing/..." %}`, `forloop.last`. Rendering raised for T1,
      T2 *and* T3; a template error surfaces at render time, not import time.
      All three are now rendered by a test.
- [x] **Preview took a field the client never sends** (`billing_entity` vs
      `entity_code`) and **returned a shape the client cannot read** (raw HTML
      vs the JSON with `.html` it destructures). Both fixed; the contract is
      asserted field-by-field.
- [x] **Half-typed lines 400'd** — a controlled input holds `""` before anyone
      types. Preview now has its own lenient line shape; **create stays
      strict**.
- [x] **Missing `pypdf` silently rerouted to vision** — the path measured
      fabricating a whole invoice. It now refuses. `pypdf` and `pillow` are
      declared.
- [x] **NVIDIA text model retired 2026-08-26** (HTTP 410, nothing changed
      locally). Default is now `openai/gpt-oss-20b` — what INVOICE.md measured
      as exact — and a 410 names the model rather than saying "fill the form by
      hand".

🔴 **Worth watching:** the whole Llama family appears to have been end-of-lifed
on the same date. The vision models still answer today; pick successors before
they do not.
