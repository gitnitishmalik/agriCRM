# INVOICE · Billing Module — Specification & Build Plan

**Owner:** Nitish Malik · **Org:** Theta Analytics · **Drafted:** 26 Aug 2026

**Status:** Working end to end, and the advanced module (§12–13) is built.
Open `/invoices` in the app: describe the work in a sentence or upload an
existing invoice, review the evidence-backed draft, run the pre-issue checks,
issue it. `/admin` is the data-operations console.

🔴 **Two things remain open and neither is an engineering task.** §5.4 — the
CA's answer on taxable supply versus grant — still gates any tax logic, and
every external provider (GSP, payment gateway, email, WhatsApp, speech-to-text)
is built behind an adapter with a deterministic fake and stays disabled until
credentials and a data-processing agreement exist. See `api/README.md`.

| Phase | | |
|---|---|---|
| I-0 · Decisions | ⏳ | 🔴 Blocked on the CA — §5.4 |
| I-1 · Schema and register | ✅ | 5 tables, 3 triggers, 5 smoke assertions |
| I-2 · Admin console | ✅ | FastAPI console at `/admin`, incl. collected data + provenance |
| I-3 · PDF generation | ✅ | T1, T2, T3 matching the originals |
| I-4a · Extraction agent | ✅ | Upload a PDF or photo, get a filled draft |
| I-5 · React screens | ✅ | Register, live-preview create form, detail |
| I-6 · Accounting handoff | ✅ | Tally / Zoho export + GSTR-1 working paper |
| I-7 · AI invoice copilot | ✅ | Proposal state machine, evidence, diff, calculation trace |
| I-8 · Collections copilot | ✅ | Ageing, UPI/gateway requests, webhooks, delivery outbox |
| I-9 · Automatic GSTIN verification | ✅ | Two layers, cache, issue-time evidence, WhatsApp inbound |
| I-10 · Compliance intelligence | ✅ | Dated HSN knowledge, pre-issue checks, CA review |
| I-4b · History import | ☐ | The ~180 documents and the master sheet |

> Fill in a form, get your invoice. That is the whole product goal. Everything
> below exists to make that one action correct, repeatable and auditable.

---

## 1. What this module is, and what it is not

Doc 01 §1 lists *"Accounting/invoicing (integrate with Tally/Zoho Books later)"*
as out of scope for v1, and CLAUDE.md treats that list as a contract. This
module amends that contract **narrowly and deliberately**, because "invoicing"
turned out to name two different jobs.

| | In this module | Deliberately not here |
|---|---|---|
| **Document** | Capture the details, generate the PDF in your own template, re-generate it identically forever | — |
| **Register** | Who was billed, for which project, how many acres, booked / on-hold / cancelled, paid or outstanding | — |
| **Arithmetic** | Line amount, tax, total, amount in words | — |
| **Statutory filing** | — | 🔴 e-invoice IRN + QR from the NIC portal, GSTR-1 / GSTR-3B, ledgers, TDS, credit and debit notes as statutory documents, bank reconciliation |

**Why the line sits there.** Generating a document you already generate by hand
in Word carries no new risk — it removes the typing errors catalogued in §3.
Filing carries penalty risk, is a solved problem your CA already runs in Tally,
and would make this codebase responsible for tax law. The register **exports to**
Tally / Zoho Books; it never replaces them.

**Why it belongs in a CRM at all.** Two of your customers — DCM Shriram and
Triveni Engineering — are sugar mills, the exact organisation type
`core.organisation` already models. Billing joined to the registry answers
questions accounting software structurally cannot: what the Syngenta
relationship is worth across four states, how many acres were sprayed in UP this
season, which mills are overdue, and — once Phase 5 lands — whether the area you
billed for matches the area satellite imagery says you covered.

---

## 2. What you issue today

Read from `6. Invoice-2024-2025/` on 26 Aug 2026: ~180 documents and one master
spreadsheet, covering FY 2024-25 through FY 2026-27.

### 2.1 Two billing entities

| | Theta Foundation for Development | Theta Enerlytics Private Limited |
|---|---|---|
| Short code | `TFD` | `TEPL` |
| GSTIN | `07AAICT8535C1Z9` | `07AAHCT0066D1ZM` |
| PAN | `AAICT8535C` | — |
| State | Delhi, code 07 | Delhi, code 07 |
| Address | L-20 Lower Basement, Green Park, New Delhi 110016 | A 10/3 Front Ground Floor, Vasant Vihar, New Delhi 110057 |
| Bank | ICICI, Greater Kailash · A/c *not in the repo* · `ICIC0000029` | ICICI, East of Kailash · A/c *not in the repo* · `ICIC0000719` (moved from Axis during FY26) |
| Signatory | *not in the repo*, Director | Authorised Signatory |
| Contact | *not in the repo* | *not in the repo* |

🔴 **The account numbers, the contact mobile and the signatory names are
deliberately absent.** This repository is public. An account number sitting
beside the company's real GSTIN and letterhead is everything an attacker needs
to send a customer a convincing invoice pointing at a different account, and
the mobile and the names are personal data under DPDP besides. They are read
from the environment — see the `backend/seeds.py` docstring and the keys in
`.env.example`. Seeding without them works and renders a visibly unpayable
`XXXXXXXXXXXX`, which is the intended state for anyone outside the company.

🔴 **The bank moved mid-year.** Older TEPL invoices carry Axis details. A
re-generated historical invoice must reproduce the bank block *as it was on the
invoice date*, not as it is today. This is why §4.1 versions the entity record
rather than storing one current address.

### 2.2 Two service lines

| Service | HSN/SAC | Unit | Observed rate | Tax |
|---|---|---|---|---|
| Drone spraying | `998611` | acre | ₹100 – ₹150 / acre | IGST 18% added |
| Drone base-map / survey | `997319` | sq km | ₹32,000 / sq km | quoted **inclusive** of GST |

🔴 **Two different tax presentations.** Spraying is quoted ex-tax with IGST added
below the line. The Mizoram survey work is quoted GST-inclusive, and the sheet
records IGST as `0` against a total that already contains it. The model must
carry `rate_is_tax_inclusive` per line, or the register will overstate revenue.

### 2.3 Customers seen

Syngenta India Pvt Ltd (a separate GSTIN per state — UP 09, Maharashtra 27,
Karnataka 29), **DCM Shriram Ltd**, **Triveni Engineering & Industries Ltd**,
Director — Department of Agriculture & Farmers Welfare, Government of Mizoram,
General Aeronautics Pvt Ltd, FITT IIT Delhi, Ubifly Technologies (Amber Wings).

### 2.4 Three document templates

| # | Template | Used by | Shape |
|---|---|---|---|
| T1 | TFD Tax Invoice | TFD, spraying | Simple block layout. Header, Bill To, line table, G. Total → IGST → Total, words, bank, signatory. |
| T2 | TEPL Tax Invoice | TEPL, spraying | Tally-style 8-column grid with Delivery Note / Mode of Payment / Buyer's Order No. fields, most left blank. |
| T3 | TEPL Survey Invoice | TEPL, Mizoram | T2 plus a Consignee (Ship to) block, Work Order and Letter Reference numbers, a long service description, and a Google Drive data link. |

T2 and T3 share a skeleton; T3 adds blocks. Build T2 first, derive T3, then T1.

---

## 3. 🔴 Four defects in the current data

Found by machine-reading the master sheet. Each maps to a constraint the module
must enforce so it cannot recur.

| # | Defect | Extent | Constraint that prevents it |
|---|---|---|---|
| D1 | GSTIN one character short | 29 of 105 lines | §5.3 format + checksum validation, blocking on save |
| D2 | Same GSTIN recorded two ways for one customer | Syngenta UP as `09AAECS9424P1ZL` (16×) and `09AAECS942P1ZL` (15×); Triveni as `09AABCT6370L1ZW` and `9AABCT6370L1ZW` | GSTIN lives on `core.organisation`, entered once, never retyped per invoice |
| D3 | Invoice number reused | `TEPL/2026-27/03`, `/04` (cancel 18 Jun → reissue 14 Jul under the same number), `2025-26/10`, FY24 `15` | §4.2 unique constraint per series; a cancelled number is burned, never reissued |
| D4 | Two number formats in one series | `TFD/25-26/9` alongside `2025-26/9` | Numbers are generated, never typed |

Additionally: `9,29.200` in the FY24 sheet should read `9,29,200`, and one
amount-in-words reads "ninteen". Both disappear once the number and the words
are computed rather than typed.

**On D1, the practical consequence:** a wrong GSTIN on a filed invoice blocks
your customer's input tax credit and surfaces as a GSTR-2B mismatch on their
side. It is worth correcting the historical records regardless of this build.

---

## 4. Data model

New tables in the `crm` schema, applied through `sql/schema.sql` like everything
else. 🔴 Do not hand-edit that file without re-running `sql/smoke_test.sql`.

### 4.1 `crm.billing_entity`

Who is issuing. Two rows today. Versioned by `valid_from` / `valid_to` so a
re-generated 2025 invoice reproduces the 2025 bank details.

| Field | Notes |
|---|---|
| `code` | `TFD`, `TEPL` |
| `legal_name`, `address`, `state_code` | As printed |
| `gstin`, `pan` | Validated per §5.3 |
| `bank_name`, `bank_account_no`, `bank_ifsc`, `bank_branch` | The block printed on the document |
| `signatory_name`, `signatory_title` | |
| `default_template` | `T1` \| `T2` \| `T3` |
| `valid_from`, `valid_to` | 🔴 Never update in place. Close the row, open a new one. |

### 4.2 `crm.invoice_number_series`

| Field | Notes |
|---|---|
| `billing_entity_id`, `financial_year` | FY runs 1 April – 31 March; `2026-27` |
| `stream` | Optional. Mizoram uses `M`, giving `TEPL/2026-27/M/1` |
| `pattern` | e.g. `{entity}/{fy_short}/{stream}{n}` |
| `next_number` | Allocated inside the insert transaction |

🔴 **Gapless and immutable.** A number is allocated once. Cancelling an invoice
keeps its number and marks it cancelled — it is never handed to another
document. That is what makes the series defensible in an audit, and it is
exactly what D3 got wrong.

### 4.3 `crm.invoice`

| Field | Notes |
|---|---|
| `invoice_no` | Generated. Unique per entity per FY. |
| `billing_entity_id` | |
| `organisation_id` → `core.organisation` | 🔴 The join that makes this a CRM feature |
| `project_id` → `crm.project` | Nullable; set once Phase 3 lands |
| `invoice_date`, `place_of_supply_state_code` | |
| `buyer_gstin`, `buyer_name`, `buyer_address` | **Snapshotted at issue.** A customer that later changes address must not silently alter a filed document. |
| `consignee_*` | T3 only |
| `buyer_order_no` | Syngenta PO, e.g. `1100644669` |
| `work_order_ref`, `letter_ref` | Mizoram |
| `tax_treatment` | `igst` \| `cgst_sgst` \| `zero_rated` \| `exempt` — see §5.4 |
| `taxable_value`, `tax_amount`, `total_value` | `numeric(14,2)` INR |
| `amount_in_words` | Generated, Indian numbering |
| `status` | §4.6 |
| `cancelled_at`, `cancellation_reason` | 🔴 Reason required when cancelling |
| `hold_reason`, `held_at` | Your sheet already uses `ON-HOLD-10-08-26` |
| `pdf_path`, `pdf_generated_at`, `pdf_sha256` | §6.3 |
| `data_link_url` | Mizoram deliverable |
| `source_id` → `dq.source`, `created_by`, `updated_by` | Provenance, as everywhere else |

### 4.4 `crm.invoice_line`

| Field | Notes |
|---|---|
| `line_no`, `description`, `hsn_sac` | |
| `quantity`, `unit` | `acre` \| `sq_km` \| `hectare` \| `each` |
| `quantity_ha` | 🔴 **Generated.** Every area also stored in hectares per the project-wide rule. Acres and sq km are input conveniences; the analysable column is hectares. |
| `rate`, `rate_unit`, `rate_is_tax_inclusive` | §2.2 |
| `tax_rate_pct` | 18 today |
| `line_taxable_value`, `line_tax_amount`, `line_total` | |
| `district_id` → `ref.district` | Optional. Unlocks "acres sprayed per district". |

### 4.5 `crm.invoice_payment`

Many payments to one invoice — the Mizoram sheet already tracks partial
receipts.

| Field | Notes |
|---|---|
| `invoice_id`, `received_on`, `amount`, `reference`, `mode` | |
| `note` | |

Outstanding is derived, never stored.

### 4.6 Status lifecycle

```
draft ──▶ issued ──▶ part_paid ──▶ paid
  │         │  │
  │         │  └──▶ on_hold ──▶ issued
  │         └─────▶ cancelled          (reason required, number burned)
  └───────────────▶ discarded          (draft only, never numbered)
```

A `draft` has no number and no PDF. Numbering happens at `issued` and is the
point of no return.

---

## 5. Calculation and validation rules

### 5.1 Line arithmetic

- Tax-exclusive: `line_taxable_value = quantity × rate`, `line_tax_amount = taxable × tax_rate_pct / 100`
- Tax-inclusive: `line_taxable_value = line_total × 100 / (100 + tax_rate_pct)`, remainder is tax
- Round to 2 decimals **per line**, then sum. Rounding the total instead produces
  totals that disagree with the line table by a rupee.

### 5.2 Amount in words

Indian numbering — lakh and crore, not million. `6,45,519 → "Six lakh forty five
thousand five hundred nineteen only"`. Generated, never typed.

### 5.3 GSTIN validation

15 characters: 2-digit state code, 10-character PAN, 1 entity digit, `Z`, 1
check character. Validation has two layers:

1. **Instant local verification** — normalise to uppercase, validate length,
   structure, state code and checksum without a network call. A malformed GSTIN
   is rejected immediately.
2. **Live registry verification** — query an approved GST/GSP provider and verify
   active status, legal name, trade name, registration type, principal address
   and state. The returned identity is shown before it can populate the buyer.

A checksum-valid GSTIN is not necessarily active; the UI must never label the
local result as “GST-verified”. Cache a live response with its provider,
verification time and expiry, but provide **Verify again** before issue. A
government department UIN (Mizoram's `15SHLD02015GIDQ`) does not follow the PAN
pattern — route it through an explicit UIN path rather than weakening GSTIN
validation.

### 5.4 🔴 Open question — tax treatment by customer

The historical data is not consistent and I will not guess at it:

- Syngenta invoices carry **IGST 18%**.
- Triveni and DCM Shriram invoices show tax **0** against a non-zero total.
- The FY24 sheet heads its amount column **"Grant Amount"**, not "Amount".

That suggests some TFD billing is grant disbursement rather than taxable supply
— a materially different thing. **This needs your CA's answer before Phase 2
writes any tax logic.** Until then `tax_treatment` is captured explicitly per
invoice and never inferred.

### 5.5 Place of supply

Both entities are Delhi (07). For services to a registered person, place of
supply is the recipient's location, so a buyer GSTIN outside 07 gives IGST and a
Delhi buyer gives CGST + SGST. Derive the suggestion from the buyer GSTIN,
**show it, and let the user override** — with the override recorded.

---

## 6. Screens

### 6.1 Create invoice — the form

One page, in this order. Everything below the buyer block is prefilled from the
customer and the service line, and is editable.

1. **Entity** — TFD or TEPL. Sets template, bank block and number series.
2. **Customer** — search `core.organisation`. GSTIN, address and state fill in.
   A customer with no GSTIN on file prompts to add it to the registry rather
   than typing it onto the invoice (this is what closes D2).
3. **Project / PO** — optional link, plus Buyer's Order No.
4. **Lines** — service, HSN, quantity, unit, rate, inclusive toggle. Amounts and
   tax compute live. Hectares shown beside acres, greyed, so the conversion is
   visible rather than hidden.
5. **Totals** — taxable, tax, total, and the words, all read-only.
6. **Preview** — the actual PDF, not an HTML approximation.
7. **Save draft** or **Issue** — Issue allocates the number and freezes the
   snapshot fields.

### 6.2 Invoice register

Columns: number, date, entity, customer, taxable, tax, total, status, paid,
outstanding, age. Filters: entity, FY, customer, status, district, service.
Totals row. CSV and Tally-shaped export.

An unknown filter is a **400**, per the project-wide rule.

### 6.3 Detail

Header, lines, payments, the generated PDF, and a full change history. The PDF
is stored with its SHA-256 so you can prove the document you sent is the
document you hold.

---

## 7. Importing the history

~180 documents and the master sheet. The importer is a `dq.source` like any
other and writes `dq.field_provenance` for every field it extracts.

- **The spreadsheet is the spine** — it has the numbers, dates, parties and
  statuses in structured form.
- **PDFs and DOCXs enrich it** — line descriptions, quantities, rates, PO and
  work-order references.
- **Mismatches raise `dq.contradiction`**, they do not overwrite. A sheet total
  that disagrees with the PDF total is a question for a human, and there are
  already several.
- Imported rows are `locked` — regenerating a PDF for a historical invoice
  reproduces the original document, it does not re-render it in today's template.

---

## 8. Build phases

Each phase ends when its exit gate passes, not when its days run out.

### Phase I-0 · Decisions — ½ day, blocking

**Tasks**

- 🔴 CA answers §5.4: is TFD-to-mill billing a taxable supply or a grant?
- Confirm the three templates are current, and that TEPL has finished moving to ICICI.
- Confirm whether cancelled numbers may be reused (§4.2 assumes **no**).

**Exit gate**

- [ ] Tax treatment documented per customer, in writing
- [ ] One approved reference PDF per template, agreed as the target output

---

### Phase I-1 · Schema and register — 2 days ✅ built

**Where it landed:** `sql/schema.sql` (tables, enums, triggers),
`sql/smoke_test.sql` tests 16–20, `backend/apps/billing/models.py`,
`backend/apps/billing/money.py`, `backend/apps/billing/gstin.py`.
Seed the two companies with `python manage.py seed_billing_entities`.


**Tasks**

- `crm.billing_entity`, `invoice_number_series`, `invoice`, `invoice_line`, `invoice_payment` in `sql/schema.sql`
- Smoke-test assertions: number allocation is gapless under concurrency; a cancelled number is never reissued; `quantity_ha` derives correctly from acres and sq km; tax-inclusive back-calculation is exact
- SQLAlchemy models in `api/models/billing.py` mapping the DDL — `__table_args__ = {"schema": "crm"}`, never `create_all()`
- Seed the two billing entities with their real, current values

**Exit gate**

- [ ] `make smoke` green including the new assertions
- [ ] Two concurrent issues cannot take the same number
- [ ] 100 acres stores as 40.4686 ha; 65.7 sq km as 6,570 ha

---

### Phase I-2 · The data-ops console — 2 days ✅ built

**Where it landed:** originally `backend/apps/billing/admin.py`; now
`api/admin/billing_views.py` with the API in `api/routers/billing*.py`. The
console was rebuilt during the FastAPI migration and is deliberately narrower
than the Django Admin it replaced — see the note at the end of this phase. Issue and Cancel are buttons at the
top of the invoice page; cancelling opens a form that will not submit without a
reason.


**Tasks**

- Admin for all five tables, with inlines for lines and payments
- Number allocation on transition to `issued`
- GSTIN validator (§5.3) wired into the form
- Cancel and hold actions, each requiring a reason
- Register changelist with the §6.2 filters and a totals row

**Exit gate**

- [ ] An invoice can be created end to end in Admin
- [ ] A malformed GSTIN is rejected with a useful message
- [ ] A cancelled invoice keeps its number and the next issue skips it

---

### Phase I-3 · PDF generation — 3 days ✅ built

**Where it landed:** `backend/apps/billing/render.py` and
`templates/billing/invoice_{base,t1,t2,t3}.html`.

⚠️ **PDF output needs an engine that Windows does not ship.** The HTML preview
works everywhere with no native dependencies; turning it into a PDF needs
WeasyPrint (Linux, CI, production — `pip install weasyprint`) or Playwright
(Windows — `pip install playwright && playwright install chromium`). With
neither installed the preview still renders and `render_pdf` raises an error
naming the fix rather than failing obscurely.


**Tasks**

- Template T2 (TEPL spray) as HTML + CSS, rendered by WeasyPrint
- Derive T3 (Mizoram survey) — consignee block, work order refs, data link
- T1 (TFD)
- Amount-in-words in Indian numbering, with tests to ten crore
- Store PDF with SHA-256; regeneration is byte-identical for the same input

**Exit gate**

- [ ] 🔴 A generated PDF placed beside its hand-made original is indistinguishable on every printed field
- [ ] Regenerating an invoice twice produces the same hash
- [ ] The words match the figure on 20 sampled historical invoices

---

### Phase I-4a · Extraction agent ✅ built

**Where it landed:** `backend/apps/billing/agent.py`, exposed at
`POST /api/v1/invoices/extract/`. Upload a photo or a PDF; the model reads it
and returns the create form filled in, with a confidence per field and a list
of warnings. Without a key the endpoint refuses cleanly and the form is typed
by hand.

🔴 It fills a draft and never issues. Arithmetic is recomputed from quantity
and rate rather than trusted, and a stated total that disagrees becomes a
warning for a human.

#### Choosing a provider

`INVOICE_EXTRACTION_PROVIDER` is `anthropic` or `nvidia`. Both work. Which
*path* runs inside the provider matters more than which provider you pick.

🔴 **Read the text layer, do not look at a picture of it.** Every invoice this
business generates is a computer-generated PDF carrying its own text.
Rasterising one and asking a vision model to squint at it throws away a
perfect transcript and then asks a model to reconstruct it — and the
reconstruction is where the errors come from. Measured on a real TEPL invoice
(`08-Invoice-Sygenta-215-Acres-UP-ONLINE.pdf`) on 26 Aug 2026:

| Path | Model | Time | Result |
|---|---|---|---|
| **Text layer** | `openai/gpt-oss-20b` | **5–10s** | ✅ **Every field exact** |
| Text layer | `openai/gpt-oss-120b` | 7s | ✅ Every field exact |
| Rasterised | `llama-3.2-90b-vision` | 158s | Every field **null** |
| Rasterised | `llama-3.2-11b-vision` | 14s | 🔴 Every field **fabricated** — invoice no. "12345" against an actual TEPL/2026-27/08, GSTIN "27AAXYZ1234P" against 09AAECS9424P1ZL, quantity 10 against 215 |

The 11B result is the one to remember: it did not fail, it confidently
produced a complete fictional invoice. Vision is therefore the **fallback for
photos and scans only** — `_pdf_text` takes the text path whenever a PDF has
more than 200 characters of extractable text, and a vision reading always gets
an unconditional review warning appended.

Verified end to end on three real invoices, all zero warnings: the TEPL spray
invoice, the two-line TFD invoice, and the Mizoram survey invoice (including
correctly detecting its GST-inclusive rate).

**What the NVIDIA path costs, structurally**

* **No enforced tool schema.** The reply is prose that should contain JSON.
  `_json_from_text` scans for the *last* balanced object whose `lines` key is
  a list — testing for the key alone picks the `confidence` block, which also
  has a "lines" key, and reports `0.99` as the invoice number. Roughly one
  reply in several is unparseable, so there is exactly one stricter retry.
* **`_normalise` forces the reply into shape.** A real reply came back with
  `lines` as a bare float. Nothing here invents data; a value that will not
  coerce is dropped and the missing-field warnings say so.
* **A 32,768-token context** on the vision models. `_fit_image` downscales
  until the payload fits — measured, not guessed: a 180KB payload of that
  invoice billed 32,436 message tokens and was rejected outright.

**Two prompt rules earn their place**, both from observed failures:

1. *Do not swap quantity and rate.* A two-line invoice came back as
   "150 acre @ 2301". The product is identical either way, so **no arithmetic
   check can catch it** — only the prompt can.
2. *Set `rate_is_tax_inclusive` when the document says so.* Missing it on the
   Mizoram rate overstates revenue by the tax fraction.

---

### Phase I-4b · History import — 2 days

**Tasks**

- Spreadsheet importer, all five sheets
- PDF and DOCX text extraction for line detail
- Provenance for every field; contradictions instead of overwrites
- Dry-run report before commit, per the standard import pattern

**Exit gate**

- [ ] All FY24–FY26 invoices in the register, reconciling to the sheet totals
- [ ] Every D1–D4 defect surfaced as a contradiction, none silently corrected
- [ ] A historical invoice regenerates as its original document

---

### Phase I-5 · React screens — 3 days ✅ built

**Where it landed:** `frontend/src/pages/Invoices.tsx` (the register),
`InvoiceNew.tsx` (create, with the live preview and the upload panel),
`InvoiceDetail.tsx`, and `frontend/src/api/billing.ts`.

🔴 **The client never computes money.** Every figure it shows comes from the
server pre-grouped the Indian way. A second implementation of the rounding and
grouping rules in TypeScript is a second one to get wrong, and the two would
disagree by a rupee somewhere nobody looks.


**Tasks**

- The §6.1 create form, live totals, live PDF preview
- The §6.2 register on AG Grid
- The §6.3 detail with payments and history
- Generated TypeScript client from the regenerated `openapi.yaml`

**Exit gate**

- [ ] An invoice raised in the browser, start to finish, without touching Admin
- [ ] `npm run build` and `tsc --noEmit` clean; `openapi.yaml` diff reviewed

---

### Phase I-6 · Accounting handoff — 1 day

**Tasks**

- Tally-shaped and Zoho-shaped CSV export
- GSTR-1 B2B working sheet — **a working sheet for your CA, not a filing**
- Outstanding / ageing report

**Exit gate**

- [ ] Your CA accepts one month's export without re-keying
- [ ] 🔴 Nothing in the codebase claims to file a return

**Total: ~13 working days**, plus the Phase I-0 decisions, which are the only
external dependency and should start now.

---

## 9. What this deliberately does not do

The boundary is now **automation without autonomous accounting**. This module
may prepare drafts, exports, reconciliations, reminders and evidence for review;
it does not file a return, make a legal tax determination, post to the ledger,
or move money without an explicit human action. No autonomous e-invoice IRN,
GSTR filing, TDS filing, credit/debit note issuance, bank write-back or payment
initiation. Integrations added below must use sandbox/test mode first and keep
the CA or authorised operator as the final approver.

---

## 10. Sequencing against the main plan

This module is independent of Phases 2–5 and can be built alongside Phase 1
sprints 3–6. It has one soft dependency: `crm.project` does not exist until
Phase 3, so `invoice.project_id` stays nullable and gets backfilled then.

The one hard dependency runs the other way — invoicing needs
`core.organisation` to hold your customers, which exists now. **Load your eight
customers into the registry first.** That is also what fixes D2 permanently.

---

## 11. Invoice-system audit imported from `chatbotapp`

Reviewed on 29 Aug 2026 from
`C:\Users\initi\Desktop\python\chatbotapp`. The useful reference implementation
is spread across `backend/invoice/`, `backend/payments.py`, `backend/gstr.py`,
`backend/emailer.py`, `backend/whatsapp.py`, `backend/agent/`, the three Jinja
invoice templates, and the invoice/assistant/insights screens.

### 11.1 What the reference app actually has

| Capability in `chatbotapp` | Decision for this project |
|---|---|
| Deterministic GST calculation, GSTIN checksum, intra/inter-state split, HSN/SAC shape checks | **Keep our engine as authority.** Port only missing golden test vectors; do not introduce a second calculator. |
| Vision/PDF extraction followed by arithmetic and GSTIN checks | **Already stronger here.** Keep text-layer-first extraction, field confidence, warnings and `InvoiceExtraction` provenance. Add duplicate detection and evals. |
| Natural-language tool-calling agent that builds, saves, lists and updates invoices | **Adopt as a constrained copilot.** It may read, propose and create drafts; issue/cancel/payment remain explicit UI confirmations. |
| WhatsApp text and voice-note invoicing using speech-to-text | **Adopt after identity and consent controls.** Route every sender to one tenant and return a draft preview, never an issued invoice. |
| UPI deep link and QR generated from invoice number and total | **Adopt first as a convenience link.** Label manual UPI as “awaiting confirmation”; add gateway webhook reconciliation later. |
| Email delivery and WhatsApp messaging | **Adopt through an outbox.** Store recipient, template, provider id, attempts, delivery status and the exact PDF hash sent. |
| GSTR-1 JSON and CA multi-client views | **Adopt as CA working papers/export only.** Never label portal-shaped output as filed or filing-ready without schema validation and CA approval. |
| Website-logo discovery and data-URI branding | **Do not copy directly.** Remote fetch needs SSRF protection, DNS/IP checks, content verification, size limits and object storage. Prefer an uploaded, approved logo. |
| Recurring invoice definitions and scheduled generation | **Adopt as recurring drafts.** A schedule creates a reviewable draft and never consumes an invoice number. |
| Insights, unpaid list and daily digest concepts | **Adopt on top of real payment rows and due dates.** The source’s paid/unpaid status is too coarse for this project’s partial-payment model. |

### 11.2 What must not be copied wholesale

- Do not replace `Decimal`/database `numeric` values with floating-point money.
- Do not let the model supply totals, tax split, invoice number, payment status,
  entity bank details or immutable issued fields.
- Do not infer a tax treatment merely because seller and buyer state codes differ.
  The deterministic engine can calculate a selected treatment; the user/CA owns
  the selection until §5.4 is resolved.
- Do not fetch arbitrary customer URLs from a worker without an SSRF-safe media
  service. Block loopback, private/link-local networks, redirects to blocked
  hosts, non-image bodies and oversized responses.
- Do not expose a public webhook without signature verification, replay
  protection, idempotency keys and a durable queue.
- Do not claim a generated UPI URI confirms payment. Only a verified payment
  provider webhook or a human-entered receipt creates `invoice_payment`.

---

## 12. Advanced AI invoice product

### 12.1 Product promise

> Tell the copilot who was served and what work was completed. It prepares the
> correct draft from CRM evidence, explains every warning, and waits for a human
> to issue it.

The AI is an interface and reviewer around the billing domain—not the billing
domain itself. Database constraints, `money.py`, `gstin.py`, the invoice model
and the render pipeline remain the system of record.

### 12.2 Trust boundary

| AI may | AI may not |
|---|---|
| Search organisations, projects, contracts and prior invoices the user is authorised to see | Allocate or choose an invoice number |
| Suggest buyer, project, PO, service line, HSN/SAC, place of supply and payment terms with evidence | Silently decide tax treatment, exemption, reverse charge or statutory eligibility |
| Extract an uploaded document and propose corrections | Overwrite an issued/cancelled invoice or its stored PDF |
| Create or update an unnumbered draft after showing a field-level diff | Issue, cancel, record a payment, send a reminder or file/export to an external system without confirmation |
| Explain deterministic calculations and validation errors | Recalculate money in model output or conceal a mismatch |

Every proposed mutation carries `proposal_id`, `actor_id`, source evidence,
model/prompt version, before/after JSON, warnings and expiry. Confirmation is
bound to the exact proposal hash; if the draft changes, confirmation is asked
again.

### 12.3 Copilot experiences

#### A. Text or voice to draft

Example: “Invoice Syngenta UP for 215 acres at the contracted spraying rate.”

1. Resolve the customer and GST registration from `core.organisation`; if two
   matches are plausible, show both.
2. Resolve project/PO and contract rate from CRM records, with links to evidence.
3. Suggest the line and tax treatment; the deterministic preview computes money.
4. Show a field-level draft diff, missing fields and policy warnings.
5. Save only after **Create draft** confirmation. Issue remains a separate action
   on the invoice detail screen.

Voice transcription must store the transcript and confidence, not the raw audio
by default. If audio retention is required, add consent and a short retention
policy.

#### B. Upload and verify

Extend the built extraction path into a review workbench:

- Use embedded PDF text first; OCR/vision only for scans and photos.
- Render each extracted field with page/bounding-box evidence where available.
- Detect likely duplicates using seller/buyer GSTIN, invoice number, date, total
  and file hash before creating anything.
- Compare stated subtotal/tax/total with deterministic recomputation.
- Compare the selected organisation, GST registration, contract rate and PO with
  CRM evidence; mismatches block one-click acceptance.
- Learn only from accepted field corrections, stored as evaluation examples.
  Never fine-tune or prompt-train directly from unreviewed uploads.

#### C. “Why is this amount different?”

The copilot returns a calculation trace, not a generated answer:

```text
215 acre × ₹150.00 = ₹32,250.00 taxable
IGST 18% = ₹5,805.00
Invoice total = ₹38,055.00
Treatment selected: IGST
Evidence: buyer GSTIN state 09; seller entity state 07
```

Each figure comes from the server result. The explanation may paraphrase the
trace but cannot supply replacement numbers.

#### D. Collections copilot

- Age receivables by due date and actual partial payments.
- Rank reminder suggestions using deterministic features first: days overdue,
  amount outstanding, last contact, promised-payment date and prior payment
  behaviour.
- Draft courteous reminders from approved templates for the selected channel.
- Batch send requires a preview, recipient list and one explicit confirmation.
- Apply quiet hours, contact consent, frequency caps, opt-outs and an audit log.
- A gateway payment link may mark an invoice paid only after a signed, idempotent
  webhook whose amount/currency/reference match the invoice. Mismatches enter a
  reconciliation queue.

“Payment-risk score” is advisory and must show its contributing facts. It must
not use sensitive personal traits and must not automatically deny service.

#### E. Compliance intelligence

- Version HSN/SAC and GST-rate knowledge by `effective_from`/`effective_to` and
  source document. Suggestions show the code, rate, effective date and citation.
- Run pre-issue checks for GSTIN/state mismatch, suspicious numbering gaps,
  duplicate documents, inclusive/exclusive tax conflict, unusual rate changes,
  missing PO, total mismatch and invoice-date/FY mismatch.
- Generate a CA review packet with warnings and evidence. The CA resolves or
  accepts each warning; the model cannot dismiss one.
- Add credit/debit notes, e-invoice IRN and GSTR integrations only as separate
  statutory phases with sandbox certification, idempotency and immutable request/
  response archives.

#### F. Agriculture-specific invoice intelligence

This is the defensible advantage over a generic invoice generator:

- Suggest billable work from completed project/service records, never merely
  from a calendar event.
- Reconcile billed acres/sq km/hectares against operation logs and the project’s
  geospatial area. Show tolerance, source date and variance.
- Detect overlapping plots or repeated service periods that may cause double
  billing.
- Compare invoice rate with the signed contract/PO and flag a variance before
  issue.
- Join revenue and collections to crop, season, district, project and customer
  while enforcing the same row-level access rules as the CRM.

### 12.4 Automatic GSTIN verification and buyer auto-fill

- Run local format, embedded PAN, state-code and checksum verification as the
  user types. This layer is deterministic, free and always available.
- Run live verification through a provider-neutral `GstinLookupProvider`; do not
  scrape the public GST portal or couple domain code to one vendor.
- A successful lookup returns GSTIN, taxpayer status, legal name, trade name,
  registration type, effective/cancellation dates, principal address, state and
  provider reference. Store the raw response outside the invoice row and retain
  its hash for audit.
- Show a field-level comparison when live identity differs from the selected CRM
  organisation. Never silently overwrite an organisation or an issued invoice.
- **Use verified details** may populate a new draft or propose an organisation
  update after explicit confirmation. Issuing remains a separate action.
- Block issue for a malformed GSTIN, a cancelled/inactive registration, a state
  conflict that changes tax treatment, or an unavailable/stale live check when
  the organisation’s policy requires current verification.
- Provider downtime must return `verification_unavailable`, not “valid”. Allow a
  permissioned override only when policy permits, with actor, reason and time.
- Cache lookup results for a configurable TTL and deduplicate concurrent checks.
  Provide **Verify again** and display “Verified at”, provider and result status.
- Apply request throttling and audit access because GSTIN lookup reveals business
  identity information. Never send invoice lines, amounts or unrelated CRM data
  to the lookup provider.

### 12.5 Payment and delivery records

Add these tables before email, WhatsApp or payment automation:

| Table | Purpose / essential fields |
|---|---|
| `crm.gstin_verification` | tenant, GSTIN, provider, provider reference, status, legal/trade name, registration type, address/state snapshot, effective/cancellation dates, checked/expires timestamps, raw-response hash, error code |
| `crm.invoice_gstin_check` | invoice, verification, checked GSTIN, result, blocking reasons, override actor/reason/time; immutable issue-time evidence |
| `crm.invoice_delivery` | `invoice_id`, channel, recipient, PDF SHA-256, template version, provider id, status, attempts, sent/delivered/failed timestamps, error code |
| `crm.payment_request` | `invoice_id`, provider, provider reference, amount, currency, URL/QR payload, expires_at, status, idempotency key |
| `crm.payment_webhook_event` | provider event id (unique), signature status, received_at, raw-payload object key/hash, processing result |
| `crm.invoice_reminder` | invoice id, policy id, scheduled/sent time, recipient, message snapshot, approval actor, delivery id |
| `crm.ai_proposal` | tenant/actor, action, model and prompt version, input/evidence hashes, proposed diff, warnings, status, expiry, confirmed_by |
| `crm.ai_evaluation_case` | redacted input fixture, expected structured result/tool calls, provenance, approval status, regression tags |

Issued documents keep their original PDF. Re-verifying a GSTIN never rewrites a
historical invoice; it creates a new verification record while the invoice keeps
the exact issue-time verification evidence.

### 12.6 API additions

All endpoints use the project’s existing authentication, role checks, tenant
scoping, strict-query behaviour, request ids and structured errors.

| Endpoint | Behaviour |
|---|---|
| `POST /api/v1/invoice-copilot/proposals/` | Text/transcript → evidence-backed proposal; no mutation |
| `POST /api/v1/invoice-copilot/proposals/{id}/apply/` | Apply confirmed diff to a draft only; idempotent |
| `POST /api/v1/invoices/{id}/checks/` | Deterministic pre-issue checks plus sourced suggestions |
| `POST /api/v1/gstin/verifications/` | Normalise, checksum-check and perform/reuse a live provider lookup |
| `GET /api/v1/gstin/verifications/{id}/` | Return status, verified identity, timestamps and mismatch warnings |
| `POST /api/v1/invoices/{id}/gstin-check/` | Attach current verification evidence to a draft/pre-issue review |
| `POST /api/v1/invoices/{id}/deliveries/preview/` | Exact recipient/message/PDF preview |
| `POST /api/v1/invoices/{id}/deliveries/` | Confirmed email/WhatsApp send through the outbox |
| `POST /api/v1/invoices/{id}/payment-requests/` | Manual UPI or gateway request; idempotent |
| `POST /api/v1/payment-webhooks/{provider}/` | Signature-verified event ingestion; quick ACK + queued processing |
| `GET /api/v1/receivables/ageing/` | Outstanding balance and ageing derived from payment rows |
| `POST /api/v1/reminder-runs/preview/` | Candidate recipients and drafted messages; no send |
| `POST /api/v1/reminder-runs/{id}/confirm/` | Confirm a frozen preview and enqueue delivery |
| `GET /api/v1/invoice-ai/evaluations/summary/` | Accuracy, abstention, mismatch and latency metrics by version |

Do not expose a generic “run tool” endpoint. Server-side allow-lists decide which
copilot actions exist and re-authorise every tool call.

### 12.7 AI quality gates

Build a redacted golden set from the three known templates plus difficult real
cases: short/invalid GSTIN, duplicate number, two lines with the same product of
quantity and rate, GST-inclusive survey, government UIN, rotated/mobile photo,
live-name/state mismatch, inactive registration, provider outage/stale cache and
a total mismatch.

Required release gates:

- 100% exact match for invoice number and GSTIN on text PDFs in the golden set.
- 100% detection of intentionally injected arithmetic mismatches and duplicates.
- Zero AI-issued invoices, AI-created payments or silent issued-document edits in
  permission/integration tests.
- Field accuracy reported separately; never hide errors inside one average score.
- Low-confidence or conflicting evidence abstains and requests review.
- Prompt/model change cannot deploy unless the evaluation suite is at least as
  good on critical fields and produces no new unsafe tool call.
- Store latency, provider cost, confidence calibration and human correction rate;
  do not store raw sensitive documents in model traces.

### 12.8 Security and privacy gates

- Treat invoice text, uploaded files, model output and inbound messages as
  untrusted. Scan files, validate MIME by content, cap pages/pixels/bytes and
  isolate parsing/rendering workers.
- Encrypt provider credentials; redact GSTIN, bank account, phone, email and
  addresses from logs and analytics.
- Use providers under an approved data-processing arrangement; document region,
  retention and whether customer data is used for training.
- Enforce tenant isolation inside every retrieval query. Retrieval results are
  data, never prompt instructions; defend against prompt injection in PDFs.
- Sign webhook validation against the raw request bytes, reject stale replays and
  process each provider event once.
- Keep an immutable audit trail for issue, cancel, payment, delivery, export and
  AI-proposal confirmation.

---

## 13. Delivery plan for the advanced module

### Phase I-7 · AI invoice copilot — 2 sprints

**Build**

- Read-only CRM retrieval tools: organisation/GST registration, project, PO,
  contract rate, prior draft and service evidence.
- Proposal service and field-level diff UI; draft-only apply endpoint.
- Deterministic calculation trace and “explain this total”.
- Duplicate detection and the evaluation harness in CI.
- Web text input first; add voice only after text proposals pass the gates.

**Exit gate**

- [ ] Ten representative requests produce correct drafts or abstain safely
- [ ] Every populated field links to evidence or is marked user-provided
- [ ] Tool tests prove the copilot cannot issue, cancel, pay, send or edit an issued invoice
- [ ] Existing billing money/GSTIN/PDF tests remain green

### Phase I-8 · Collections copilot — 1–2 sprints

**Build**

- Ageing API, promised-payment note, manual UPI request and QR.
- Delivery outbox, email provider and exact artifact tracking.
- Reminder preview/confirm flow with consent, quiet hours and frequency caps.
- Gateway payment links/webhook reconciliation only after manual UPI is stable.

**Exit gate**

- [ ] Partial payments produce the correct outstanding amount and ageing bucket
- [ ] Duplicate webhook delivery cannot create a duplicate payment
- [ ] No reminder can send without a frozen preview or an explicitly enabled policy
- [ ] Delivery history identifies the recipient and exact PDF hash sent

### Phase I-9 · Automatic GSTIN verification and WhatsApp — 1–2 sprints

**Build**

- Instant checksum verification plus a provider-neutral live lookup adapter.
- Verification cache, audit records, buyer auto-fill comparison and issue-time
  policy enforcement.
- WhatsApp webhook with signature/replay controls and sender-to-tenant binding.
- Voice note → transcript → proposal → draft-preview response; media PDF send.

**Exit gate**

- [ ] Malformed, inactive and state-mismatched GSTINs are stopped before issue
- [ ] Provider downtime never produces a false “verified” result
- [ ] Verified buyer details populate a draft only after confirmation
- [ ] An unregistered sender cannot read or mutate any tenant data
- [ ] Voice can create a draft proposal but cannot allocate a number
- [ ] Opt-out and channel failure are visible in the delivery history

### Phase I-10 · Compliance and domain intelligence — 2+ sprints

**Build**

- Effective-dated HSN/SAC knowledge with primary-source citations and CA approval.
- Contract/PO rate comparison, geospatial billed-area variance and double-billing checks.
- Tally/Zoho exports and a GSTR-1 working paper with validation report.
- Later, separate certified projects for credit/debit notes, IRN and GSTR-2B.

**Exit gate**

- [ ] Every tax/code suggestion shows its effective date and source
- [ ] CA can resolve each warning and export a signed review report
- [ ] Area and rate variances are reproducible from stored evidence
- [ ] Nothing claims to file a return or obtain an IRN until that integration is certified

### Recommended build order

Start with the **proposal service + diff UI + duplicate detector + eval harness**.
They make the existing extraction feature safer immediately and create the
trust boundary every later AI feature needs. Then add collections because it
uses deterministic invoice/payment data and delivers value without asking AI to
decide tax. Automatic GSTIN verification and WhatsApp follow once delivery
auditing exists; compliance integrations remain last because they carry the
highest external and statutory risk.
