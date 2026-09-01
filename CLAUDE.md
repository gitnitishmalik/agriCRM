# AgriCRM — Project Context

**Owner:** Nitish Malik · **Org:** Theta Analytics · **Spec version:** 1.0 (24 Aug 2026)

---

## What this project is

A production CRM for the **Indian agriculture value chain** — farmers, FPOs (Farmer Producer Organisations), ACS/cooperative societies, and sugar mills — plus the commercial layer that sells into them.

It is not a Salesforce clone. Standard CRMs model `Company → Contact → Deal`. This system models:

```
State → District → Block → Village
                              ↓
   Sugar Mill ← cane command area → Farmer → FPO (a Companies Act entity with a board)
        ↓                             ↓          ↓
   Procurement officer          Land parcels   MD / CEO / Directors / Member farmers
        ↓                             ↓          ↓
              ───────  Project / Lead / BD activity  ───────
```

A farmer is simultaneously a **supplier to a mill**, a **member of an FPO**, a **landholder**, and a **messaging recipient with a consent state**. No off-the-shelf CRM has that shape. The relationship graph is the product; the contact list is a by-product.

## Current state

**Phase 0 (Foundation) complete.** Phase 1 (Organisation Registry) is in progress —
sprints 1–3 done, sprints 4–6 outstanding.

| Path | Status |
|---|---|
| `agri-crm-docs/*.md` | 16 documents, complete |
| `agri-crm-docs/sql/schema.sql` | Applied and running locally on PostgreSQL 16 + PostGIS |
| `agri-crm-docs/sql/smoke_test.sql` | 20/20 green. Wired into CI and `make smoke`. |
| `agri-crm-docs/sql/schema_invoice_advanced.sql` | 🔴 **Idempotent**, unlike `schema.sql`. Applied by `make db-migrate`, which is safe against a live database. |
| `backend/` | **FastAPI. The whole service.** Django was removed on 30 Aug 2026 — deleted, not merely stopped. Auth + MFA, organisations, geography, **people / roles / contact points**, the full billing module, the invoice copilot, collections, GSTIN verification, compliance exports, collectors, and the `/admin` console. |
| `infra/docker/` | Local Postgres + Redis. **Ports 5433 / 6380**, not the defaults. |
| `infra/terraform/` | Scaffolded and documented, **not applied** — needs AWS credentials |
| `.github/workflows/ci.yml` | 6 jobs: schema, lint, compliance, test, **ai-safety**, frontend |
| `frontend/` | React 19 + Vite: auth, MFA, invoices, copilot panel, issue confirmation, receivables, delivery history. |
| `mobile/` | Does not exist. Phase 6. |

**What works right now:** `make db-migrate` → `make run` gives you the API on
:8001, Swagger at `/api/docs`, the data-operations console at `/admin`, a
working JWT + TOTP auth flow, the organisation registry with its collected-data
provenance, and the complete billing module — draft, copilot proposal,
pre-issue checks, issue, PDF, delivery outbox, receivables, GSTIN verification
and the accounting exports. **185 tests pass.**

🔴 **The console is why Django is gone.** This file valued Django Admin at
roughly three months of frontend work and named it the one reason not to
retire the Django service. `/admin` is that reason answered — server-rendered
over the same domain layer, with the collected data and its provenance as the
thing it leads with. With it in place the stack is FastAPI end to end.

**Phase 1 remaining:** LGD geography load (~660k villages), bulk import
with the legal-basis gate (sprint 4), collectors (sprints 5–6), and the exit
gate's real-data volume: 20,000+ FPOs and 500+ mills.

**Sprint 3 landed** — `core.person`, `core.person_org_role` and
`core.contact_point` are mapped and served at `/api/v1/people/`, with R4 on the
write path, R9 masking on every read and R10 on volume. 28 tests, most of them
holding a compliance control rather than a feature.

### Phase 0 decisions worth knowing

- **Python 3.13.** Doc 03 named Django 5.0, which does not support it; the
  service ran on Django 5.2 LTS for the whole of Phase 0 and is now FastAPI.
- **Postgres on 5433, Redis on 6380.** A local PostgreSQL install commonly holds
  5432 and silently wins the connection, producing a confusing auth failure.
- **The business schema is owned by `sql/schema.sql`, not by the ORM.**
  🔴 `Base.metadata.create_all()` is never called and must not be — a test
  greps for it. The `ref`/`core`/`comm`/`crm`/`dq`/`audit` schemas are applied
  by DDL and *mapped* by SQLAlchemy models, because the DDL carries the
  partitioning, generated columns and triggers the ORM cannot express, and
  those *are* the compliance controls. Schema changes are reviewed SQL applied
  by `make db-migrate`, never a generated migration.
- **`backend/models/accounts.py` maps the `accounts_user` table in full.** `role`
  and `district_ids` exist now even though RLS itself lands in Phase 3 — the
  column has to be there before the policy that reads it.
- **🔴 `User.public_id`, not `User.pk`, crosses into the business schema.** The
  DDL types every user reference (`owner_user_id`, `created_by`, `changed_by`,
  `crm.agent.user_id`, …) as `uuid` and carries no FK back to `accounts_user`,
  so the business schema never depends on the auth table. The integer PK is
  now just the surrogate key for `accounts_user` itself. Anything writing into
  `core`/`comm`/`crm`/`dq`/`audit` uses `public_id`.
- **`openapi.yaml` is committed.** Regenerate with `make schema-doc`; a diff in
  review is how contract drift becomes visible.

### Phase 1 decisions worth knowing

- **`__table_args__ = {"schema": "core"}` is how a model reaches a non-public
  schema.** SQLAlchemy has first-class support for this, which is one of the
  smaller reasons the port was worth doing: Django needed a `schema_table()`
  helper that closed and reopened the identifier quoting inside `db_table`, and
  that helper is gone.
- **The test suite runs against a real Postgres holding the real `schema.sql`.**
  Nothing else keeps a mapped table honest — a renamed column is a runtime
  error no static check catches. Tests write through every model and read back,
  and one deliberately trips a DDL `CHECK` the ORM cannot express.
- **Duplicate blocking is a scorer, not a trigram threshold.**
  `backend/dedupe.py` strips legal-form suffixes, then compares
  token sets with a Dice coefficient and fuzzy token equality. Trigram is only
  an index-backed prefilter. Scoped to a district, because a national name
  match is noise. The admin form, the create endpoint's 409 and
  `check-duplicates` all call it, so they cannot drift apart.
- **Unknown query filters are a 400, not a shrug.** A typo'd filter that
  silently does nothing is how someone exports the whole registry believing
  they exported one district.
- **Lists that would scan a huge table refuse instead.** `ref.village` reaches
  ~660k rows; both the admin changelist and `/villages/` require a district,
  block or pincode first.

### People, roles and contact points — sprint 3 decisions worth knowing

`backend/routers/people.py` over `core.person`, `core.person_org_role` and
`core.contact_point`. This is the module where personal data lives, so most of
what follows is a compliance control rather than a design preference.

- **🔴 R4 is a source-kind gate on the write path, not a policy in a document.**
  `domain/pii.require_pii_source` resolves the `dq.source` a row names and
  refuses unless its kind is one of `partner_agreement`, `field_collection`,
  `inbound_signup`, `theta_analytics`, `purchased_licensed` or `manual_entry`.
  What is *absent* from that set is the control: `public_registry`,
  `official_website`, `open_government_data` and `industry_directory` are all
  fine for an FPO name, a CIN, a registration date or a director's DIN — and
  none of them is a lawful basis for a named person's mobile number.
  `test_pii_source_kinds_exclude_scraped_registries` duplicates the list on
  purpose, so widening it fails a test and has to be argued for in the diff.

- **🔴 The gate is on the contact point too, not only on the person.** Gating
  creation alone leaves the interesting half open: a person created lawfully,
  then a mobile attached to them from a scraped registry. This is the SFAC CEO
  block expressed as a runtime check rather than as a collector that currently
  happens to skip it.

- **🔴 Unmasking is a request parameter, never a client-side filter.** A payload
  carrying the full number for the UI to hide would be a control any `curl`
  walks straight past, and `audit.data_access_log` would record a view that did
  not happen alongside one that did. `?unmask=true` needs `contact.view_full`
  (the same three roles as `BILLING_OVERRIDE`) and writes the log row *before*
  the response is assembled — a caller who asked has asked, whether or not the
  person turned out to have any contact points.

- **One mask, two call sites.** `domain/pii.mask_phone` and
  `admin/rendering.mask_phone` must produce the same string, and a test asserts
  it. Two masks differing by one digit are one bug away from one of them being
  reversible.

- **The list endpoint carries no contact values at all.** A directory of names
  is a working tool; a directory of mobiles is the thing that gets exported
  once and lives on a laptop forever. Contact values are reachable only per
  person, which is what makes the audit log readable afterwards.

- **🔴 R10 counts rows returned, not rows matched.** A paginated walk is the
  shape an exfiltration takes, so each page is its own logged read and each
  page over the threshold needs its own typed reason.

- **A role ends by being dated. There is no delete.** `test_router_exposes_no_
  role_delete` reads the router and asserts no DELETE method exists, for the
  same reason `test_admin.py` proves the console cannot issue: the guarantee is
  the *absence* of a code path, and absence is not something a request can
  demonstrate.

- **The one-open-primary-contact rule is left to the database.** `uq_por_primary`
  is a partial unique index; a read-then-write check in Python races and the
  index does not. The route turns the `IntegrityError` into a 409 with a
  sentence naming the organisation.

- **Phones normalise to E.164 or are rejected.** A trunk zero is stripped
  before the country code (`091 98765 43210` and `+91 98765 43210` are the same
  number), and anything that will not reduce to ten digits raises rather than
  guesses. Same rule as bigha-to-hectare: a wrong value stored silently
  surfaces months later as an undeliverable message counted against the
  WhatsApp quality rating, by which time nobody can tell which import did it.

- **🔴 `core.person.full_name` collapses whitespace, it does not just trim it.**
  The original DDL was `btrim(first || ' ' || middle || ' ' || last)`, which
  yields `'Sunita  Devi'` — two spaces — for every row without a middle name,
  which is most rows. That string is what `idx_person_name_trgm` indexes and
  what every name search compares against, so the defect sat directly in the
  path Doc 07 warns about for Indian name matching. Fixed in `schema.sql` with
  a `regexp_replace`; an existing database needs the generated column dropped
  and re-added (the index with it). Caught because a test asserted the obvious
  expected value rather than the value the schema happened to produce.

### Invoice module decisions worth knowing (I-7 → I-10)

The advanced billing module is built. `INVOICE.md` §12–13 is the spec and
`backend/README.md` has the full account; these are the decisions a change would
undo by accident.

- **The copilot's action vocabulary is the trust boundary.**
  `crm.ai_proposal_action` has four members and none of them is `issue`,
  `cancel`, `record_payment` or `send`. An action the copilot cannot *name* is
  one it cannot request. Adding a member is a schema change a person makes on
  purpose, and it invalidates the guarantee the tests assert.

- **Unsafe requests are screened before a provider is called**
  (`providers/copilot.guard_intent`), and the refusal is stored as a failed
  proposal. A model that declines is one prompt away from not declining; a
  refusal nobody counts is one nobody can prove kept happening.

- **Confirmation binds to a hash that includes the before-state.** Proposals,
  delivery previews and reminder batches all work this way. A draft edited
  between showing and confirming produces a different hash, and the apply is
  refused rather than overwriting an edit nobody reviewed.

- **The proposal patch allow-list is an allow-list, not a deny-list.** Money,
  numbering and status fields are absent from it, and a patch naming one is
  *rejected* rather than stripped — an ignored field is a change the human
  approved in the diff and the system did not make.

- **🔴 The pre-issue checks run inside `issue_invoice`, not only on the
  screen.** A client that skips `/checks/` still cannot issue against a
  malformed GSTIN. A control that depends on a UI remembering to ask is not a
  control.

- **`not_available` is a first-class check result.** Operation logs land in
  Phase 3 and the satellite cross-check in Phase 5; until then those checks say
  so. Collapsing them into a pass would be a false assurance about the exact
  question this system exists to answer — did we bill for more acres than we
  sprayed.

- **`verification_unavailable` is never "valid" and is never cached.** A GSTIN
  service that answers "probably fine" when it cannot reach the registry
  produces a confident record of a check that did not happen.

- **A payment request is not a payment.** Only a human-entered receipt or a
  signed webhook matching amount, currency *and* reference creates an
  `invoice_payment`. Anything ambiguous goes to the reconciliation queue;
  guessing will eventually guess wrong on a large number.

- **Webhooks are stored before they are trusted**, unique on
  `(provider, provider_event_id)`. A handler that returns early on a bad
  signature keeps no record of what it rejected.

- **Consent is re-checked at dispatch, not at preview** (R7), and an opt-out
  cancels the delivery rather than failing it — an opt-out is a decision, not
  an error, and counting it as one makes the failure rate meaningless.

- **`sql/schema_invoice_advanced.sql` is idempotent and `schema.sql` is not.**
  Use `make db-migrate` against anything holding real rows; `make db-apply`
  runs the destructive path and expects empty schemas. Never add `schema.sql`
  to the migrate list.

- **Every external provider has a deterministic fake and defaults to it.** That
  is why the safety suite runs on every commit at no cost — a safety test
  skipped for want of a key looks like coverage and is not. Naming a live
  provider without credentials raises at startup rather than falling back,
  because a silent fallback shows fixture data as a live result.

- **Nothing files anything.** The Tally/Zoho exports and the GSTR-1 sheet are
  working papers, and the payloads say so in a `not_a_filing` field. A test
  greps the export module for affirmative filing language and self-checks its
  own patterns.

🔴 **Four port bugs worth knowing about, because they share a shape.** Each
passed every test and failed in a browser — the suite exercised the JSON paths
while the breakage lived in paths nothing called. Full account in
`backend/README.md`; the rules that came out of them:

- **A route must never redirect.** FastAPI's trailing-slash 307 points at an
  absolute backend URL, browsers strip `Authorization` across origins, and the
  result reads in the log like an expiring session. Register both forms.
  `test_no_api_route_redirects_on_a_trailing_slash` walks every route.
- **A template that renders is not a template that was ported.** All three
  invoice templates still carried Django syntax; the error only surfaces at
  render time. Every template is now rendered by a test.
- **A preview is not a create.** The live preview fires per keystroke, so it
  takes either entity identifier and tolerates half-typed lines. Creating an
  invoice keeps the strict shape — the leniency is confined to a path that
  saves nothing.
- **A missing optional package must not change behaviour.** Absent `pypdf`
  silently rerouted computer-generated PDFs to the vision path, which is the
  one measured fabricating an invoice. It now refuses.

**The console (`/admin`)** is server-rendered over the same domain layer. It is
read-heavy and write-narrow: it cannot issue, cancel or record a payment, and a
test reads the source to prove those code paths do not exist. Its session is
the same JWT the API issues, with the same MFA rule — a second auth system
would be a second set of bugs.

---

**Current phase:** Phase 1. See `agri-crm-docs/15-execution-plan.md`.

---

## 🔴 Non-negotiable rules

These are compliance controls, not preferences. Violating one is a legal or commercial failure, not a bug. Full reasoning in Doc 05 and Doc 12.

| # | Rule |
|---|---|
| **R1** | A collector asserts `dq.source.is_approved` before its first request. If false, raise and exit non-zero. |
| **R2** | Collectors set a descriptive `User-Agent` with a contact email, respect `robots.txt`, rate-limit to ≤1 req/sec. |


| **R6** | Outbound recipients come **only** from `comm.v_messageable_farmer`. Never from `core.farmer`. Enforce with a CI grep in `apps/communications`. |
| **R7** | Consent is re-checked **at dispatch time**, not only at segment preview. |
| **R8** | Aadhaar is salted SHA-256 + last 4 only. Plaintext never touches DB, log, export or cache. **Preferably do not collect it at all in v1.** |
| **R9** | PII is masked by default in the UI. Unmasking needs `contact.view_full` and writes `audit.data_access_log`. |
| **R10** | Exports over 1,000 PII records require a typed reason and trigger an alert. |
| **R11** | Staging and dev **never** contain production PII. |
| **R12** | Logs retained 1 year (DPDP Rule 6); PII scrubbed from application logs and Sentry. |
| **R13** | Breach runbook exists and is drilled: Board notified on discovery, individuals within 72 hours. |

### The scraping question — settled

The original ask was to scrape every farmer in India (name, phone, email, address, land area). **build that.** Doc 05 is the full argument; the short version:

1. **It kills the WhatsApp channel first.** Cold lists produce 5–15% block/report rates → Meta drops quality to Yellow → Red → WABA disabled. Appeals almost never succeed.
2. **DPDP Act 2023 + Rules 2025 is live law with a live regulator.** Penalties to ₹250 crore. The "publicly available" exemption (s.3(c)(ii)) applies only where *the data principal themselves* published it — a farmer in a state subsidy portal did not.
3. **It makes the asset worthless in diligence.** "We scraped it" ends the conversation with any mill, bank, carbon buyer or acquirer.

**What to build instead:** institutional data is fair game (MCA director names + DINs published by statute, SFAC/NABARD lists, ISMA/NFCSF directories). Aggregate data is fair game (data.gov.in, AGMARKNET) and gives targeting without holding one unconsented number. Volume comes from **partnership ingestion** — one FPO MoU with a consent clause = ~1,200 clean records; 500 FPOs over two years = 600,000 consented farmers.

> A scraped list of a million is worth less than a consented list of fifty thousand.

### Collectors — what is built (Aug 2026)



| Module | Does |
|---|---|
| `fetch.py` | The only route to the internet. Rate limit, robots.txt, User-Agent. |
| `base.py` | R1/R4 enforcement, then hands off to a subclass's `collect()`. |
| `sfac.py` | SFAC state-wise FPO lists — name, CIN, district, registration date, address. |
| `upsert.py` | Writes to `core.organisation` at confidence 0.60, with provenance. |

Run one: `make collector ARGS="--dry-run"` (or
`python -m backend.collectors.run sfac --dry-run`), then without the flag.
`--states Bihar Goa` and `--limit N` narrow it. An unapproved source exits
non-zero, which is R1 doing its job in a scheduled run.

**🔴 Scrapfly is a fetcher, not a way past a site.** `SCRAPFLY_API_KEY` routes
requests through Scrapfly for a stable Indian egress IP. Its anti-bot (`asp`)
and CAPTCHA features are sent explicitly `false` and a test asserts it — R3
forbids them. A site that cannot be read without defeating its protections is
a site telling us not to read it; the answer there is a licence or a
partnership, not a better scraper.

**🔴 The SFAC PDFs contain a CEO  — name, mobile, personal email — ** Three reasons, in order of weight: the register
row for `sfac_fpo_list` says "Organisational data only" and taking the block
would make that row false; a mobile number belonging to a named person is
personal data under DPDP whether or not they run a company; and `core.person`
exists for the human beings, populated from a partner agreement or a field
visit with a consent state attached, which is what makes those rows usable
afterwards. `strip_personal()` runs over every field including the name, so a
column shift cannot leak one either — that guard exists because an early
version did exactly that, and a test now holds it shut.

Changing this is a decision for a named person with legal advice, recorded by
editing the source register row. The collector refuses to run the moment that
row says `contains_pii = true`.



---

## Tech stack

| Layer | Choice |
|---|---|
| Database | PostgreSQL 16 + PostGIS + `pg_trgm` + `btree_gist` + `unaccent` |
| Backend | **FastAPI + SQLAlchemy 2 (async) + Pydantic v2 + Uvicorn.** Auth is JWT (`python-jose`) + TOTP (`pyotp`) issued by the service itself. Django 5 + DRF is retired — see below. |
| Async | Celery 5.4 + Redis 7 (queues: `default`, `import`, `heavy`, `messaging`) |
| API docs | FastAPI → OpenAPI 3.1 (`/api/docs`) → generated TypeScript client. `make schema-doc` regenerates the committed `openapi.yaml`. |
| Frontend | React 19 + TypeScript + Vite + TanStack Query + shadcn/ui + Tailwind |
| Data grid | AG Grid Community (100k-row views) |
| Maps | MapLibre GL JS + self-hosted vector tiles |
| Mobile | React Native (Expo) + WatermelonDB / expo-sqlite, offline-first |
| Messaging | WhatsApp Business Cloud API (direct with Meta) + Amazon SES |
| Hosting | AWS **ap-south-1 (Mumbai)** — ECS Fargate, RDS Multi-AZ, ElastiCache, S3 |
| Data-ops console | Server-rendered Jinja2 at `/admin`, over the same domain layer as the API |
| IaC / CI | Terraform 1.9+ · GitHub Actions · Sentry + CloudWatch + Grafana |

**Why FastAPI:** the three things Django was chosen for have each been
answered, and the reasons it was *not* chosen have not gone away.

- **Admin.** Django Admin was valued at roughly three months of frontend work
  and was the single strongest argument for Django. `/admin` is that argument
  answered: server-rendered over the same domain layer, read-heavy and
  write-narrow, leading with collected data and its provenance rather than
  with a table of every column. It cannot issue an invoice, cancel one, or
  record a payment, and a test reads the source to prove those code paths do
  not exist — a guarantee Django Admin's default CRUD could not give.
- **Migrations.** They were never used for the business schema. `ref`, `core`,
  `comm`, `crm`, `dq` and `audit` are owned by DDL, because the partitioning,
  generated columns and triggers *are* the compliance controls and the ORM
  cannot express them. Django's migration story only ever covered Django's own
  auth and session tables — which is to say, tables that existed because
  Django did.
- **Python.** Unchanged. Collectors, dedupe and the Phase 5 satellite
  cross-check still live in the same language as the API.

What FastAPI adds that mattered here: native async against `asyncpg` for the
long-running import and export paths, Pydantic v2 as one schema definition for
validation *and* the OpenAPI contract the frontend generates from (no
`drf-spectacular` layer that can describe a route it does not actually serve),
and no second auth stack — `python-jose` + `pyotp` replaced simplejwt +
django-otp + axes, and the rate limiting axes provided is a middleware.

**What it cost, honestly:** a substantial and unbudgeted detour, roughly what
Doc 03 predicted when it listed FastAPI as the rejected second choice. Four bugs shipped that
every test passed and a browser caught — the trailing-slash 307, three
templates still carrying Django syntax, a preview endpoint stricter than the
form it served, and a missing optional package silently changing behaviour.
The rules that came out of them are below and in `backend/README.md`.

**Django is gone.** Removed on 30 Aug 2026 — deleted, not merely stopped.
There is no `manage.py`, no `backend/apps/`, no Django in any requirements file
or virtualenv. `.venv` at the repo root is the only environment.

🔴 **`backend/` is the FastAPI service, not the old Django tree.** The package
was called `api/` for most of the port and was renamed to `backend/` on
31 Aug 2026; the Django code that used to live at `backend/apps/` was deleted
first. So the name is reused, and every path in this file means the FastAPI
package: `backend.main:app`, `backend/routers/`, `backend/domain/`. If you find
a document still saying `api/`, it predates the rename.

Before it went, the two parity suites were run one last time and confirmed
every Django endpoint had a FastAPI counterpart. Those suites then had nothing
left to compare, so their *assertions* were rewritten as freestanding ones and
the comparison was dropped:

- `backend/tests/test_legacy_hashes.py` — 🔴 **the one that matters.** The database
  still holds Django-written PBKDF2 hashes and nothing rewrites them; a hash
  cannot be recomputed without the plaintext, so a user's password is only ever
  re-hashed when they next change it, which for most people is never. Real
  hashes captured from Django 5.2.17 are pinned there. If that file goes red,
  every existing account is locked out.
- `backend/tests/test_conventions.py` — Indian money grouping, the invoice-status
  vocabulary checked against the live database enum, the MFA-required roles,
  and the exact acre-to-hectare conversion.

A copy of the deleted tree is at `../TF-TE-django-archive/` on this machine
only. It is not in git, is not a fallback, and nothing may import from it.

**Rejected and why:** MySQL (weak geo, no trigram) · MongoDB (the graph is the product; document stores make every relationship a manual join, and you lose transactional consent guarantees) · Neo4j (second DB for a 4-hop graph recursive CTEs handle) · Django + DRF (chosen first, for Admin and migrations; retired once `/admin` replaced the former and DDL turned out to own the latter — see above) · Odoo/SuiteCRM (you would fight its Company→Contact→Deal model longer than building the right one).

---

## Schema map

| Schema | Contains |
|---|---|
| `ref` | Geography (state/district/block/village, **LGD-coded**), crops, varieties |
| `core` | Organisations, people, roles, contact points, farmers, land, crops, documents |
| `comm` | Consent ledger, suppression, templates, campaigns, messages, inbound |
| `crm` | Projects, leads, opportunities, agents, territories, visits, activities, tasks |
| `dq` | Sources, field provenance, contradictions, imports, merges, dedupe candidates |
| `audit` | Change log, data-access log, DSR requests |

### Design decisions baked into the DDL — do not undo these

- **One `core.organisation` table** with an `org_type` discriminator + `fpo_profile` / `sugar_mill_profile` / `cooperative_profile` extensions. Three separate tables would triple every join, search and permission rule.
- **`core.farmer` is `PARTITION BY LIST (state_id)`.** Composite PK `(id, state_id)`; every child FK carries `(farmer_id, farmer_state_id)`. 🔴 **Every query against `core.farmer` must include `state_id`** or Postgres scans all partitions — the API enforces this by requiring `state` on `/farmers/`.
- **`comm.consent_event` is append-only** — a trigger raises on UPDATE/DELETE. Current state lives in `comm.consent_current`, maintained by trigger.
- **`comm.suppression` outranks a fresh opt-in** (smoke test 8). This is what protects you when someone re-imports an old list containing a number that complained.
- **People are not contacts-of-a-company.** `core.person` + `core.person_org_role` with `valid_from`/`valid_to`. Close old role rows; never overwrite.
- **Contact points have their own lifecycle** — a phone is a row with verification state, delivery-failure counter and source, not a column on a person. Rural phone churn is 15–20%/year.
- **Nothing is hard-deleted.** `is_deleted`, `merged_into_id`, `dq.merge_event` with a full JSONB snapshot so merges reverse.
- **Derived, not entered:** `farmer_class` (trigger, from `total_area_ha`), `person.full_name` (generated), `opportunity.weighted_value_inr` (generated).
- **Monthly RANGE partitions** on `comm.message`, `crm.activity`, `audit.change_log`, `audit.data_access_log`.

**Do not hand-edit `sql/schema.sql` without re-running `sql/smoke_test.sql`.** It takes under a second and catches trigger regressions unit tests miss.

---

## Conventions

- `snake_case` for all database identifiers
- All timestamps `timestamptz`, stored UTC, displayed Asia/Kolkata
- All money `numeric(14,2)` in INR unless a `currency` column says otherwise
- 🔴 **All area in hectares** `numeric(10,4)`. Acres/bigha/guntha are input conveniences converted at the edge. A bigha varies by state (~0.25 ha WB, ~0.625 ha parts of UP, ~0.16 ha UK) — use a state-keyed table, **reject rather than guess**.
- Store `name_local` alongside `name_en` for every person and org (Devanagari preserved verbatim)
- Phone normalisation → E.164 `+91XXXXXXXXXX`; always query `value_normalised`
- Dates: parse Indian conventions explicitly with `dayfirst=True`
- `MUST` / `SHOULD` / `MAY` follow RFC 2119
- 🔴 marks a compliance-critical requirement

### Engineering standards

`ruff` (lint + format, config in `backend/ruff.toml`) · `mypy --strict` on new modules · `pytest` + `pytest-asyncio` + `httpx.AsyncClient` against a real Postgres, run as `make test` · **≥80% coverage on `backend/collectors` and the consent paths in `backend/domain`** (the two places a bug becomes a legal problem) · `eslint` + `prettier` + `tsc --noEmit` · secrets in AWS Secrets Manager only, `gitleaks` in pre-commit · trunk-based, short-lived branches, PR + 1 review.

---

## Build phases

Full detail in `agri-crm-docs/15-execution-plan.md`. A phase ends when its **exit gate** passes, not when someone decides it is done.

🔴 **No phase carries a duration, deliberately.** An estimate beside a task is
read as a commitment, and the commitment then decides when the phase ends
instead of the gate doing it. The order below is the contract; how long each
takes is whatever it takes.

| Phase | Deliverable |
|---|---|
| **Track P** | 🔴 Lawyer · Theta legacy audit · Meta verification · BD partnership outreach |
| 0 · Foundation | Repo, CI, Terraform, schema applied, smoke test green |
| 1 · Org Registry | FPO + ACS + mill registry with **real data**, bulk import, collectors |
| 2 · Farmer Core | Farmer master, land, consent ledger, Theta import at audited tiers |
| 3 · Commercial | Project Registry, BD Tracker, Agent Tracker, **RLS tested** |
| 4 · Engagement | WhatsApp + Email, templates, campaigns, opt-out |
| 5 · Intelligence | Quality scoring, dedupe, **satellite cross-check** |
| 6 · Field App | Offline React Native app for agents |
| 7 · Scale & harden | Partitioning automation, pen test, DR drill, load test |

🔴 **Track P starts immediately regardless of engineering phase.** All four wait on somebody outside the team and each blocks a later phase.

---

## Quality tiers — the "useful vs not useful" layer

Every farmer, org and person carries a `quality_tier`:

| Tier | Criteria | Use |
|---|---|---|
| 🥇 **Gold** | Verified <180 days · completeness ≥70 · consent recorded · no contradictions · no recent delivery failures | Campaigns, client-facing counts, proposals |
| 🥈 **Silver** | Authoritative source · completeness ≥45 · verified 180–540 days ago or never by us | Market sizing, territory planning, verification queue |
| 🥉 **Bronze** | Completeness <45, or only inferred/manual/unaudited source, or >540 days | A lead, not a fact. **Never messaged.** |
| 🚫 **Quarantine** | ≥3 delivery failures · unresolved contradiction >30 days · unapproved source · DSR erasure in flight | Excluded from search, campaigns and counts. **Never silently deleted.** |

Targets at 12 months: Gold 15–25% · Silver 40–50% · Bronze 25–35% · Quarantine <5%.

**The number that matters:** verification throughput vs. decay rate. If you verify 4,000/week and 6,000 decay, the database is getting worse while the row count grows.

**The differentiator:** Theta already runs satellite analytics. Farmer declares 3.5 ha of cane → imagery shows 2.1 ha → contradiction raised → agent GPS-walks the boundary → 2.3 ha verified → tier Gold. No competitor with a scraped list can do this at all. Phase 5.

---

## Document map

| # | Doc | Read for |
|---|---|---|
| 00 | `00-executive-summary.md` | The whole system in 10 minutes |
| 01 | `01-product-requirements.md` | Personas, modules, user stories, NFRs, **the out-of-scope contract** |
| 02 | `02-data-model.md` | Every table, every column, the ER diagram |
| 03 | `03-tech-stack.md` | Stack choices *with rejected alternatives* · repo layout §9 |
| 04 | `04-architecture.md` | Services, request flows, scaling, observability |
| 05 | `05-data-sourcing-and-legal.md` | 🔴 **Read before writing any collector.** Approved sources, DPDP, engineering rules R1–R13 |
| 06 | `06-ingestion-pipeline.md` | LAND→NORMALISE→VALIDATE→MATCH→UPSERT→PROVENANCE→SCORE, entity resolution |
| 07 | `07-data-quality-organic.md` | Tiers, completeness scoring, decay, coherence rules, verification methods |
| 08 | `08-fpo-acs-registry.md` | Field-level spec for FPO / mill / society records |
| 09 | `09-project-registry-and-trackers.md` | Projects, BD pipeline, agent tracker, activity feed |
| 10 | `10-communication-whatsapp-email.md` | Consent architecture, WhatsApp, SES, campaigns |
| 11 | `11-api-spec.md` | REST contract for every resource |
| 12 | `12-security-rbac.md` | Roles, RLS, masking, audit, retention, incident response |
| 13 | `13-roadmap-and-phases.md` | Phase order, team, risks |
| 14 | `14-cost-estimate.md` | Infra + messaging + people, monthly and annual |
| 15 | `15-execution-plan.md` | **Phase-by-phase tasks, preconditions and exit gates** |

---

## Things that will bite you

- **Name ambiguity is severe.** "Ram Kumar" in one district may be 400 distinct people. Minimum disambiguation key: name + father's/spouse's name + village + phone. `father_or_spouse` is not optional in practice.
- **Indian name matching needs preprocessing** — transliteration variants (Chaudhary/Chaudhri/Choudhary), honorific stripping, token-set not ordered-string comparison, Devanagari↔Latin normalisation, org suffix stripping (Ltd/FPC/Sahkari).
- **Landholding is self-reported and inflated** by 20–40%. Always store `area_source` (self_declared / document / satellite).
- **Never let a bulk import overwrite a human-verified value.** Field-verified = confidence 0.95; scraped registry = 0.60. The upsert rule requires incoming confidence > existing + 0.15, else it writes a contradiction.
- **The 24-hour WhatsApp service window** — outside it, only approved templates. Utility category is ₹0.115 vs marketing ₹0.8631 (**7.5×**), and utility is also what protects your quality rating.
- **Mill sales calendar is seasonal.** Decision-makers are unreachable during crushing (Nov–Apr) and available May–September.
- **"20 farmers sharing one phone"** — a field agent or input dealer entering their own number for everyone they register. Only a coherence rule finds it, and it quietly destroys thousands of records.
- **`state_id` in every farmer query.** Enforce in the query helper that builds the `core.farmer` select, and assert partition pruning in tests. `/farmers/` requires `state` for this reason.

---

## Working agreements for agents in this repo

- The out-of-scope list in Doc 01 §1 is a **contract** (no accounting, trading, payments, satellite analytics rebuild, farmer-facing app in v1). Revisit only at phase boundaries.
- Prefer additive migrations. Add nullable → backfill in batches → add NOT NULL → drop old, across separate deploys. Never drop a column in the same release that stops writing to it.
- New enum values are appended, never renamed or removed.
- Any index on a table over 1M rows uses `CREATE INDEX CONCURRENTLY`.
- One router per bounded context in `backend/routers/`, with its SQLAlchemy models in
  `backend/models/` and its Pydantic schemas in `backend/schemas/`. **Do not create a
  `common` or `utils` module** — it becomes a dumping ground and everything
  imports it circularly. Shared compliance logic goes in `backend/domain/`, which
  is named for what it holds and is allowed to have no router at all.
- 🔴 **There is no Django.** `backend/` is the FastAPI package, not the old
  Django tree of the same name. If you find yourself reaching
  for a management command, an ORM migration or an admin registration, the
  equivalents are `backend/cli.py`, a reviewed SQL file under
  `agri-crm-docs/sql/` applied by `make db-migrate`, and `backend/admin/`.
- When touching `backend/collectors/` or anything under `backend/domain/` (consent,
  PII masking, scoping, redaction), assume the change has legal consequences
  until proven otherwise.
