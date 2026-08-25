# 01 · Product Requirements Document

## 1. Scope

### In scope
Master data on farmers, FPOs, ACS/cooperatives and sugar mills; the people inside those organisations; the relationships between all of them; commercial pipeline (projects, leads, BD, agents); consent-governed WhatsApp and email engagement; data quality and provenance; reporting.

### Explicitly out of scope for v1
Accounting/invoicing (integrate with Tally/Zoho Books later), input/output commodity trading, payments and settlement, satellite/remote-sensing analytics (Theta Analytics already does this — we *link* to it, we don't rebuild it), farmer-facing mobile app (v1 mobile is for our agents only).

## 2. Personas

| # | Persona | Volume | Primary need | Access pattern |
|---|---|---|---|---|
| P1 | **Field / BD Agent** | 20–200 | Log visits, add and update orgs and farmers, see today's plan, work offline | Mobile-first, own territory only |
| P2 | **BD Manager** | 5–20 | Pipeline health, agent performance, forecast, reassign accounts | Web, own region |
| P3 | **Data Ops Analyst** | 3–10 | Import, dedupe, verify, quarantine, fix quality | Web, cross-region, powerful bulk tools |
| P4 | **Campaign Manager** | 2–5 | Build segments, launch WhatsApp/email, read results | Web, only consented audiences |
| P5 | **Project Manager** | 5–15 | Run a project through its lifecycle, manage its counterparties | Web, own projects |
| P6 | **Leadership** | 3–8 | Coverage maps, funnel, data-asset growth | Dashboards, read-mostly, all regions |
| P7 | **Compliance Officer** | 1–2 | Consent audit, DSR requests, retention, provenance reports | Read-all + consent controls |
| P8 | **System Admin** | 1–3 | Users, roles, integrations, config | Everything |

## 3. Modules

### M1 — Organisation Registry
Unified registry for FPO, ACS/PACS, sugar mill, cooperative federation, agri-input dealer, NGO/promoting institution, government body, and private company. One `organisation` table with a `type` discriminator plus type-specific extension tables ([Doc 02](./02-data-model.md) §4).

**Must have**
- Create/edit/merge organisations with full audit trail
- Type-specific fields (crushing capacity for mills, member count and CIN for FPOs, registration act for societies)
- Hierarchy: parent org → subsidiary/unit/branch (e.g. a sugar group with 6 mills)
- Geography down to village, with PostGIS point + optional command-area polygon
- Attach documents (registration certificate, MoU, licence)
- Duplicate detection at creation time (fuzzy name + district + phone)
- Bulk import from XLSX/CSV with a dry-run preview and per-row error report

**Should have**
- Auto-enrich from MCA master data by CIN
- Timeline of every interaction with the org across all users

### M2 — People & Roles
People are separate from organisations and can hold multiple roles across multiple organisations over time. An FPO's MD who later joins a mill board is one person record, two role records.

**Must have**
- `person` record with name, designation-free identity
- `person_organisation_role` with role type (MD, CEO, Chairman, Director, Secretary, Cane Manager, Procurement Head, Board Member, Member-Farmer), start/end date, is_primary_contact flag
- Multiple phone numbers and emails per person, each with its own verification state and consent state
- Role change history preserved, never overwritten

### M3 — Farmer Master
**Must have**
- Farmer identity: name, gender, DOB/age band, father/spouse name (critical for disambiguation in rural India), category (SC/ST/OBC/Gen), farmer class (marginal <1ha / small 1–2ha / semi-medium 2–4ha / medium 4–10ha / large >10ha — derived, not entered)
- Address to village level, with village linked to the LGD (Local Government Directory) code
- Land: one or more `land_parcel` records with area in hectares, ownership type (owned/leased/sharecropped), survey/khasra number, irrigation source, optional geometry
- Crops: `farmer_crop` per season (Kharif/Rabi/Zaid) per year with crop, variety, area, expected yield
- Livestock (relevant for biogas/dairy projects)
- Bank/UPI presence flag only — 🔴 **never store full account numbers in v1**
- Identity references stored as **hash + last 4 digits only**, never plaintext ([Doc 12](./12-security-rbac.md) §5)
- Consent ledger entry required before any outbound message
- Source and provenance on every farmer record

**Should have**
- Link to FPO membership
- Link to supplying mill + supplier/cane code
- Link to Theta Analytics external ID for satellite/yield data lookup

### M4 — Data Intelligence ("useful vs not useful")
This is your requirement for a section separating important information from noise, built properly.

**Must have**
- Per-record **quality tier**: `gold` / `silver` / `bronze` / `quarantine` (definitions in [Doc 07](./07-data-quality-organic.md))
- Per-record **completeness score** 0–100, computed from a weighted field checklist per entity type
- Per-field provenance: `source_system`, `source_reference`, `collected_at`, `verified_at`, `verified_by`, `confidence` 0.00–1.00
- **Quarantine queue** — records that are contradicted, undeliverable, stale beyond threshold, or of unknown/unlawful origin. Quarantined records are invisible to campaigns and to normal search, but are never silently deleted.
- **Decay rules** — a phone number unverified for 24 months automatically drops from gold to silver; 36 months to bronze. Configurable per field.
- **Contradiction detection** — when two sources disagree on a field, both values are retained, the record is flagged, and an analyst resolves it.

**Should have**
- A "Signals" feed surfacing high-value changes: a mill announcing capacity expansion, an FPO filing its annual return, a director change on MCA, a lead going cold for 30 days

### M5 — Project Registry
**Must have**
- Project record: code, name, type (biogas/CBG, cane-yield analytics, carbon/MRV, mechanisation, irrigation, input supply, training, other), stage, value, currency, start/expected-end/actual-end
- Counterparty organisation(s) with role (client, implementation partner, funder, aggregator)
- Contact persons attached to the project with role on *this* project
- Sites: one project can span many villages/mills
- Milestones with owner and due date
- Documents: proposal, MoU, PO, report
- Full activity feed
- Linked BD opportunity it originated from

### M6 — Business Development Tracker
**Must have**
- Lead capture (manual, import, web form, campaign reply)
- Lead → Opportunity conversion
- Configurable pipeline stages with probability weights: `new → contacted → qualified → proposal_sent → negotiation → won | lost | dormant`
- Named lead contact person + org
- Expected value, weighted value, expected close date
- Loss reason taxonomy (price, competitor, no budget, no decision, timing, unresponsive, not a fit)
- Stage-ageing alerts: an opportunity sitting >N days in a stage raises a task
- Forecast view: this month / quarter, committed vs. best case

### M7 — Agent Tracker
**Must have**
- Agent profile linked to a user, with territory (states/districts/blocks) and assigned accounts
- Visit logging: org or farmer visited, purpose, outcome, notes, next action, **GPS coordinates + device timestamp captured automatically**
- Targets: visits/week, new orgs/month, new consented farmers/month, pipeline value
- Actual vs. target dashboard per agent, per team
- Route/day plan: what an agent should do today, generated from due follow-ups
- Offline capture with conflict-safe sync

🔴 **Constraint:** GPS is captured at the moment of a logged visit only. Continuous background location tracking of employees is not implemented — it is disproportionate, damages trust, and creates its own DPDP exposure for employee data.

### M8 — Engagement Engine
**Must have**
- Consent ledger as the single source of truth: channel, purpose, status (`opted_in`/`opted_out`/`never_asked`/`withdrawn`), evidence (how consent was obtained), timestamp, IP/device or physical form reference
- WhatsApp: template management synced with Meta, template-approval status, per-message send/delivery/read/failure webhooks written to the timeline
- Email: transactional (SES) + campaign, with open/click/bounce/complaint handling
- Segment builder over farmer/org attributes — 🔴 **segments cannot return non-consented contacts; this is enforced in the query layer, not the UI**
- STOP / "बंद करें" keyword handling → immediate opt-out across all channels
- Suppression list that survives re-import
- Send throttling and quiet hours (no sends 21:00–08:00 IST)

### M9 — Search & Reporting
- Global search across orgs, people, farmers, projects
- Saved filters, per-user and shared
- Exports with 🔴 mandatory reason capture and full export audit log
- Dashboards per persona
- Coverage map: choropleth of farmer/FPO/mill density by district

## 4. Key user stories

| ID | As a… | I want to… | So that… | Acceptance criteria |
|---|---|---|---|---|
| US-01 | BD Agent | search an FPO by partial name and district | I find it before creating a duplicate | Fuzzy search returns matches ≥0.35 trigram similarity in <500ms; a create attempt with ≥0.6 similarity shows a blocking "possible duplicate" panel |
| US-02 | BD Agent | log a visit offline in a village with no signal | my day's work isn't lost | Visit persists to local SQLite; syncs on reconnect; server rejects duplicates by client-generated UUID |
| US-03 | Data Ops | import 50,000 farmers from an XLSX | Theta's existing data is in the CRM | Dry-run shows row counts by outcome (create/update/skip/error); errors downloadable as XLSX with a reason column; import is atomic per batch and reversible for 7 days |
| US-04 | Data Ops | see every farmer whose phone failed delivery twice | I can quarantine bad numbers | Filter on `delivery_failure_count >= 2` exists; bulk-quarantine action available; action is audited |
| US-05 | Campaign Mgr | send a WhatsApp update to cane farmers in Muzaffarnagar | they get the mill's payment schedule | Segment builder filters on crop=sugarcane + district; **count of consented vs. total is shown before send**; non-consented are excluded and the exclusion count is displayed |
| US-06 | Farmer (via reply) | reply STOP | I stop receiving messages | Opt-out recorded within 5 seconds; all channels for that person suppressed; confirmation message sent; no further message can be queued |
| US-07 | BD Manager | see which opportunities have not moved in 21 days | I can intervene | Stage-ageing report; auto-task to owner at threshold |
| US-08 | Project Mgr | attach three villages and two mills to one project | the project reflects reality | Many-to-many project↔site and project↔organisation with a role on each link |
| US-09 | Compliance | produce every record and consent artefact for one farmer | I can answer a DSR in 30 days | Single-subject export produces JSON + PDF of all data and consent history, and is itself audited |
| US-10 | Leadership | see consented-farmer count growth by month by state | I can track the data asset | Time-series dashboard from the consent ledger, not the farmer table |
| US-11 | Data Ops | merge two FPO records | the graph stays clean | Merge preserves both source IDs, re-points all children, writes a `merge_event`, and is reversible for 30 days |
| US-12 | Agent | see my targets vs. actuals today | I know where I stand | Dashboard on mobile home screen, computed server-side, cached 5 min |

## 5. Non-functional requirements

| Area | Requirement |
|---|---|
| **Scale** | 10,000,000 farmer rows, 50,000 organisations, 500,000 people, 5,000,000 activity rows/year, 100,000,000 message-event rows over 3 years |
| **Performance** | p95 list query <400ms; global search <600ms; any single-record read <150ms; import throughput ≥5,000 rows/min |
| **Availability** | 99.5% business hours (07:00–22:00 IST); RPO 15 min; RTO 4 h |
| **Concurrency** | 300 concurrent web users, 200 concurrent mobile agents |
| **Offline** | Mobile app fully functional offline for 72 h; sync resolves conflicts last-writer-wins per field with a conflict log |
| **Localisation** | UI in English + Hindi at v1; data entry accepts Devanagari; architecture supports Marathi, Punjabi, Telugu, Kannada, Gujarati, Tamil later. Store a `name_local` alongside `name_en` for every person and org. |
| **Data residency** | 🔴 All personal data stored and processed in `ap-south-1` (Mumbai). No PII in third-party SaaS outside India without a documented assessment. |
| **Retention** | Farmer PII retained while consent is live + 24 months, then anonymised. Message logs 24 months. Audit logs 7 years. |
| **Accessibility** | WCAG 2.1 AA on web; minimum 16px touch targets on mobile |
| **Browser support** | Chrome/Edge last 2 versions, Firefox ESR, Safari 16+ |

## 6. Constraints and assumptions

**Assumptions**
- Theta Analytics' existing farmer data has a documented lawful basis; this must be confirmed before import (Doc 05 §7). If it cannot be, that data is imported in `quarantine` tier and is not messageable.
- WhatsApp Business Account will be verified under the Theta Analytics legal entity with a Green quality rating maintained.
- Field agents have Android devices, 4GB RAM or better.

**Constraints**
- Rural connectivity: assume 2G fallback; API payloads for mobile must stay under 100KB per sync page.
- Name ambiguity is severe: "Ram Kumar" in one district may be 400 distinct people. Father's name + village + phone is the minimum disambiguation key.
- Phone-number churn in rural India runs 15–20% per year. The data model must treat a number as an attribute with a lifecycle, not as an identity.
- Landholding data is frequently self-reported and inflated. Store `area_source` (self-declared / document-verified / satellite-derived) on every parcel.
