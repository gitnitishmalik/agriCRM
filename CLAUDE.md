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

**Phase 0 (Foundation) complete.** Phase 1 (Organisation Registry) is next.

| Path | Status |
|---|---|
| `agri-crm-docs/*.md` | 16 documents, complete |
| `agri-crm-docs/sql/schema.sql` | Applied and running locally on PostgreSQL 16 + PostGIS |
| `agri-crm-docs/sql/smoke_test.sql` | 15/15 green. Wired into CI and `make smoke`. |
| `backend/` | Django 5.2 + DRF. 12 app packages, only `accounts` implemented. |
| `infra/docker/` | Local Postgres + Redis. **Ports 5433 / 6380**, not the defaults. |
| `infra/terraform/` | Scaffolded and documented, **not applied** — needs AWS credentials |
| `.github/workflows/ci.yml` | 4 jobs: schema, lint, compliance, test |
| `frontend/` `mobile/` | Do not exist. Phase 2 and Phase 6. |

**What works right now:** `make bootstrap` → `make run` gives you Django Admin,
Swagger UI, and a working JWT + TOTP auth flow. 28 tests pass.

### Phase 0 decisions worth knowing

- **Django 5.2 LTS, not the 5.0 named in Doc 03** — 5.0 does not support Python 3.13.
- **Postgres on 5433, Redis on 6380.** A local PostgreSQL install commonly holds
  5432 and silently wins the connection, producing a confusing auth failure.
- **The business schema is owned by `sql/schema.sql`, not by Django models.**
  Django manages only its own tables (auth, sessions, celery beat). The
  `ref`/`core`/`comm`/`crm`/`dq`/`audit` schemas are applied by DDL and get
  `managed = False` models in Phase 1. The DDL carries partitioning, generated
  columns and triggers the ORM cannot express — and those *are* the compliance
  controls.
- **`apps/accounts.User` is defined in full already.** `AUTH_USER_MODEL` cannot
  be swapped after the first migration, so `role` and `district_ids` exist now
  even though RLS itself lands in Phase 3.
- **`openapi.yaml` is committed.** Regenerate with `make schema-doc`; a diff in
  review is how contract drift becomes visible.

**Current phase:** Phase 1. See `agri-crm-docs/15-execution-plan.md`.

---

## 🔴 Non-negotiable rules

These are compliance controls, not preferences. Violating one is a legal or commercial failure, not a bug. Full reasoning in Doc 05 and Doc 12.

| # | Rule |
|---|---|
| **R1** | A collector asserts `dq.source.is_approved` before its first request. If false, raise and exit non-zero. |
| **R2** | Collectors set a descriptive `User-Agent` with a contact email, respect `robots.txt`, rate-limit to ≤1 req/sec. |
| **R3** | **No collector authenticates, solves a CAPTCHA, or evades a rate limit.** If it needs to, stop and ask legal. |
| **R4** | Personal data enters only via `partner_agreement`, `field_collection`, `inbound_signup`, or an approved `theta_analytics` / `purchased_licensed` batch. |
| **R5** | An import cannot commit unless `legal_basis_confirmed = true`, set by a named user. |
| **R6** | Outbound recipients come **only** from `comm.v_messageable_farmer`. Never from `core.farmer`. Enforce with a CI grep in `apps/communications`. |
| **R7** | Consent is re-checked **at dispatch time**, not only at segment preview. |
| **R8** | Aadhaar is salted SHA-256 + last 4 only. Plaintext never touches DB, log, export or cache. **Preferably do not collect it at all in v1.** |
| **R9** | PII is masked by default in the UI. Unmasking needs `contact.view_full` and writes `audit.data_access_log`. |
| **R10** | Exports over 1,000 PII records require a typed reason and trigger an alert. |
| **R11** | Staging and dev **never** contain production PII. |
| **R12** | Logs retained 1 year (DPDP Rule 6); PII scrubbed from application logs and Sentry. |
| **R13** | Breach runbook exists and is drilled: Board notified on discovery, individuals within 72 hours. |

### The scraping question — settled

The original ask was to scrape every farmer in India (name, phone, email, address, land area). **Do not build that.** Doc 05 is the full argument; the short version:

1. **It kills the WhatsApp channel first.** Cold lists produce 5–15% block/report rates → Meta drops quality to Yellow → Red → WABA disabled. Appeals almost never succeed.
2. **DPDP Act 2023 + Rules 2025 is live law with a live regulator.** Penalties to ₹250 crore. The "publicly available" exemption (s.3(c)(ii)) applies only where *the data principal themselves* published it — a farmer in a state subsidy portal did not.
3. **It makes the asset worthless in diligence.** "We scraped it" ends the conversation with any mill, bank, carbon buyer or acquirer.

**What to build instead:** institutional data is fair game (MCA director names + DINs published by statute, SFAC/NABARD lists, ISMA/NFCSF directories). Aggregate data is fair game (data.gov.in, AGMARKNET) and gives targeting without holding one unconsented number. Volume comes from **partnership ingestion** — one FPO MoU with a consent clause = ~1,200 clean records; 500 FPOs over two years = 600,000 consented farmers.

> A scraped list of a million is worth less than a consented list of fifty thousand.

---

## Tech stack

| Layer | Choice |
|---|---|
| Database | PostgreSQL 16 + PostGIS + `pg_trgm` + `btree_gist` + `unaccent` |
| Backend | Django 5 + Django REST Framework 3.15 |
| Async | Celery 5.4 + Redis 7 (queues: `default`, `import`, `heavy`, `messaging`) |
| API docs | drf-spectacular → OpenAPI 3.1 → generated TypeScript client |
| Frontend | React 18 + TypeScript + Vite + TanStack Query + shadcn/ui + Tailwind |
| Data grid | AG Grid Community (100k-row views) |
| Maps | MapLibre GL JS + self-hosted vector tiles |
| Mobile | React Native (Expo) + WatermelonDB / expo-sqlite, offline-first |
| Messaging | WhatsApp Business Cloud API (direct with Meta) + Amazon SES |
| Hosting | AWS **ap-south-1 (Mumbai)** — ECS Fargate, RDS Multi-AZ, ElastiCache, S3 |
| IaC / CI | Terraform 1.9+ · GitHub Actions · Sentry + CloudWatch + Grafana |

**Why Django:** Admin is a working data-ops console on day one (worth ~3 months of frontend work for a data-curation system), trustworthy migrations for a schema that will change 40× in year one, and Python matches Theta's data-science stack — collectors, dedupe models and the satellite cross-check live in the same language as the API.

**Sequencing hedge worth taking:** ship Django Admin as the entire v1 UI. Schema + ingestion + collectors + admin live in ~10 weeks, real data in month three, then build React in Phase 2 around screens people have actually used.

**Rejected and why:** MySQL (weak geo, no trigram) · MongoDB (the graph is the product; document stores make every relationship a manual join, and you lose transactional consent guarantees) · Neo4j (second DB for a 4-hop graph recursive CTEs handle) · FastAPI (rebuild admin/auth/permissions/migrations — legitimate second choice, budget +6–8 weeks) · Odoo/SuiteCRM (you would fight its Company→Contact→Deal model longer than building the right one).

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

`ruff` (lint + format) · `mypy --strict` on new modules · `pytest` + `pytest-django` + `factory_boy` · **≥80% coverage on `apps/communications` and `apps/dataquality`** (the two places a bug becomes a legal problem) · `eslint` + `prettier` + `tsc --noEmit` · secrets in AWS Secrets Manager only, `gitleaks` in pre-commit · trunk-based, short-lived branches, PR + 1 review.

---

## Build phases

Full detail in `agri-crm-docs/15-execution-plan.md`. A phase ends when its **exit gate** passes, not when its weeks run out.

| Phase | Weeks | Deliverable |
|---|---|---|
| **Track P** | 1 → ongoing | 🔴 Lawyer · Theta legacy audit · Meta verification · BD partnership outreach |
| 0 · Foundation | 1–3 | Repo, CI, Terraform, schema applied, smoke test green |
| 1 · Org Registry | 4–9 | FPO + ACS + mill registry with **real data**, bulk import, collectors |
| 2 · Farmer Core | 10–15 | Farmer master, land, consent ledger, Theta import at audited tiers |
| 3 · Commercial | 16–22 | Project Registry, BD Tracker, Agent Tracker, **RLS tested** |
| 4 · Engagement | 23–29 | WhatsApp + Email, templates, campaigns, opt-out |
| 5 · Intelligence | 30–36 | Quality scoring, dedupe, **satellite cross-check** |
| 6 · Field App | 37–44 | Offline React Native app for agents |
| 7 · Scale & harden | 45–52 | Partitioning automation, pen test, DR drill, load test |

🔴 **Track P starts week 1 regardless of engineering phase.** All four have multi-week external lead times and each blocks a later phase.

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
| 13 | `13-roadmap-and-phases.md` | 52-week roadmap, team, risks |
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
- **`state_id` in every farmer query.** Enforce with a Django manager and assert partition pruning in tests.

---

## Working agreements for agents in this repo

- The out-of-scope list in Doc 01 §1 is a **contract** (no accounting, trading, payments, satellite analytics rebuild, farmer-facing app in v1). Revisit only at phase boundaries.
- Prefer additive migrations. Add nullable → backfill in batches → add NOT NULL → drop old, across separate deploys. Never drop a column in the same release that stops writing to it.
- New enum values are appended, never renamed or removed.
- Any index on a table over 1M rows uses `CREATE INDEX CONCURRENTLY`.
- One Django app per bounded context. **Do not create `apps/core`** — it becomes a dumping ground and everything imports it circularly.
- When touching `apps/communications` or `apps/dataquality`, assume the change has legal consequences until proven otherwise.
