# AgriCRM — Claude Build Brief

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


## Purpose

Use this document as the implementation brief for continuing the AgriCRM project.

The immediate objective is **not** to implement the entire 52-week roadmap in one pass. The objective is to turn the existing foundation into a usable internal CRM where Theta Analytics can:

1. Maintain organisations, people, farmers/prospects and their relationships.
2. Upload CSV/XLSX files safely.
3. Detect duplicates before committing an import.
4. Track leads, owners, stages, tasks and follow-ups.
5. Retain the source, legal basis, verification state and history behind imported data.
6. Use approved public institutional sources without scraping personal farmer information.

Work in small, reviewable increments. Complete and verify one milestone before starting the next.

---

## Read before changing code

Read these files completely before implementation:

1. `CLAUDE.md`
2. `README.md`
3. `agri-crm-docs/00-executive-summary.md`
4. `agri-crm-docs/01-product-requirements.md`
5. `agri-crm-docs/02-data-model.md`
6. `agri-crm-docs/04-architecture.md`
7. `agri-crm-docs/05-data-sourcing-and-legal.md`
8. `agri-crm-docs/06-ingestion-pipeline.md`
9. `agri-crm-docs/08-fpo-acs-registry.md`
10. `agri-crm-docs/09-project-registry-and-trackers.md`
11. `agri-crm-docs/11-api-spec.md`
12. `agri-crm-docs/12-security-rbac.md`
13. `agri-crm-docs/15-execution-plan.md`
14. `INVOICE.md`

Do not reinterpret the compliance requirements as optional product preferences.

---

## Current repository state

### Committed and substantially implemented

- Django 5.2 + Django REST Framework backend.
- React 19 + TypeScript + Vite + Tailwind frontend.
- PostgreSQL 16 + PostGIS business schema.
- Redis and Celery configuration.
- Custom user model, roles and territory fields.
- JWT login, refresh, logout and password change.
- TOTP MFA enrolment and verification flows.
- Geography models, Django Admin and read-only APIs.
- Organisation models, type profiles, Django Admin and REST API.
- Organisation duplicate detection and forced-override audit fields.
- Data-source, field-provenance and contradiction models.
- OpenAPI generation and generated frontend types.
- Compliance-oriented SQL triggers and smoke tests.

### Present in the working tree but not safely finished

There is a large **uncommitted billing/invoicing implementation**. It includes backend models, APIs, tests, invoice rendering, AI-assisted document extraction and React invoice screens.

Before modifying anything:

```bash
git status --short
git diff --stat
```

Treat every existing modification and untracked file as user-owned work. Do not reset, discard, overwrite or mass-format it.

The invoicing implementation currently includes:

- `INVOICE.md`
- `backend/apps/billing/`
- `backend/tests/test_billing_*.py`
- `frontend/src/api/billing.ts`
- `frontend/src/pages/Invoice*.tsx`
- billing additions to settings, URLs, schema, OpenAPI and CI

Preserve and stabilize this work. Do not remove billing because it is outside the original phase order.

### Known current validation results

- Django system check passes.
- Ruff passes.
- TypeScript checking passes.
- Vite production build passes.
- The billing money/extraction subset recently produced 68 passes and 1 failure.
- The failing test was `test_an_unsupported_file_type_names_what_to_upload`: unsupported content reached the missing-Pillow error before file-type validation.
- The full database-backed suite could not be rerun because Docker Desktop was stopped.
- Approximately 179 parametrized tests are currently collected.

Revalidate these facts; do not assume they remain true after edits.

---

## Non-negotiable engineering constraints

### Preserve the database ownership model

The business schema is owned by:

```text
agri-crm-docs/sql/schema.sql
```

The `ref`, `core`, `comm`, `crm`, `dq` and `audit` schemas use DDL features Django migrations cannot fully represent. Django business models map to those tables using `managed = False`.

Whenever changing a business table:

1. Update `schema.sql`.
2. Update the matching unmanaged Django model.
3. Update smoke tests where appropriate.
4. Regenerate `openapi.yaml` when the API contract changes.
5. Run the SQL smoke suite.

Do not silently move business-schema ownership into Django migrations.

### Preserve user identifiers correctly

`accounts.User.pk` is an internal integer used by Django packages.

`accounts.User.public_id` is the UUID that crosses into business schemas. Any reference from `core`, `crm`, `dq`, `comm` or `audit` to an application user must use `public_id`.

### Preserve soft deletion and auditability

- Do not hard-delete business records.
- Use `is_deleted`, status changes or domain-specific cancellation/discard behavior.
- Preserve import-batch history.
- Preserve duplicate overrides and who performed them.
- Preserve full provenance when an import changes a field.

### Do not scrape farmer personal information

Do not implement bulk scraping of individual farmer names, phones, emails, home addresses, land records or beneficiary records.

Approved collector categories are institutional and aggregate sources such as:

- LGD geography
- MCA company master data
- SFAC/NABARD/NCDC/NAFED FPO lists
- ISMA/NFCSF/state sugar directories
- data.gov.in datasets
- AGMARKNET and other aggregate datasets
- official organisation websites, at low rate and according to their terms

Collectors must:

1. Check `dq.source.is_approved` before the first request.
2. Use a descriptive User-Agent with a contact address.
3. Respect `robots.txt`.
4. Remain at or below one request per second.
5. Never authenticate, bypass a CAPTCHA or evade rate limits.
6. Store raw source material before transformation.

### Personal data import requirements

Personal data can enter only from a documented permitted source such as:

- partner agreement
- field collection with notice and consent
- inbound self-registration
- approved Theta Analytics legacy batch
- licensed dataset with written warranties

An import containing personal data must not commit unless a named user has confirmed its legal basis.

### Farmer queries must be state-scoped

`core.farmer` is partitioned by `state_id`. Every list, search, update and relationship lookup involving farmers must include the state partition key. The `/farmers/` list endpoint must reject an unscoped request with HTTP 400.

---

## Security work that must happen first

### S-1: enforce MFA server-side

`apps.accounts.permissions.IsMFAVerified` exists, but verify that it is actually applied to all protected endpoints.

Required behavior:

- A user whose role requires MFA may receive the initial JWT pair.
- Before MFA verification, that token must be rejected by protected business APIs.
- MFA enrolment and verification must remain accessible so the user can complete the second step.
- A verified token with `mfa_satisfied=true` must access protected APIs.
- Non-MFA roles continue to work normally.
- Token refresh must not accidentally convert an unverified session into a verified one.

Add regression tests that exercise actual endpoints, not only the permission class in isolation.

### S-2: keep development bypass development-only

The local `.env` may contain:

```text
DEV_NO_AUTH=1
```

The frontend may also use `VITE_NO_AUTH=1` in a development-only environment file.

Requirements:

- Production and staging must refuse to start if the backend bypass is enabled.
- Production builds must never compile the frontend bypass on.
- Automated tests must run with authentication enabled.
- Documentation must clearly say that real PII must never be loaded while the bypass is active.
- Do not commit a secret or an active production bypass flag.

### S-3: stabilize billing validation

Fix the unsupported-file test so MIME/extension validation happens before image/PDF libraries are imported or used.

The extraction endpoint must:

- reject unsupported MIME types with a useful 400 response;
- enforce file-size limits before external API calls;
- never issue an invoice automatically;
- return a human-reviewable draft with confidence and warnings;
- avoid logging file contents or extracted personal data.

### Security exit gate

**Passed 28 August 2026.** Evidence in the Phase 1 completion report below.

- [x] Authentication tests pass. *(237 backend tests, 0 failures)*
- [x] A privileged pre-MFA token receives 403 from a protected business
      endpoint. *(`/organisations/` and `/invoices/`, over HTTP, all four
      MFA-required roles)*
- [x] A verified privileged token succeeds. *(login → enrol → TOTP → verify →
      200, over HTTP)*
- [x] Development bypass tests pass. *(and a serious system check now stops the
      boot, so production refuses rather than ignores)*
- [x] Billing upload validation tests pass. *(Phase 0)*
- [x] No secrets are added to the repository. *(re-scanned; see the audit in
      the Phase 1 report)*

---

## Target product milestone

Build an internal, desktop-first CRM with these navigation areas:

```text
Overview
Organisations
People
Farmers / Prospects
Imports
Pipeline
Tasks
Invoices
Data Health
Account
```

The first usable workflow must be:

```text
Upload CSV/XLSX
    -> select source and record legal basis
    -> map columns
    -> validate and dry-run
    -> review errors and duplicates
    -> commit accepted rows
    -> open an organisation/person/farmer record
    -> create or assign a lead
    -> set stage and next follow-up
    -> see the activity history
```

Do not call the product usable until this full workflow succeeds.

---

## Milestone A — stabilise the current tree

### Tasks

1. Start Docker Desktop, then run the database and Redis containers.
2. Run the complete test suite.
3. Fix current billing test failures.
4. Run SQL smoke tests.
5. Run Ruff, TypeScript checking and the frontend build.
6. Check OpenAPI generation for warnings or undocumented endpoints.
7. Review the working-tree diff for accidental secrets and generated noise.
8. Do not commit unless explicitly asked; report a recommended commit split instead.

### Commands

Use the repository commands where possible:

```bash
docker compose up -d
make check
make smoke
make schema-doc
```

On Windows, use the project virtual environment and the documented frontend commands if `make` is unavailable.

### Exit criteria

- Complete backend suite passes.
- SQL smoke suite passes.
- Ruff passes.
- TypeScript passes.
- Vite production build passes.
- OpenAPI is regenerated and consistent.
- Known billing upload failure is fixed.

---

## Milestone B — organisation registry frontend

The organisation backend already exists. Replace the `/organisations` placeholder with a real React interface.

### Screens

#### Organisation list

Include:

- search by name and alias;
- type filter: FPO, sugar mill, cooperative and other supported types;
- state and district filters;
- quality-tier filter;
- active/deleted behavior according to permissions;
- pagination using the backend cursor contract;
- clear loading, empty and error states;
- a create button for authorized roles.

Do not download every organisation to filter it in the browser.

#### Organisation creation

Include:

- common organisation fields;
- type-specific profile fields;
- geography selectors using reference APIs;
- source selection;
- duplicate check before create;
- a duplicate-blocking panel that shows candidates and scores;
- forced override only with explicit confirmation and an auditable reason if the backend supports it.

#### Organisation detail

Include:

- identity and type profile;
- addresses and geography;
- registration details;
- aliases;
- source and data-quality information;
- annual metrics;
- related people;
- activity history placeholder wired to real activity API when Milestone F lands;
- edit and soft-delete controls according to permission.

### API requirements

Use and extend `/api/v1/organisations/`. Do not create a parallel frontend-only data model.

Unknown filters must continue returning HTTP 400.

### Exit criteria

- Create, list, filter, view, edit and soft-delete work end to end.
- Duplicate blocking is visible and cannot be bypassed accidentally.
- UI works at mobile and desktop breakpoints, with desktop optimized for data operations.
- No placeholder remains at `/organisations`.

---

## Milestone C — people, roles and contact points

Implement the application layer for the existing schema concepts:

- `core.person`
- `core.person_org_role`
- `core.contact_point`

### Domain behavior

- A person can hold roles in multiple organisations.
- Roles have `valid_from` and `valid_to`.
- Changing a role closes the previous role row; it does not overwrite history.
- Phone and email values are separate contact-point rows.
- Contact points have verification state, source and lifecycle metadata.
- One primary contact point per kind/owner is enforced.
- Contact details are masked by default.

### Required APIs

Provide endpoints for:

- people list/search;
- person create/read/update;
- organisation people;
- role add/close;
- contact-point add/verify/deactivate;
- one-record contact reveal for users with permission.

### PII rules

- Default serializers return masked phone/email values.
- Full values require `contact.view_full`.
- Every reveal writes `audit.data_access_log`.
- There is no bulk-unmask endpoint.
- Search uses normalized values without returning full values to unauthorized users.

### Frontend

Build:

- `/people` list;
- person detail page;
- people section on organisation detail;
- add/close-role controls;
- masked contact display;
- explicit reveal control for permitted users.

### Exit criteria

- Role history is preserved.
- Contact masking is tested at serializer and endpoint level.
- Every reveal creates an audit row.
- Organisation detail displays current and former people correctly.

---

## Milestone D — safe CSV/XLSX import

This is the highest-priority missing capability because real data cannot safely enter the CRM without it.

### Supported inputs

- `.csv`
- `.xlsx`

Do not accept macros or executable spreadsheet formats.

### Import flow

#### 1. Upload

Create a `dq.import_batch` row and store the original file unchanged in the configured storage backend.

Record:

- uploader;
- upload timestamp;
- source;
- file name;
- file SHA-256;
- entity type;
- row count;
- status;
- legal-basis state;
- optional consent/evidence reference.

#### 2. Column mapping

Allow users to map incoming columns to CRM fields.

Support saved mappings per source/partner and entity type.

Never guess silently. Suggestions are allowed, but the user must confirm ambiguous mappings.

#### 3. Normalize

Normalize at the edge:

- trim whitespace;
- preserve original values for audit/debugging;
- normalize phone numbers to E.164 where possible;
- explicitly parse Indian dates with day-first behavior;
- map state/district/block/village through LGD codes or a reviewable match;
- preserve Devanagari/local names;
- convert area to hectares only when state/unit context makes the conversion unambiguous;
- reject ambiguous bigha conversions rather than guessing.

#### 4. Dry run

Dry run must not write business records.

Return:

- total rows;
- valid rows;
- invalid rows;
- probable duplicates;
- creates;
- safe updates;
- contradictions;
- quarantined rows;
- a 20-row preview;
- per-row validation errors.

#### 5. Legal-basis gate

An import cannot commit unless:

- its source is allowed for the entity/data type;
- `legal_basis_confirmed=true`;
- a named authenticated user confirmed it;
- the confirmation timestamp is recorded.

For Theta legacy batches, require an explicit classification:

- Green: documented compatible consent;
- Amber: legitimate but unclear/narrower consent, non-messageable;
- Red: quarantine or reject, never messageable.

#### 6. Commit

- Commit in chunks of at most 5,000 rows.
- Make the operation idempotent.
- Store row-level outcomes.
- Never let a lower-confidence import silently overwrite a recently human-verified field.
- Write `dq.field_provenance` for every accepted field change.
- Raise contradictions where a lower-confidence source disagrees with a stronger value.
- Preserve suppressions and opt-outs across re-imports.

#### 7. Error file

Generate a downloadable XLSX containing:

- the original row;
- row number;
- field-level error messages;
- duplicate candidates where relevant;
- suggested correction where safe.

#### 8. Rollback

Allow an authorized data-ops user to reverse an import for seven days.

Rollback must be auditable and must not erase unrelated subsequent edits.

### Frontend

Build `/imports` with:

- batch history;
- upload wizard;
- column mapping;
- dry-run summary;
- error preview/download;
- duplicate review;
- legal-basis confirmation;
- commit progress;
- rollback action;
- clear status and failure recovery.

### Exit criteria

- A sample organisation spreadsheet imports successfully.
- A bad file produces a useful downloadable error workbook.
- A duplicate cannot be silently created.
- A batch without legal confirmation cannot commit through either UI or direct API use.
- Re-uploading the same file is idempotent.
- Rollback reverses only the import's accepted changes.

---

## Milestone E — farmers and prospects

### Important terminology checkpoint

The existing project is an agricultural CRM. It does not currently model recruitment/job candidates.

If the business uses the word **candidate** to mean a sales prospect, model that as a lead/prospect relationship—not as a recruitment applicant.

If recruitment candidates are genuinely required, stop and obtain confirmation before adding a separate HR/recruitment bounded context. Do not overload `core.farmer` or `crm.lead` with recruitment semantics.

### Farmer application layer

Implement models/APIs for the existing farmer schema and related entities:

- farmer;
- land parcel;
- crops;
- livestock;
- farmer-to-organisation links;
- consent state;
- provenance and quality information.

### Required behavior

- Every farmer operation includes `state_id`.
- Farmer lists reject missing state scope.
- Aadhaar plaintext is never accepted, stored or logged.
- Prefer not collecting Aadhaar at all.
- Contact values are masked by default.
- Consent state is displayed per purpose.
- Amber/Red legacy batches are never treated as messageable.

### Frontend

Replace `/farmers` with:

- state-scoped list/search;
- filters for geography, organisation, crop and quality tier;
- farmer detail;
- land/crop/organisation relationships;
- provenance and freshness indicators;
- consent summary;
- create/edit flow with consent/source requirements.

### Exit criteria

- Unscoped list is rejected.
- Farmer CRUD and related records work.
- PII masking and consent restrictions are covered by tests.
- Imported farmers show their batch and field provenance.

---

## Milestone F — pipeline, tasks and activity history

Implement the minimum commercial CRM layer:

- leads;
- opportunities;
- projects where conversion requires them;
- stage history;
- activities;
- tasks and follow-up dates;
- ownership and territory behavior.

### Minimum lead fields

- organisation/person/farmer relationship as appropriate;
- source;
- owner;
- status/stage;
- estimated value;
- probability where applicable;
- next follow-up date;
- last activity timestamp;
- notes;
- created/updated timestamps.

### Rules

- Stage changes write immutable stage-history records.
- Marking an opportunity lost requires a loss reason.
- Leads can convert to opportunities without losing source/history.
- Opportunity conversion can create/link a project.
- Every meaningful mutation writes an activity entry.
- Overdue follow-ups are visible on the overview screen.
- Territory-scoped roles cannot access out-of-territory records.

### Frontend

Replace `/pipeline` with:

- lead list;
- opportunity list or board;
- lead/opportunity details;
- stage change control;
- owner assignment;
- task/follow-up creation;
- overdue filters;
- conversion workflow;
- activity timeline.

### Exit criteria

- A user can create a lead from an organisation/person.
- A follow-up appears on the dashboard.
- Stage history cannot be rewritten.
- Lost reason is enforced in the database and API.
- Conversion preserves history and relationships.

---

## Milestone G — overview and data health

Replace the build-status-only overview with operational information while retaining a separate build-status/developer page if useful.

### Overview metrics

- organisations by type;
- farmers/prospects by state;
- new records this week;
- imports requiring attention;
- duplicate-review queue;
- overdue tasks;
- leads by stage;
- weighted pipeline;
- invoices outstanding;
- recent activity.

### Data health

Build the first useful `/quality` page:

- source scorecard;
- quality-tier distribution;
- contradictions needing review;
- duplicate candidates;
- stale high-value records;
- import failure rate;
- verification throughput versus decay rate when scoring is implemented.

Do not show decorative zero cards for metrics whose backend is not implemented.

---

## Billing completion requirements

Preserve the billing implementation and finish it as an independent bounded context.

### Required checks

- Models match the billing DDL.
- Draft invoices allocate no invoice number.
- Issuing allocates exactly one number transactionally.
- Cancelled invoice numbers are never reused.
- Issued invoice identity fields cannot be silently changed.
- Payment transitions correctly update part-paid/paid status.
- Cancelling requires a reason.
- GSTIN and government UIN behavior is tested.
- Tax-inclusive rates do not inflate revenue.
- Indian digit grouping and rupees-in-words remain server-owned.
- HTML preview and issued document use the same calculations and templates.
- AI extraction creates only a draft.
- Extraction warnings and confidence reach the user.
- Uploaded invoice documents are size/type checked before external calls.
- PDF generation degrades clearly when no rendering engine is installed.

### Visual verification

Render each invoice template and compare it to its approved reference document. Do not declare template completion based only on HTML output or arithmetic tests.

---

## API conventions

- Base path: `/api/v1/`.
- Keep URL versioning.
- Use cursor pagination for large active tables.
- Return the project's uniform error envelope.
- Document every endpoint with drf-spectacular.
- Regenerate frontend types when OpenAPI changes.
- Reject unknown filters with HTTP 400.
- Avoid N+1 queries using `select_related`/`prefetch_related` deliberately.
- Never return an unbounded geography, farmer, activity or audit list.
- Use transactions for imports, conversion, issuing invoices and other multi-write operations.
- Use idempotency keys for retried import and mobile-style writes where relevant.

---

## Frontend conventions

- Preserve the existing design language unless fixing a clear usability problem.
- Use TanStack Query for server state.
- Do not create a second global server-state store.
- Use generated OpenAPI types where practical.
- Keep access tokens in memory and refresh behavior consistent with the existing client.
- Provide accessible labels, keyboard behavior and meaningful errors.
- Show skeleton/loading, empty, error and permission-denied states.
- Avoid loading entire tables for client-side filtering.
- Optimize data-entry screens for desktop while keeping responsive behavior.
- Keep local-language text intact.

---

## Testing requirements

For each milestone, add tests at the lowest appropriate level and at least one end-to-end API workflow.

### Backend

- Model/DDL round-trip tests for every unmanaged model.
- Database-constraint tests for critical rules.
- API permission tests.
- MFA tests using a real pre/post-verification token flow.
- Import validation, legal-basis and idempotency tests.
- Duplicate blocking tests.
- PII masking and reveal-audit tests.
- State-partition-scoping tests for farmers.
- Pipeline history and lost-reason tests.
- Billing arithmetic and lifecycle tests.

### Frontend

At minimum verify:

- TypeScript compilation;
- production build;
- primary page rendering;
- loading/error/empty states;
- duplicate-blocking workflow;
- import wizard state transitions;
- permission-sensitive controls;
- invoice create/preview/issue workflow.

### Compliance

- Run `scripts/check-r6.sh`.
- Run SQL smoke tests after DDL changes.
- Scan for secrets.
- Confirm logs do not contain phone, email, Aadhaar, PAN, tokens or uploaded file content.

---

## Required verification commands

Run commands appropriate to the platform, but cover all of these checks:

```bash
docker compose up -d
python manage.py check
pytest
ruff check .
python manage.py spectacular --validate --fail-on-warn
npm run typecheck
npm run lint
npm run build
make smoke
make check
```

If a command cannot run because Docker, a native PDF dependency, credentials or an external service is unavailable, report the exact blocker. Do not describe unexecuted tests as passing.

---

## Definition of the first usable CRM release

The first internal release is complete only when all of the following are true:

- Authentication is enabled for the target environment.
- MFA-required roles cannot use protected APIs before verification.
- An organization can be created, searched, edited and viewed in React.
- People and contact roles can be attached to organisations.
- Contact values are masked and reveals are audited.
- CSV/XLSX data can be dry-run, reviewed and committed.
- Imports require a source and legal-basis confirmation.
- Duplicates are blocked or explicitly reviewed.
- Imported records retain field provenance.
- A farmer/prospect or organisation can be connected to a lead.
- Leads have owners, stages, tasks and next follow-up dates.
- Stage history and activity history are visible.
- Invoice workflows remain functional.
- Complete backend, SQL, lint, typecheck and frontend build checks pass.
- No production PII is present in development or staging.

---

## How to report progress

At the end of every implementation session, report:

1. Files changed.
2. User-visible behavior added.
3. Database/API contract changes.
4. Tests added or changed.
5. Exact commands executed and their results.
6. Tests not run and why.
7. Remaining known defects.
8. The next smallest safe milestone.

Do not report a module as complete merely because its schema or placeholder page exists.

---

## Suggested execution order

Follow this order unless a discovered dependency requires a documented adjustment:

1. Stabilize current working tree and billing tests.
2. Enforce MFA server-side.
3. Build organisation React screens.
4. Implement people, roles and contact points.
5. Build safe CSV/XLSX import.
6. Implement farmer/prospect application layer.
7. Implement leads, opportunities, tasks and activity history.
8. Build operational overview and initial data-health screens.
9. Implement approved institutional collectors.
10. Harden deployment, RLS, monitoring, backup and disaster recovery.

Do not begin WhatsApp campaigns until consent, suppression, dispatch-time consent checks and Meta business verification are complete.

---

## Master phase-by-phase delivery structure

This section is the controlling delivery plan. The earlier milestone sections
describe feature behavior in detail; this section prevents work from being
skipped between milestones.

### Phase discipline

Claude must follow these rules for every phase:

1. Inspect the current implementation before writing code.
2. Record which requirements already exist, partially exist or are absent.
3. Create or update backend, frontend, database, documentation and tests as one
   coherent feature—not as disconnected layers.
4. Remove the corresponding `NotBuiltYet` route only after the real screen is
   connected to a real API.
5. Run the phase verification commands.
6. Produce a phase completion report.
7. Stop at the exit gate and request approval before beginning the next phase.

A phase is not complete because files were created. It is complete only when
its exit gate is demonstrated with passing tests or a documented external
dependency accepted by the owner.

### Required phase completion report

At the end of every phase, update a checklist in the pull request or working
notes with this exact structure:

```text
Phase:
Status: NOT STARTED / IN PROGRESS / BLOCKED / COMPLETE

Backend:
- [ ] Models/DDL
- [ ] Services/domain logic
- [ ] API/serializers/permissions
- [ ] Admin/data-ops surface

Frontend:
- [ ] Routes
- [ ] List/detail/create/edit flows
- [ ] Loading/empty/error/permission states
- [ ] Responsive and accessibility checks

Data:
- [ ] Seed/reference data
- [ ] Import/migration path
- [ ] Provenance/audit behavior

Verification:
- [ ] Unit tests
- [ ] Database constraint tests
- [ ] API workflow tests
- [ ] Frontend typecheck/build
- [ ] OpenAPI regenerated
- [ ] SQL smoke tests when DDL changed
- [ ] Security/compliance checks

Not completed:
Blockers:
Files changed:
Commands run and exact results:
```

No unchecked item may disappear from later reports. Move it to a named backlog
with an owner and reason if it is intentionally deferred.

---

## Phase 0 — repository safety and baseline

### Goal

Establish a reproducible green baseline without losing the user's existing
work.

### Prerequisites

- Repository is available locally.
- Existing working-tree changes have been inspected.
- Docker Desktop is available for database-backed verification.

### Build checklist

- [ ] Capture `git status --short` and `git diff --stat`.
- [ ] Identify tracked modifications and untracked files.
- [ ] Preserve the billing implementation and every unrelated user change.
- [ ] Start PostgreSQL and Redis with Docker Compose.
- [ ] Confirm database extensions and business schemas apply.
- [ ] Run the full backend suite.
- [ ] Fix the known unsupported-file validation test.
- [ ] Run SQL smoke tests.
- [ ] Run Ruff, frontend lint, TypeScript and production build.
- [ ] Validate OpenAPI and regenerate `openapi.yaml` if needed.
- [ ] Check that no secrets, uploaded documents or production PII are tracked.
- [ ] Recommend a safe commit split; do not commit without permission.

### Exit gate

- Full available test suite is green.
- SQL smoke suite is green.
- Backend and frontend builds succeed.
- Current differences are understood and preserved.
- Remaining external blockers are explicitly listed.

---

## Phase 1 — authentication, authorization and environment safety

### Goal

Make the security boundary trustworthy before real data is loaded.

### Backend checklist

- [x] Apply server-side MFA enforcement to protected business endpoints.
- [x] Keep login, token refresh where appropriate, MFA enrolment and MFA
      verification reachable during the pre-MFA state.
- [x] Ensure refresh tokens preserve the correct MFA state.
- [x] Confirm role definitions and cross-territory roles.
- [x] Confirm privileged permissions are explicit and tested.
- [x] Confirm development bypass refuses production/staging use.
- [x] Confirm tests always run with real authentication enabled.
- [x] Confirm password change revokes outstanding refresh tokens.
- [x] Confirm brute-force protection is active outside development.

### Frontend checklist

- [x] Login success and error behavior. *(unchanged; not automated — S-1b)*
- [x] MFA-required redirect. *(now also for a reloaded/deep-linked session)*
- [x] MFA enrolment and verification. *(backend flow tested over HTTP)*
- [x] Expired access-token refresh. *(single-flight refresh in client.ts; MFA state preserved)*
- [x] Logout and local token clearing. *(server-side blacklisting tested)*
- [x] Permission-denied behavior. *(403 pre-MFA now routes to /mfa rather than a blank shell)*
- [x] No-auth development mode excluded from production bundles. *(the build now refuses)*

### Tests

- [x] Non-MFA user can access a protected endpoint.
- [x] Privileged pre-MFA token receives 403.
- [x] Privileged verified token succeeds.
- [x] Refresh cannot bypass MFA.
- [x] Development bypass startup guards work.
- [x] Production settings never import the bypass.

### Exit gate

An API client cannot bypass MFA or role checks by ignoring frontend navigation,
and no production build can enable the development bypass.

### Completion report — 28 August 2026

```text
Phase: 1 — authentication, authorization and environment safety
Status: COMPLETE

Backend:
- [x] Models/DDL — unchanged. No migration; no DDL touched.
- [x] Services/domain logic — IsMFAVerified hardened; boot-time check guard added.
- [x] API/serializers/permissions — MFA enforced by default; PRE_MFA opt-outs; /auth/me/ reports token MFA state.
- [x] Admin/data-ops surface — unchanged. See "Not completed" for Admin MFA.

Frontend:
- [x] Routes — unchanged.
- [x] List/detail/create/edit flows — unchanged.
- [x] Loading/empty/error/permission states — RequireAuth now routes an unsatisfied privileged session to /mfa.
- [x] Responsive and accessibility checks — no visual change; bundle 70.47 -> 70.55 kB (one condition).

Data:
- [x] Seed/reference data — unchanged.
- [x] Import/migration path — not applicable to this phase.
- [x] Provenance/audit behavior — unchanged.

Verification:
- [x] Unit tests — 237 passed, 0 failed (was 186; +51).
- [x] Database constraint tests — 20/20 SQL smoke assertions.
- [x] API workflow tests — 51 new tests drive real HTTP against real endpoints.
- [x] Frontend typecheck/build — tsc clean, oxlint 0 warnings, Vite build 567ms.
- [x] OpenAPI regenerated — 0 warnings strict, DEV_NO_AUTH on and off, byte-identical.
- [x] SQL smoke tests when DDL changed — DDL unchanged; run anyway, green.
- [x] Security/compliance — R6 guard passes; no secrets; no new PII.

Not completed:
- Django Admin does not require MFA. /admin/ is session-authenticated, so
  DEFAULT_PERMISSION_CLASSES does not reach it: a data_ops user with is_staff
  can read the registry there with a password alone. Closing it needs
  django-otp's OTPAdminSite, which is a different mechanism and touches every
  admin.py. Named backlog item S-1a, owner Nitish Malik, for Phase 13
  (production hardening) or sooner if staff accounts are issued first.
- Frontend behaviour is asserted by typecheck and build only. There is no
  browser test runner in this repo, so the login / MFA-redirect / logout flows
  are verified server-side and by reading the code, not by an automated
  frontend test. Named backlog item S-1b, owner Nitish Malik.

Blockers: none.

Files changed:
  backend/config/settings/base.py          IsMFAVerified added to DEFAULT_PERMISSION_CLASSES
  backend/apps/accounts/permissions.py     both MFA claims required, not just mfa_satisfied
  backend/apps/accounts/views.py           PRE_MFA map; explicit opt-outs; pinned schema description
  backend/apps/accounts/serializers.py     /auth/me/ reports mfa_satisfied for the presenting token
  backend/config/startup_checks.py         NEW — refuse_unsafe_boot()
  backend/config/wsgi.py                   calls refuse_unsafe_boot()
  backend/config/asgi.py                   calls refuse_unsafe_boot()
  backend/conftest.py                      sign_in fixture
  backend/tests/test_phase1_auth.py        NEW — 36 tests
  backend/tests/test_phase1_environment.py NEW — 15 tests
  backend/tests/test_auth_bypass.py        updated for the new default permissions
  backend/tests/test_phase1_registry.py    client fixture now carries a verified token
  backend/tests/test_billing_agent.py      same, for the extraction endpoint test
  frontend/vite.config.ts                  production build refuses VITE_NO_AUTH
  frontend/src/App.tsx                     RequireAuth redirects an unsatisfied session to /mfa
  openapi.yaml                             regenerated (2 changes: pinned description, mfa_satisfied)
  frontend/src/api/schema.d.ts             regenerated

Commands run and exact results:
  pytest -q                                237 passed, 141 warnings, 66.07s, exit 0
  ruff check .                             All checks passed!
  ruff format --check .                    106 files already formatted
  manage.py check                          System check identified no issues (0 silenced)
  spectacular --validate --fail-on-warn    exit 0 with DEV_NO_AUTH=0
  spectacular --validate --fail-on-warn    exit 0 with DEV_NO_AUTH=1
  diff of the two schemas                  identical; in sync with committed openapi.yaml
  scripts/smoke-test.sh                    SMOKE TEST GREEN — 20/20 assertions passed
  scripts/check-r6.sh                      R6 OK
  npm run lint                             oxlint src, 0 warnings
  npm run typecheck                        tsc --noEmit clean
  npm run build                            built in 567ms; index 70.55 kB, react 220.36 kB, CSS 25.60 kB
  VITE_NO_AUTH=1 npm run build             exit 1, refused with the remediation message
  vite build --mode development            exit 0, 60.07 kB — 10 kB smaller, the login and MFA
                                           screens compiled out. This is what the guard prevents shipping.
```

**Negative verification.** A security test that passes both with and without
the fix proves nothing, so both changes were confirmed by reverting them:

| Reverted to | Result |
|---|---|
| `DEFAULT_PERMISSION_CLASSES` without `IsMFAVerified` | 14 of the new tests fail, including both business endpoints, writes, refresh and all four MFA roles |
| `IsMFAVerified` checking `mfa_satisfied` alone | `test_a_token_issued_before_a_promotion_does_not_survive_it` fails |

**What was actually wrong.** `IsMFAVerified` was written in Phase 0, unit
tested, and referenced approvingly in three docstrings — and attached to
nothing. Every organisation, geography and billing endpoint inherited
`IsAuthenticated` alone. A privileged user who ignored the frontend's redirect
to `/mfa/` and called the API with curl was served. The fix is a default rather
than a per-view decoration, because a permission class that must be remembered
on every new viewset is one that will eventually be forgotten, and the
forgetting looks like nothing at all in review.

---

## Phase 2 — geography and reference-data foundation

### Goal

Load and verify the geography and reference records required by every business
module.

### Build checklist

- [ ] Verify State, District, Block, Village, Crop and CropVariety models
      against DDL.
- [ ] Complete the LGD ingestion/synchronization command.
- [ ] Preserve stable LGD identifiers across refreshes.
- [ ] Load all required states, districts, blocks and villages.
- [ ] Load crop and crop-variety reference data.
- [ ] Record source and synchronization timestamps.
- [ ] Handle renamed, split, merged and inactive geography records.
- [ ] Prevent unbounded village API and Admin scans.
- [ ] Provide dependent state → district → block → village selectors.
- [ ] Add synchronization metrics and error reporting.

### Tests

- [ ] Hierarchy round-trip tests.
- [ ] Duplicate LGD code prevention.
- [ ] Unscoped village list returns 400.
- [ ] Sync is idempotent.
- [ ] Renamed/inactive records retain historical relationships.

### Exit gate

Real LGD data is loaded, dependent geography selection works, and every later
module can reference stable geography records.

---

## Phase 3 — organisation registry end to end

### Goal

Deliver the first complete CRM master-data module through both Django Admin and
React.

### Backend checklist

- [ ] Verify base organisation and all type-profile models.
- [ ] Verify annual metrics and aliases.
- [ ] Complete list, search, filter, create, retrieve, update and soft delete.
- [ ] Preserve unknown-filter rejection.
- [ ] Preserve duplicate scoring and district scoping.
- [ ] Audit forced duplicate overrides.
- [ ] Enforce source compatibility and data-quality fields.
- [ ] Optimize list/detail queries.

### Frontend checklist

- [ ] Replace the organisation placeholder route.
- [ ] Organisation list with server-side filters and pagination.
- [ ] Create flow with type-specific fields.
- [ ] Duplicate review/override flow.
- [ ] Detail view with profile, geography, source and quality.
- [ ] Edit and soft-delete behavior.
- [ ] Annual metrics and aliases.
- [ ] Loading, empty, error and permission states.

### Data checklist

- [ ] Load a verified sample of every supported organisation type.
- [ ] Confirm no production PII enters development.
- [ ] Prepare the path to the Phase 1 target volume of FPOs and mills.

### Exit gate

An authorized user can manage organisations entirely through React, duplicate
creation is blocked, and Django Admin remains a complete data-ops fallback.

---

## Phase 4 — people, organisation roles and contacts

### Goal

Model the humans inside organisations without losing role or contact history.

### Backend checklist

- [ ] Map Person, PersonOrgRole and ContactPoint to the DDL.
- [ ] Implement person CRUD and search.
- [ ] Implement add/close role behavior.
- [ ] Implement contact add, verify, mark-primary and deactivate.
- [ ] Enforce primary-contact uniqueness.
- [ ] Normalize phone/email values.
- [ ] Mask contact values by default.
- [ ] Implement one-record reveal with permission.
- [ ] Write an audit access row for every reveal.
- [ ] Prevent bulk reveal/export paths.

### Frontend checklist

- [ ] People list and search.
- [ ] Person create/detail/edit.
- [ ] Organisation people tab.
- [ ] Current and historical role presentation.
- [ ] Masked contacts and explicit reveal control.
- [ ] Contact verification and deactivation controls.

### Exit gate

People can move between roles without overwriting history, unauthorized users
never receive full contacts, and authorized reveals are individually audited.

---

## Phase 5 — import platform and legacy-data intake

### Goal

Create the controlled doorway through which organisation, person and later
farmer data enters the CRM.

### Backend checklist

- [ ] Implement ImportBatch and row-error application models.
- [ ] Store original uploads with SHA-256.
- [ ] Accept CSV and XLSX only.
- [ ] Validate MIME, extension and size before parsing.
- [ ] Implement saved column mappings by source/entity type.
- [ ] Normalize dates, phones, geography, local names and area units.
- [ ] Implement no-write dry run.
- [ ] Implement duplicate and contradiction output.
- [ ] Implement legal-basis confirmation by a named user.
- [ ] Implement Green/Amber/Red legacy classification.
- [ ] Commit idempotently in bounded chunks.
- [ ] Write field-level provenance.
- [ ] Prevent low-confidence overwrite of recent verified values.
- [ ] Generate downloadable XLSX errors.
- [ ] Implement seven-day auditable rollback.
- [ ] Add task progress and retry behavior through Celery.

### Frontend checklist

- [ ] Import history.
- [ ] Upload step.
- [ ] Source and legal-basis step.
- [ ] Column mapping step.
- [ ] Dry-run summary and row preview.
- [ ] Error and duplicate review.
- [ ] Commit confirmation and progress.
- [ ] Error workbook download.
- [ ] Rollback action and result.

### Exit gate

At least one organisation/person spreadsheet completes the entire upload → dry
run → review → commit → provenance → rollback cycle successfully.

---

## Phase 6 — farmer master, land and consent

### Goal

Deliver the state-partitioned farmer master and defensible consent records.

### Backend checklist

- [ ] Map Farmer and all child models to DDL.
- [ ] Require state scope for every farmer operation.
- [ ] Implement land parcels, crops, livestock and organisation links.
- [ ] Implement create/edit with source and provenance.
- [ ] Implement consent-event application layer.
- [ ] Preserve append-only consent behavior.
- [ ] Preserve suppression over later opt-in/import.
- [ ] Implement messageable-state calculation from the approved view.
- [ ] Reject Aadhaar plaintext everywhere.
- [ ] Implement retention/anonymization jobs.
- [ ] Implement DSR request workflow foundations.

### Frontend checklist

- [ ] Replace farmer placeholder route.
- [ ] State-scoped farmer list and search.
- [ ] Farmer detail and edit.
- [ ] Land, crop, livestock and organisation sections.
- [ ] Consent-by-purpose summary.
- [ ] Source, quality, verification and freshness indicators.
- [ ] Import-batch and provenance history.

### Exit gate

Farmer data can be created/imported with provenance, unscoped queries fail,
consent history cannot be rewritten, and Amber/Red records are non-messageable.

---

## Phase 7 — leads, opportunities, projects, tasks and activities

### Goal

Make the CRM operational for business-development work.

### Backend checklist

- [ ] Implement leads and source attribution.
- [ ] Implement opportunities and required stages.
- [ ] Enforce loss reason.
- [ ] Implement immutable stage history.
- [ ] Implement lead → opportunity conversion.
- [ ] Implement opportunity → project conversion/linking.
- [ ] Implement owners and territory scope.
- [ ] Implement tasks, due dates, reminders and completion.
- [ ] Implement ordered activity feed.
- [ ] Write activity entries from all meaningful mutations.
- [ ] Implement stuck-stage/overdue calculations.
- [ ] Implement forecast totals.

### Frontend checklist

- [ ] Replace pipeline placeholder route.
- [ ] Lead list/detail/create/edit.
- [ ] Opportunity list/board and detail.
- [ ] Stage movement with required fields.
- [ ] Task and follow-up controls.
- [ ] Conversion workflow.
- [ ] Activity timeline.
- [ ] Forecast and overdue views.

### Exit gate

The BD team can manage a real lead from first contact through opportunity and
project conversion without relying on a pipeline spreadsheet.

---

## Phase 8 — billing and invoicing production completion

### Goal

Turn the existing uncommitted invoice implementation into a verified bounded
context without breaking CRM delivery.

### Build checklist

- [ ] Stabilize all billing tests.
- [ ] Verify DDL/model parity.
- [ ] Verify invoice numbering under concurrent issue requests.
- [ ] Verify cancellation and payment lifecycle.
- [ ] Verify GST, UIN, tax-inclusive and tax-exempt cases.
- [ ] Verify all three templates against approved reference documents.
- [ ] Install/configure a production PDF backend.
- [ ] Store generated documents and SHA-256 reliably.
- [ ] Keep issued-document regeneration deterministic.
- [ ] Keep AI extraction draft-only.
- [ ] Validate extraction upload type/size before external calls.
- [ ] Display extraction confidence and warnings.
- [ ] Add permission checks and audit behavior.
- [ ] Connect invoices to organisations/projects where supported.
- [ ] Complete list, create, preview, issue, cancel and payment UI.

### Exit gate

An invoice can be created, previewed, issued, rendered, paid and cancelled with
correct numbering, totals and audit history, and the complete billing suite is
green.

---

## Phase 9 — data quality, dedupe and operational reporting

### Goal

Make the database improve over time rather than decay invisibly.

### Build checklist

- [ ] Implement entity completeness and quality scoring.
- [ ] Implement freshness decay and tier transitions.
- [ ] Implement dedupe review queue.
- [ ] Implement contradiction-resolution queue.
- [ ] Implement quarantine and verification queues.
- [ ] Implement merge with reversible snapshots.
- [ ] Tune auto-merge only against a labelled dataset.
- [ ] Implement source scorecard.
- [ ] Implement import-quality metrics.
- [ ] Implement overview operational metrics.
- [ ] Replace data-health placeholder route.
- [ ] Report verification throughput versus decay rate.

### Exit gate

Data Ops can see what is wrong, prioritize work, resolve duplicates and
contradictions, reverse a merge, and measure whether quality is improving.

---

## Phase 10 — approved collectors and scheduled ingestion

### Goal

Automate only approved institutional and aggregate data acquisition.

### Collector order

1. LGD synchronization.
2. MCA master data.
3. SFAC FPO lists.
4. ISMA directory.
5. NFCSF directory.
6. State Sugarfed/Cane Commissioner sources for UP and Maharashtra.
7. Configured data.gov.in aggregate datasets.

### Build checklist

- [ ] Implement one reusable BaseCollector.
- [ ] Assert source approval before network access.
- [ ] Enforce User-Agent, robots and rate limit.
- [ ] Store raw responses before transform.
- [ ] Record run metrics and failure details.
- [ ] Feed normalized output through the same import/provenance rules.
- [ ] Prevent collector overwrite of stronger verified values.
- [ ] Add layout-change/row-count alerts.
- [ ] Schedule collectors with Celery Beat.
- [ ] Keep collector queue isolated from messaging/import queues.
- [ ] Add fixtures so parsing tests do not depend on live websites.

### Exit gate

At least one approved collector runs end to end, an unapproved source fails
before its first request, and layout changes are observable rather than silent.

---

## Phase 11 — consent-governed communications

### Goal

Deliver WhatsApp and email without allowing a send to bypass consent or
suppression controls.

### Prerequisites

- Lawyer-reviewed privacy notice.
- Approved purpose-specific consent wording.
- Meta business verification or approved BSP path.
- SES domain warmed and authenticated.
- Farmer/recipient consent system complete.

### Build checklist

- [ ] Template management and language versions.
- [ ] WhatsApp template-status synchronization.
- [ ] Segment builder using permitted fields.
- [ ] Exclusion breakdown before approval.
- [ ] Campaign approval gates.
- [ ] Dispatch-time consent recheck.
- [ ] Recipient selection only from `comm.v_messageable_farmer`.
- [ ] Quiet hours and weekly frequency cap.
- [ ] Redis rate limiting.
- [ ] Webhook signature verification and fast acknowledgement.
- [ ] Delivery, failure, bounce and complaint processing.
- [ ] STOP handling in every launched language.
- [ ] Suppression propagation and queued-message cancellation.
- [ ] Campaign abort and auto-pause thresholds.
- [ ] Live campaign progress and audit trail.
- [ ] Replace campaigns placeholder route.

### Exit gate

A controlled test campaign demonstrates consent filtering, suppression,
dispatch-time recheck, quiet hours, delivery updates and multilingual opt-out.

---

## Phase 12 — field operations and offline mobile

### Goal

Support territory-scoped field work and later an offline-first Android app.

### Web/field backend checklist

- [ ] Agent profiles and historical territories.
- [ ] Targets and actuals.
- [ ] Field visits with GPS/device timestamps.
- [ ] Territory-scoped day plan.
- [ ] Web visit-entry surface.
- [ ] RLS policies and pooled-connection tests.
- [ ] Cursor-based pull sync.
- [ ] Idempotent batch push with per-record results.
- [ ] Conflict log and retry behavior.

### Mobile checklist

- [ ] Expo application shell.
- [ ] Local SQLite persistence.
- [ ] Offline authentication/session behavior.
- [ ] Offline day plan and record lookup.
- [ ] Visit capture.
- [ ] Farmer capture with local-language notice and consent.
- [ ] Deferred photo upload.
- [ ] Sync status, errors and conflict resolution.
- [ ] Flaky-network and duplicate-retry testing.

### Exit gate

A pilot agent completes a full offline day, syncs without data loss or
duplicate visits, and can see and correct rejected records.

---

## Phase 13 — production deployment, observability and hardening

### Goal

Operate the complete system safely in AWS Mumbai at expected scale.

### Infrastructure checklist

- [ ] Terraform completed and reviewed.
- [ ] Separate staging and production accounts/environments.
- [ ] RDS PostgreSQL/PostGIS, backups and Multi-AZ.
- [ ] ElastiCache Redis.
- [ ] ECS services and isolated Celery queues.
- [ ] S3 storage with lifecycle, encryption and access controls.
- [ ] Secrets Manager for all credentials.
- [ ] TLS, security headers and allowed-host/origin restrictions.
- [ ] India-region data residency verification.
- [ ] Automated migrations/DDL application with rollback plan.

### Operations checklist

- [ ] PII-scrubbed Sentry and structured logs.
- [ ] Metrics and alerts for API, Celery, database and collectors.
- [ ] Audit-log retention.
- [ ] Partition creation automation.
- [ ] Backup restore drill with measured RTO/RPO.
- [ ] Incident and breach runbook drill.
- [ ] Penetration test and remediation.
- [ ] Load test at three times projected peak.
- [ ] Dependency and secret scanning.
- [ ] Production access/offboarding review.

### Exit gate

Deployment is repeatable, restore and incident drills meet targets, security
findings are closed, and the system passes load and compliance checks.

---

## Phase 14 — final completeness audit and handover

### Goal

Prove that no planned feature was silently left as schema-only, API-only,
frontend-only or documentation-only work.

### Completeness audit

For every module in `backend/apps/` and every product area in
`agri-crm-docs/01-product-requirements.md`, create a row in a final matrix:

| Product area | DDL/model | Domain logic | API | Admin | React | Tests | Docs | Production verified |
|---|---|---|---|---|---|---|---|---|
| Accounts | | | | | | | | |
| Geography | | | | | | | | |
| Organisations | | | | | | | | |
| People/contacts | | | | | | | | |
| Imports | | | | | | | | |
| Farmers/land/crops | | | | | | | | |
| Consent/suppression | | | | | | | | |
| Leads/opportunities | | | | | | | | |
| Projects | | | | | | | | |
| Tasks/activities | | | | | | | | |
| Billing/invoices | | | | | | | | |
| Data quality | | | | | | | | |
| Collectors | | | | | | | | |
| Campaigns/messaging | | | | | | | | |
| Field operations | | | | | | | | |
| Mobile/sync | | | | | | | | |
| Reporting/exports | | | | | | | | |
| Audit/DSR | | | | | | | | |
| Deployment/operations | | | | | | | | |

Use only these values in each cell:

- `COMPLETE`
- `PARTIAL — <named missing work>`
- `BLOCKED — <external dependency>`
- `NOT STARTED`
- `NOT APPLICABLE — <reason>`

Blank cells are forbidden.

### Final audit checklist

- [ ] Search the UI for every `NotBuiltYet`, TODO and placeholder.
- [ ] Search backend apps for empty model/API/admin modules.
- [ ] Compare actual endpoints with `agri-crm-docs/11-api-spec.md`.
- [ ] Compare actual tables/models with `agri-crm-docs/02-data-model.md`.
- [ ] Compare delivered behavior with the PRD.
- [ ] Confirm every API is used or intentionally internal.
- [ ] Confirm every React screen uses real backend data.
- [ ] Confirm every scheduled job is deployed and observable.
- [ ] Confirm every compliance rule R1–R13 has an enforcement point and test.
- [ ] Confirm all placeholder and demo data is removed from production paths.
- [ ] Confirm operator, data-ops and incident documentation is current.
- [ ] Record every accepted deferred item in a versioned post-v1 backlog.

### Exit gate

The completeness matrix contains no unexplained blank or `PARTIAL` cells, all
accepted deferrals are explicit, and the owner signs off on the final backlog.

---

## Phase status dashboard

Maintain this table as work progresses. Update it at the end of every phase;
never delete a phase.

| Phase | Name | Status | Exit gate evidence |
|---:|---|---|---|
| 0 | Repository safety and baseline | COMPLETE | 28 Aug 2026: 186 backend tests, 20/20 SQL smoke, Ruff, oxlint (0 warnings), `tsc`, Vite build, R6 guard, and strict OpenAPI at 0 warnings with `DEV_NO_AUTH` both on and off. Working tree preserved; nothing committed |
| 1 | Authentication and environment safety | COMPLETE | 28 Aug 2026: `IsMFAVerified` moved into `DEFAULT_PERMISSION_CLASSES` — it existed but was applied to nothing, so every business endpoint served a privileged pre-MFA token. 51 new tests in `test_phase1_auth.py` / `test_phase1_environment.py` drive real HTTP: privileged pre-MFA token gets 403 from `/organisations/` and `/invoices/`, verified token gets 200, non-MFA role unaffected, refresh preserves state in both directions, tampered claim is 401, session cookie is 401, password change is post-MFA only and revokes every outstanding token. 237 backend tests, 20/20 SQL smoke, Ruff, oxlint, `tsc`, Vite build, R6 guard, strict OpenAPI at 0 warnings with `DEV_NO_AUTH` on and off (byte-identical). Verified by reverting the fix: 14 of the new tests fail without it. Nothing committed |
| 2 | Geography and reference data | PARTIAL | Models/APIs exist; real LGD sync/load outstanding |
| 3 | Organisation registry | PARTIAL | Backend/Admin exist; React UI and target data volume outstanding |
| 4 | People, roles and contacts | NOT STARTED | Schema exists only |
| 5 | Import platform | NOT STARTED | Schema/design exists only |
| 6 | Farmer master and consent | NOT STARTED | Schema exists only |
| 7 | Pipeline, tasks and activities | NOT STARTED | Schema exists only |
| 8 | Billing production completion | IN PROGRESS | Suite green and extraction upload validation fixed 28 Aug 2026 (type checked before provider dispatch, endpoint returns 400). Still uncommitted; templates not yet compared against the approved reference documents; production PDF backend unverified |
| 9 | Data quality and reporting | NOT STARTED | Foundation models only |
| 10 | Approved collectors | NOT STARTED | Empty collector package/design only |
| 11 | Communications | NOT STARTED | Schema/settings only |
| 12 | Field operations and mobile | NOT STARTED | Schema only; no mobile app |
| 13 | Production hardening | NOT STARTED | Deployment scaffolding only |
| 14 | Completeness audit and handover | NOT STARTED | Runs after all build phases |

---

## Anti-gap rules

These rules exist specifically to prevent features from looking complete while
one layer is missing:

- A DDL table without an application model is incomplete.
- A model without domain behavior and permissions is incomplete.
- An API without tests and OpenAPI documentation is incomplete.
- An Admin-only feature is incomplete when the phase requires a React user
  workflow.
- A React page using mock/static data is incomplete.
- A route showing `NotBuiltYet` is incomplete.
- A background task without scheduling, retry behavior, metrics and alerts is
  incomplete.
- A collector without source approval, raw landing and provenance is
  incomplete.
- A communication feature without dispatch-time consent checking is unsafe and
  incomplete.
- A test that was not executed is not passing.
- A production feature that has only been tested with synthetic unit data is
  not production verified.
- A schema smoke test does not replace an API workflow test.
- An API workflow test does not replace a database-constraint test for a
  compliance invariant.
- Documentation must be updated in the same phase as behavior changes.

---

## Initial instruction to Claude

Use the following as the first prompt with this file:

> Read `CLAUDE.md` and `CLAUDE_CRM_BUILD_BRIEF.md` completely. Treat the Master phase-by-phase delivery structure as the controlling plan. Inspect the current git status and preserve every existing change, especially the uncommitted billing implementation. Begin only with Phase 0, then stop at its exit gate and report the required completion checklist. Run the available checks, fix verified failures, add regression tests, and report what changed. Do not start the next phase until I explicitly approve it. Do not commit, reset, discard or deploy anything unless I explicitly ask.
