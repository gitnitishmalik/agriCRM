# 03 · Technology Stack

## 1. The recommendation, in full

| Layer | Choice | Version |
|---|---|---|
| Database | PostgreSQL + PostGIS + pg_trgm | 16.x / 3.4 |
| Cache, queue broker, rate limiter | Redis | 7.x |
| Object storage | AWS S3 (ap-south-1) | — |
| Backend framework | **FastAPI + SQLAlchemy 2 (async) + Pydantic v2** | 0.115+ / 2.0 / 2.x |
| Async workers | Celery + Celery Beat | 5.4 |
| Scraping / collectors | Scrapy + Playwright (only where JS rendering is required) | 2.11 |
| Data processing | pandas + Polars for large imports | — |
| Entity resolution | `dedupe` / `recordlinkage` + Postgres trigram pre-blocking | — |
| API docs | FastAPI's own OpenAPI 3.1 output | — |
| Frontend | React + TypeScript + Vite | 19 / 5.x / 7.x |
| Server state | TanStack Query | 5.x |
| UI components | shadcn/ui + Tailwind CSS | — |
| Data grid | AG Grid Community | 32.x |
| Maps | MapLibre GL JS + self-hosted tiles | 4.x |
| Charts | Recharts | 2.x |
| Mobile | React Native (Expo) + WatermelonDB or expo-sqlite | SDK 51+ |
| Auth | `python-jose` JWT (HS256) + `pyotp` TOTP MFA | — |
| Data-ops console | Server-rendered Jinja2 at `/admin` | — |
| Search (later) | OpenSearch | 2.x |
| Messaging | WhatsApp Business Cloud API (direct) + Amazon SES | — |
| Hosting | AWS ap-south-1: ECS Fargate, RDS, ElastiCache, S3, CloudFront | — |
| IaC | Terraform | 1.9+ |
| CI/CD | GitHub Actions | — |
| Monitoring | Sentry + CloudWatch + Grafana | — |

## 2. Database: PostgreSQL — and why not the alternatives

**Why Postgres wins here specifically:**

- **PostGIS** gives you cane command areas as real polygons, "farmers within 15 km of this mill", district choropleths, and GPS-validated field visits. This is not a nice-to-have in agri — geography *is* the business.
- **pg_trgm** gives fuzzy name matching inside the database. Indian entity names are a matching nightmare ("Bhainswal Kisan Producer Company Ltd" vs "Bhainsval Kisaan FPC"). Trigram indexes turn dedupe from a batch job into an interactive query. Smoke test 14 demonstrates it.
- **JSONB** absorbs the schema variation you will absolutely hit — every state publishes FPO data with different columns. Put the odd ones in `extra`, promote them to real columns when they prove durable.
- **Declarative partitioning** handles 10M+ farmer rows and 100M message rows without a second system.
- **Row-level security** enforces territory-based access at the database, so a bug in an API view cannot leak another region's data.
- **Everything in one place.** Geospatial + fuzzy search + relational + document, in one engine, one backup, one connection pool, one thing to operate. At your team size this is the decisive argument.

**Rejected alternatives:**

| Option | Why not |
|---|---|
| **MySQL / MariaDB** | Weak geospatial, no trigram indexes, inferior JSON, no partial indexes, no true partitioning ergonomics. You would end up bolting on Elasticsearch and PostGIS-equivalents anyway. |
| **MongoDB** | The core value of this system is the *relationship graph* — farmer↔FPO↔mill↔project↔person. Document stores make every one of those a manual join in application code. You would also lose transactional consent guarantees, which is a compliance problem, not a preference. |
| **Neo4j** | Genuinely tempting for the relationship graph. But it means running a second database for a graph that is at most 4 hops deep, which recursive CTEs in Postgres handle fine. Revisit only if you build real network analysis (influence propagation across FPO boards). |
| **Snowflake / BigQuery** | Analytical warehouses, not OLTP. Your CRM needs sub-200ms single-record reads. Add one *later* if you sell analytics products on top; the CRM is not it. |
| **Airtable / Google Sheets** | Row limits, no referential integrity, no consent enforcement, no audit trail. Fine for a 500-row pilot, catastrophic at 500,000. |

## 3. Backend: FastAPI — and why not the alternatives

> **Revised.** This document originally specified Django + DRF, and the system
> was built on it through Phase 0 and the first Phase 1 sprints. It is now
> FastAPI + SQLAlchemy 2 (async) + Pydantic v2, and Django is retired. The
> original argument and what actually happened to it are both kept below,
> because the reasoning was sound and knowing *which* premise failed is what
> makes the next such decision better.

**The original case for Django, and how each part resolved:**

| Original claim | What happened |
|---|---|
| Django Admin is a working data-ops console immediately, worth a substantial amount of frontend work | **True, and it was the right call for Phase 0.** It was replaced by `/admin` — server-rendered over the same domain layer, read-heavy and write-narrow. Critically it *cannot* issue an invoice, cancel one or record a payment, and a test reads the source to prove those paths do not exist. Django Admin's generic CRUD could not make that promise, and on a system with financial records that promise turned out to matter more than the free screens. |
| Migrations are trustworthy; you will change this schema forty times in year one | **The premise was wrong, not the claim.** The business schema is owned by `sql/schema.sql` and always was — the partitioning, generated columns and triggers *are* the compliance controls and no ORM can express them. Django's migrations only ever managed Django's own auth and session tables. The forty schema changes were forty reviewed SQL files either way. |
| Auth, permissions, RBAC, sessions, CSRF, password hashing, throttling — solved and not your problem | **Half true.** Password hashing was genuinely free, and its format is inherited verbatim (`api/security.py`) because the existing hashes are in it. The rest was three libraries — simplejwt, django-otp, axes — configured into a shape none of them defaulted to, because MFA-mandatory-by-role is not a thing any of them ship. That is now ~200 lines of `python-jose` + `pyotp` that does exactly the documented rule. |
| Python matches Theta's data-science stack | **Still true, and unaffected.** Collectors, dedupe and the Phase 5 satellite cross-check are in the same language as the API. This was never an argument for Django specifically. |
| DRF + drf-spectacular produces a typed OpenAPI schema | **Superseded.** drf-spectacular *describes* views it inspects, and a description can drift from what the view serves. Pydantic v2 models are the validation and the schema — one definition, no inspection step. |

**What FastAPI added that was worth the port:**

- **Async against `asyncpg`.** The import, export and PDF paths hold a request
  open for seconds. Under Django these each occupied a worker.
- **One schema definition.** The request model, the response model and the
  OpenAPI contract the frontend generates from are the same object.
- **A smaller trust surface for the AI features.** The copilot's action
  vocabulary, the patch allow-list and the confirmation hash are ordinary
  Pydantic types, and the tests assert properties of those types rather than
  properties of a model's behaviour.

**What it cost:** a substantial and unbudgeted detour — which is what the row below predicted
when FastAPI was the rejected alternative. The estimate was accurate. Four
bugs shipped that the whole suite passed and a browser caught (a trailing
slash 307 that stripped `Authorization`, three templates still holding Django
syntax, a preview endpoint stricter than the form calling it, and an absent
optional package silently changing an extraction path). See `api/README.md`.

**Rejected alternatives:**

| Option | Why not |
|---|---|
| **Django + DRF** | Chosen originally, for Admin and migrations; see the table above for how both resolved. It remains the right default for a CRM whose admin screens *are* the product and whose schema an ORM can express — this one is neither. |
| **Node/NestJS** | Perfectly capable. But it splits the language from the data pipelines, and Prisma's migration story is weaker for a schema this shaped (partitioned tables, generated columns, triggers). |
| **Laravel** | Strong admin ecosystem, but the Python data-tooling gap is the dealbreaker given Theta's existing work. |
| **Odoo / SuiteCRM / EspoCRM** | You *could* customise one. You would spend as long fighting its opinionated Company→Contact→Deal model as building the right model yourself, and the farmer/FPO/mill graph does not fit it. Reconsider only if your team is under two engineers. |

## 4. Frontend

**React + TypeScript + Vite.** TypeScript is non-negotiable on a 60-table domain model — the compiler is what stops "did that field come back as `total_area_ha` or `totalAreaHa`" bugs from reaching production. Generate the client types from the OpenAPI schema so backend changes break the frontend build rather than production.

**TanStack Query** for server state. A CRM is 90% server state; Redux is the wrong tool and adds a lot of ceremony for nothing.

**shadcn/ui + Tailwind.** You own the component source, so you can adapt the table, form and dialog primitives to a data-dense CRM rather than fighting a component library's opinions.

**AG Grid Community** for the big tables. You will have views with 100,000 rows, 30 columns, grouping, and inline edit. Hand-rolling that is a six-month project. The Community edition covers virtualisation, sorting, filtering, grouping and CSV export.

**MapLibre GL + self-hosted vector tiles.** Coverage maps by district, mill command areas, agent visit tracks. Self-hosted tiles avoid per-view billing at the volumes a field team generates. Mapbox GL JS moved to a proprietary licence; MapLibre is the open fork and is fine.

## 5. Mobile: React Native (Expo), offline-first

The field app is the highest-risk component, because rural connectivity is genuinely bad and an agent who loses a day's work stops using the app permanently.

**Architecture:**
- **WatermelonDB** (or `expo-sqlite` + a hand-rolled sync layer) as the local store
- Every record created offline carries a **client-generated UUID** — `crm.field_visit.client_uuid` is unique in the schema for exactly this reason. Retries are idempotent.
- **Pull sync** is cursor-based on `updated_at`, scoped to the agent's territory, paginated to under 100KB
- **Push sync** is a batch endpoint; the server returns per-record accept/reject so partial failures don't block the batch
- **Conflict resolution:** last-writer-wins *per field*, with every conflict written to a log an analyst can review. Silent conflict resolution in a data-quality system is how you lose trust in the data.
- Photos queue separately and upload on Wi-Fi by default

**Why not Flutter:** equally good technically; React Native wins on code and type sharing with the React web app, which matters more than raw performance for a forms-and-lists app.

**Why not a PWA:** iOS PWA storage eviction and background-sync limitations make it too risky for offline-critical work. Also, you need reliable GPS and camera access.

## 6. Messaging integration

**WhatsApp: Meta Cloud API directly, not through a BSP.**

At the volumes in [Doc 14](./14-cost-estimate.md), BSP markup (typically 15–30% on top of Meta's rate, or a per-seat platform fee) exceeds the cost of the engineering it takes to integrate the Cloud API directly. You need: template management, a send endpoint, and a webhook receiver. That is it.

Effective 1 July 2026, Meta's India per-message rates are approximately **₹0.8631 for marketing** and **₹0.115 for utility and authentication**. From 1 October 2026, service messages and in-window utility templates become billable at the utility rate rather than free. Your traffic should be overwhelmingly **utility** category — advisories, payment schedules, meeting notices — which is 7.5× cheaper than marketing and, not coincidentally, far less likely to get you blocked.

*Use a BSP (AiSensy, Gupshup, Interakt, WATI) only if* you need a shared team inbox with agent assignment on day one, or you have no backend capacity for the webhook work. It is a reasonable v1 shortcut; plan to migrate.

**Email: Amazon SES.** ~₹0.008/email at Indian volumes, in-region (ap-south-1), with SNS-delivered bounce and complaint notifications you must write back to `comm.suppression`. Configure SPF, DKIM and DMARC before your first send, and warm the sending domain gradually before sending at volume. Alternatives: Postmark (better deliverability, ~8× the cost — worth it for transactional only), Brevo/Zoho Campaigns (fine, but adds a data-residency question).

**SMS:** deferred to v2. India's TRAI DLT regime requires registering your entity, your sender IDs and every template content template before you can send. It is real work with an external lead time you do not control. Do it when you need it, not before. 🔴 SMS to numbers on the TRAI DND registry without valid consent carries regulatory penalties independent of DPDP.

## 7. Infrastructure

**AWS ap-south-1 (Mumbai).** 🔴 Data residency is both a compliance position and a sales argument when you talk to cooperative banks and government-linked buyers.

```
Route53 → CloudFront → ALB → ECS Fargate
                              ├── api        (FastAPI + Uvicorn workers, 2–8 tasks, autoscale on CPU + queue depth)
                              ├── worker     (Celery: imports, sends, exports — 2–6 tasks)
                              ├── collector  (Celery: scheduled public-registry collectors — 1–2 tasks)
                              └── beat       (Celery Beat scheduler — exactly 1 task)
                                     │
        RDS PostgreSQL 16 (Multi-AZ, db.m6g.large → db.r6g.xlarge)
              └── read replica (analytics + dashboards)
        ElastiCache Redis (cache.t4g.medium)
        S3 (documents, imports, exports, backups — SSE-KMS, versioning on)
        Secrets Manager (all credentials; nothing in env files)
```

**Why ECS Fargate over EKS:** Kubernetes is a full-time job. Fargate gives you containers, autoscaling and rolling deploys without a control plane to operate. At this scale it is strictly the right trade. Revisit at ~30 services.

**Why not serverless (Lambda) for the API:** cold starts are still real on a
full app image, connection pooling to RDS becomes an RDS Proxy problem, and long-running imports don't fit the execution model. Lambda is right for webhook receivers if you want to isolate them; keep the main API on containers.

**Environments:** `dev` (local Docker Compose), `staging` (single-AZ, scaled-down, 🔴 **synthetic or anonymised data only — never a production PII copy**), `production`.

## 8. Search: stage it

**Now (0–2M records):** PostgreSQL full-text search (`tsvector` + GIN) plus trigram similarity. Handles fuzzy name lookup, cross-entity search, and typo tolerance well enough.

**Later (>2M records or when p95 search exceeds 600ms):** OpenSearch, fed by a Debezium/logical-replication CDC pipeline into a denormalised search index.

Do not start with OpenSearch. It is a second datastore to keep in sync, and sync bugs manifest as "the record exists but search can't find it" — the most confidence-destroying failure mode a CRM has.

## 9. Repository layout

```
agri-crm/
├── api/                     # THE SERVICE — FastAPI + SQLAlchemy 2 + Pydantic v2
│   ├── main.py              # app, middleware, CORS, startup checks
│   ├── config.py            # pydantic-settings, read from the repo-root .env
│   ├── db.py                # async engine + session. Never create_all().
│   ├── security.py          # JWT (HS256) + TOTP + PBKDF2 password verify
│   ├── deps.py              # FastAPI dependencies: current user, role gates
│   ├── routers/             # one module per bounded context — the HTTP surface
│   │   ├── auth.py          # login, refresh, MFA enrol/verify
│   │   ├── geography.py     # ref.* — states, districts, blocks, villages
│   │   ├── organisations.py # organisation + type profiles
│   │   ├── people.py        # core.person, roles, contact points
│   │   ├── farmers.py       # farmer, land, crops, org links
│   │   ├── billing*.py      # draft, write, render, extract, entities
│   │   ├── copilot.py       # the invoice copilot's proposal endpoints
│   │   ├── deliveries.py    # outbox, previews, dispatch
│   │   ├── receivables.py   # ageing, reminders, reconciliation
│   │   ├── dataquality.py   # sources, provenance, contradictions
│   │   └── compliance.py    # exports, DSR handling
│   ├── models/              # SQLAlchemy models MAPPING sql/schema.sql
│   ├── schemas/             # Pydantic request/response contracts
│   ├── domain/              # compliance logic with no HTTP of its own:
│   │                        #   pii, scoping, redaction, checks, delivery,
│   │                        #   payments, proposals, exports, evaluation
│   ├── providers/           # external services; every one has a deterministic fake
│   ├── admin/               # the server-rendered data-operations console
│   ├── collectors/          # one module per approved dq.source
│   ├── templates/           # Jinja2 — invoices, console, emails
│   └── tests/
├── frontend/
│   ├── src/{api,components,features,hooks,lib,pages,types}/
│   └── vite.config.ts
├── mobile/
│   └── src/{db,sync,screens,components}/
├── infra/
│   ├── terraform/{modules,envs/{staging,production}}/
│   └── docker/
├── agri-crm-docs/           # ← these documents, and sql/
└── .github/workflows/
```

**One router per bounded context**, with its models in `api/models/` and its
schemas in `api/schemas/`. Resist the urge to create a `common` or `utils`
module — it becomes a dumping ground and everything imports it circularly.
Shared compliance logic goes in `api/domain/`, which is named for what it
holds and is allowed to have no router at all.

## 10. Engineering standards

| Concern | Standard |
|---|---|
| Python lint/format | `ruff` (lint + format), `mypy --strict` on new modules |
| TS lint/format | `eslint` + `prettier`, `tsc --noEmit` in CI |
| Tests | `pytest` + `pytest-asyncio` + `httpx.AsyncClient` against a real Postgres holding the real DDL; **≥80% coverage on `api/collectors` and the consent/PII paths in `api/domain`** (the two places a bug becomes a legal problem) |
| Schema tests | `sql/smoke_test.sql` runs in CI on every migration |
| API contract | FastAPI emits OpenAPI 3.1 from the Pydantic models; `openapi.yaml` is committed; TS client generated from it; contract drift fails the build |
| Schema changes | reviewed SQL, applied by `make db-migrate`. No migration generator: a schema change is something a person wrote and someone else read. |
| Secrets | AWS Secrets Manager only. `gitleaks` in pre-commit. |
| Branching | trunk-based, short-lived branches, PR + 1 review |
| Deploys | GitHub Actions → ECR → ECS rolling; schema additions run as a separately approved one-off task before the new revision goes live, never in the web start command |
| Observability | Sentry (errors), CloudWatch (logs/metrics), Grafana (dashboards). Structured JSON logs with a `request_id` correlating API → Celery → provider webhook. |

## 11. The build-vs-buy call, stated plainly

Building this custom costs several times what configuring Odoo or Zoho CRM costs.

**Build custom if:** the farmer/FPO/mill relationship graph and the data-quality/provenance layer are the product — i.e. if the database itself is an asset you intend to monetise or that materially differentiates Theta Analytics. Based on what you've described, it is.

**Buy and configure if:** you need something running as fast as possible and the CRM is purely internal sales hygiene, with the data asset built separately.

**The hybrid worth considering — and what it actually looked like:** ship a
server-rendered admin console as the entire v1 UI. It is genuinely usable for
internal data-ops staff. Ship the schema, the ingestion pipelines and the
admin first, prove the data model against real work, then build the polished
React UI once you know which screens people actually use. This is the fastest
route to a system that is *correct*, and correctness is the thing that is
expensive to retrofit.

That is what happened, with one amendment worth recording: the console was
Django Admin for Phase 0 and is now a purpose-built one at `/admin`. The
generic version got the project moving months earlier and was the right
starting point. It stopped being the right answer at the point the system held
financial records, because a console that *can* do anything the ORM can do is
a console you cannot make promises about — and "it cannot issue an invoice,
and here is the test that proves there is no code path" is a promise worth
more than free screens.
