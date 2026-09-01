# 15 · Execution Plan — Phase by Phase

[Doc 13](./13-roadmap-and-phases.md) is the roadmap: what happens when, and why in that order. This document is the execution layer: the task list per phase, the precondition that must be true before a phase starts, and the **exit gate** that must be demonstrably true before it ends.

**The rule that governs this document:** a phase does not end because effort was spent on it. It ends when its exit gate passes. If the registry holds 4,000 FPOs instead of 20,000, Phase 1 is not finished — and starting Phase 2 on top of an unproven ingestion pipeline is how you end up with a system nobody trusts.

🔴 **There are no durations in this plan, deliberately.** An estimate printed next to a task is read as a commitment, and the commitment then decides when the phase ends instead of the gate doing it. Sequence and preconditions are what this document specifies; how long each takes is whatever it takes.

---

## Track P — Parallel workstreams (start immediately, never stop)

🔴 These four are not a phase. They start immediately **regardless of what engineering is doing**, because each waits on somebody outside the team and each blocks a later phase. Every one of them is routinely deferred into its "own" phase, and every one of them then becomes the reason that phase slips.

| # | Workstream | Owner | Waits on | Blocks |
|---|---|---|---|---|
| **P1** | Data protection lawyer | You | External counsel | Phase 2 exit, Phase 4 launch |
| **P2** | Theta legacy data audit | Data ops + you | Internal audit | Phase 2 import |
| **P3** | Meta business verification | You | Meta approval, can stall | Phase 4 entirely |
| **P4** | BD partnership outreach | BD team | Continuous | Phase 2 data volume |

### P1 · Legal engagement

Brief the lawyer with [Doc 05](./05-data-sourcing-and-legal.md). Ask for six deliverables:

1. Privacy notice — English + Hindi, plain language, farmer-comprehensible
2. Consent language per purpose (advisory / market info / project / marketing)
3. Partner MoU data-sharing clause (for FPO and mill agreements)
4. Retention schedule, confirmed against [Doc 12](./12-security-rbac.md) §9
5. Processor agreement template (AWS, Meta, SES, Sentry)
6. Grievance redressal process + named officer

Budget ₹1.5–4 lakh. Against a ₹250 crore penalty ceiling this is the cheapest line item in the project.

### P2 · Theta legacy data audit

For **each batch** — not in aggregate — record: how it was collected, when, by whom, under what consent, for what stated purpose. A spreadsheet is fine. Then classify:

| Class | Meaning | Import as |
|---|---|---|
| **Green** | Documented consent, compatible purpose | `silver`/`gold`, messageable |
| **Amber** | Legitimate collection, unclear or narrower consent | `bronze`, **not messageable**; queue for re-consent |
| **Red** | No documented basis | `quarantine`, or do not import |

🔴 Assume 30–50% lands Amber or Red. Plan the re-consent campaign (through the original partner institution, never cold) as the fallback, not as a surprise.

Only when a batch is classified do you set `dq.source.theta_analytics.is_approved = true` — batch by batch, never wholesale.

### P3 · Meta business verification

Gather: incorporation certificate, GST registration, business address proof, and a live website with a **visible privacy policy** (the last one is the most common rejection reason). Then: Business Manager account → business verification → WABA → dedicated number registration → display name approval.

🔴 Have a BSP (AiSensy, Gupshup, Interakt) identified as contingency. If verification stalls, a BSP is a substantially smaller integration than going direct, and you migrate to direct later.

### P4 · BD partnership outreach

List your first 20 target FPO/mill partnerships and start the conversations immediately. The BD motion and the data acquisition are the same activity. Outreach that starts now is consented farmer data that exists when Phase 2 is ready to import it.

Also immediately: **pick your first two states.** Almost certainly UP and Maharashtra. Depth in two beats breadth in twenty.

---

## Phase 0 — Foundation
**Goal:** an empty but fully deployable system.

**Precondition:** none. Start here.

### Tasks

| Area | Task |
|---|---|
| Repo | Monorepo per [Doc 03](./03-tech-stack.md) §9 · Docker Compose dev environment · pre-commit with `ruff`, `gitleaks` |
| Infra | Terraform for staging: VPC (public/private/isolated subnets), RDS, ECS, S3, Secrets Manager, ElastiCache |
| Database | Postgres 16 + PostGIS + `pg_trgm` + `btree_gist` + `unaccent` · apply `sql/schema.sql` · apply `sql/seed_reference.sql` |
| Testing | 🔴 `sql/smoke_test.sql` wired into CI and **green** |
| Backend | FastAPI project, one router per bounded context (empty) · settings via pydantic-settings from a single `.env` |
| Auth | JWT (15 min access / 7 day rotating refresh) + TOTP MFA scaffolding |
| CI/CD | GitHub Actions → ECR → ECS rolling deploy · migrations as a pre-deploy one-off task |
| Observability | Sentry **with PII scrubbing configured from the first commit**, not retrofitted |

### Exit gate

- [ ] A migration merged to `main` deploys to staging with no human step
- [ ] `smoke_test.sql` runs in CI on every migration and passes all 15 assertions
- [ ] `gitleaks` clean; no secret in the repo or an env file
- [ ] Sentry receives an error from staging with PII scrubbed

---

## Phase 1 — Organisation Registry
**Goal:** 🔴 a working FPO / mill / ACS registry **with real data in it**.

**Precondition:** Phase 0 exit gate passed.

**Why this is first:** institutional data has no consent dependency. You can move fast and prove the entire ingestion pipeline before any legally sensitive data arrives — and it is what your BD team needs *now*.

### Sprint order (order is load-bearing)

| Sprint | Deliverable | Note |
|---|---|---|
| 1 | `ref` geography loaded from LGD | 🔴 **First. Everything joins to this.** ~660k villages. |
| 2 | `core.organisation` + `fpo_profile` / `sugar_mill_profile` / `cooperative_profile`; `/admin` console built out with list views, filters, related detail and bulk actions | Console quality here determines data-ops velocity for six months |
| 3 | `person`, `person_org_role`, `contact_point`; masking on by default; one-primary-contact rule | |
| 4 | Bulk import: column mapping UI, dry run, downloadable error XLSX, commit with legal-basis gate | The loop data ops lives in — make it good |
| 5 | Collectors: `lgd_sync`, `mca_master`, `sfac_fpo` | Base class asserts `source.is_approved` first |
| 6 | Collectors: `isma_directory`, `nfcsf_directory`, `state_sugarfed` (UP + MH) | `state_sugarfed` is the highest-value collector for sugar positioning |

Plus, across the phase: **duplicate detection at creation time** (trigram + district + phone, blocking panel above 0.6 similarity).

### Exit gate

- [ ] 20,000+ FPOs and 500+ mills in `core.organisation`
- [ ] Data-ops team working in the `/admin` console **daily** — not a demo, actual use
- [ ] An import committed and then rolled back successfully within the 7-day window
- [ ] A collector run against an unapproved source **fails loudly and exits non-zero**
- [ ] Duplicate blocking demonstrably prevents a duplicate on the create form

---

## Phase 2 — Farmer Core & Consent
**Goal:** a farmer master with a consent ledger you can defend to a regulator.

**Precondition:** 🔴 **P2 (Theta audit) complete** and **P1 (privacy notice) lawyer-reviewed.** Do not start the import without both. This is the one phase where a missing precondition is not a delay but a liability.

### Tasks

| Area | Task |
|---|---|
| Schema | `core.farmer` + state partitions live · `land_parcel`, `farmer_crop`, `farmer_livestock`, `farmer_org_link` |
| Consent | `comm.consent_event` (append-only) · `consent_current` sync trigger · `comm.suppression` · `comm.v_messageable_farmer` |
| Import | Green batches with full provenance · Amber as `bronze`, **non-messageable** · Red excluded or quarantined |
| Compliance | Privacy notice deployed with `notice_version` recorded · DSR workflow (`audit.dsr_request`) · retention + anonymisation job scheduled |
| API | 🔴 `state` required on `/farmers/` list — reject 400 without it |

### Exit gate

- [ ] Every Theta batch imported at its audited tier, with `dq.source` provenance attached
- [ ] A DSR fulfilled end to end: access request → JSON + PDF package → audited
- [ ] Smoke tests 5, 7 and 8 pass against live data (append-only ledger, opt-out propagation, suppression beats opt-in)
- [ ] 🔴 A query proves nothing sits in `v_messageable_farmer` that shouldn't — Amber and Red batches return zero rows
- [ ] Anonymisation job dry-run produces the expected row count

---

## Phase 3 — Commercial Modules
**Goal:** BD runs its entire pipeline in the system.

**Precondition:** Phase 1 exit gate. (Phase 2 is not a hard dependency — projects and opportunities attach to organisations.)

### Tasks

| Area | Task |
|---|---|
| Projects | `project` + `project_organisation` + `project_contact` + `project_site` + `project_milestone` · default milestone templates per project type |
| Pipeline | `lead` → `opportunity` → `project` conversion · stage-history trigger · ageing automation (21-day stuck rule) · forecast: committed / best case / weighted |
| Agents | `agent`, `agent_territory` (valid_from/valid_to), `agent_target` |
| Security | 🔴 **RLS policies applied and tested** on organisation, farmer, opportunity, lead, field_visit — **including the pooled-connection case** |
| Visits | `field_visit` web entry (mobile deferred to Phase 6) |
| Cross-cutting | `crm.activity` feed written by every module · `crm.task` · notifications |
| **Start early** | 🔴 SES domain warming is slow and cannot be rushed — **begin it in this phase**, not Phase 4 |

### Exit gate

- [ ] BD team's forecast produced from the system; **zero pipeline spreadsheets in use**
- [ ] An opportunity cannot be marked `lost` without a `loss_reason` (DB constraint fires)
- [ ] RLS verified: an agent session cannot read an out-of-territory record, and a pooled connection does not carry one user's context into another's request
- [ ] Organisation detail page shows a complete, ordered activity timeline

---

## Phase 4 — Engagement Engine
**Goal:** consent-governed WhatsApp and email, sending at volume, quality rating Green.

**Precondition:** 🔴 **P3 (Meta verification) complete.** SES domain warmed (started Phase 3).

### Tasks

| Area | Task |
|---|---|
| Templates | Template management · Meta sync of `approval_status` nightly · per-language versions (`hi`, `en`, + operating states) |
| Send path | 🔴 Dispatch-time consent re-check · quiet hours 21:00–08:00 IST · frequency cap max 3/week/recipient · per-second Redis token-bucket throttle |
| Webhooks | Signature verification (`X-Hub-Signature-256`) · **respond 200 within 5s** · write raw to Redis, process async · handle Meta redelivery |
| Opt-out | 🔴 STOP handling in every operating language — **test each one before launch** · 5-second propagation: inbound → consent_event → suppression → cancel queued → confirmation reply |
| Email | SES with SPF/DKIM/DMARC · separate config sets for transactional vs campaign · bounce/complaint → `comm.suppression` |
| Campaigns | Segment builder · 🔴 exclusion breakdown always shown · approval gate above 5,000 recipients or any marketing send · live progress with a working abort |
| Guardrails | Auto-pause at opt-out >1% or failure >5% · alert the moment quality drops from Green |

### Exit gate

- [ ] A 5,000-recipient **utility** campaign sent with **>95% delivery, <0.3% opt-out, quality rating Green**
- [ ] STOP tested and honoured in every launched language, within 5 seconds
- [ ] A hard bounce writes suppression, and a re-import of that address does not resurrect it
- [ ] Preview and dispatch counts differ correctly when a recipient opts out between the two

---

## Phase 5 — Data Intelligence
**Goal:** the quality layer that makes the data organic — and the moat.

**Precondition:** Phases 2 and 4. Delivery receipts from Phase 4 are a verification input here.

### Tasks

| Area | Task |
|---|---|
| Scoring | Completeness score per entity type · quality tier assignment · nightly rescore |
| Decay | Weekly decay job · automatic tier transitions (Gold >180d → Silver, etc.) |
| Entity resolution | Blocking predicates · weighted scoring · 🔴 tune thresholds against **500 hand-labelled pairs** before trusting auto-merge; start auto-merge at 0.96 and lower once precision is measured |
| Queues | Dedupe review · contradiction resolution · quarantine review · verification queue · stale-high-value |
| Coherence | Nightly cross-field rules → `dq.contradiction` (incl. the "20 farmers sharing one phone" rule) |
| 🔴 **Differentiator** | **Theta satellite cross-check for land area** — declared vs. observed → contradiction → agent GPS walk → Gold |
| Dashboards | Source scorecard · Data Health dashboard · decay forecast |

### Exit gate

- [ ] Tier distribution published weekly, trending toward Gold 15–25% / Quarantine <5%
- [ ] 🔴 **Verification throughput exceeds decay rate** — the single number that says the database is improving
- [ ] Satellite cross-check raises real contradictions on real parcels and an agent resolves one end to end
- [ ] Source scorecard identifies at least one underperforming source (it will)

---

## Phase 6 — Field Mobile App
**Goal:** offline-first Android app your agents actually use.

**Precondition:** Phases 2, 3, 5. The day plan needs quality signals to prioritise against.

🔴 **Highest-risk phase.** An app that fails once in the field is abandoned permanently, and an abandoned app means your best data source stops producing.

### Tasks

| Area | Task |
|---|---|
| Shell | Expo app · local SQLite (WatermelonDB or `expo-sqlite`) |
| Sync | Cursor-based pull (`updated_at` + `id`, ≤100KB/page, territory-scoped server-side) · batch push with **per-record accept/reject** · idempotency on `client_uuid` |
| Capture | Visit logging with GPS + device timestamp · 🔴 farmer creation **with consent capture and notice displayed in local language** |
| UX | Day plan (5 things to do, not a database) · targets vs. actuals · photo capture with deferred Wi-Fi upload |
| Safety | Conflict log for last-writer-wins-per-field resolution — never silent |
| Rollout | 🔴 **Pilot with 5 agents**, design *with* two of them, then roll out |

### Exit gate

- [ ] An agent completes a **full day with no connectivity** and loses nothing on sync
- [ ] Three sync retries over flaky 2G produce one visit record, not three
- [ ] A rejected record surfaces to the agent without blocking the rest of the batch
- [ ] Consent captured offline carries `notice_version`, `language`, GPS and agent ID
- [ ] Pilot agents choose to keep using it once the pilot ends

---

## Phase 7 — Scale & Harden
**Goal:** production-grade at 10× current volume.

### Tasks

| Area | Task |
|---|---|
| Partitions | Automation via `pg_partman` or cron — 🔴 a missing partition is an outage; the DEFAULT partitions are a safety net, not a plan |
| Performance | Read replica for analytics · query optimisation pass · **partition-pruning assertions in tests** |
| Search | OpenSearch **only if** search p95 >600ms — not before |
| Security | Penetration test + remediation to closure |
| Resilience | 🔴 DR drill: restore from backup, **measure actual RTO** · incident runbook drill with named people |
| Load | Load test at 3× projected peak |
| Handover | Documentation refresh; these docs updated to match what shipped |

### Exit gate

- [ ] Load test at 3× peak passes within NFR latency budgets
- [ ] DR drill meets the 4-hour RTO with a real restore, not a plan
- [ ] Pen-test findings closed, not merely logged
- [ ] Incident runbook drilled once, with the phone numbers confirmed current

---

## Gate discipline — what slips and what does not

**May slip without consequence:** UI polish, dashboard breadth, extra collectors, additional languages, OpenSearch, the read replica.

🔴 **May never slip, at any phase:**

| Item | Why |
|---|---|
| `smoke_test.sql` green in CI | Trigger regressions are invisible to unit tests |
| Legal basis gate on import commit | The gate *is* the compliance control |
| Consent re-check at dispatch | Between preview and send, people opt out |
| RLS tested including pooled connections | Untested RLS is a false sense of security, which is worse than none |
| Raw landing zone before any transform | In month nine you will need to reprocess |
| No production PII in staging | The most common way personal data leaks |
| Backup restore actually tested | An untested backup is a hope |

**The two risks Doc 13 rates high-likelihood are the two that will actually happen:** legacy data quality worse than expected, and scope creep. Budget for the first by assuming 30–40% of legacy records are unusable. Defend against the second by treating the out-of-scope list in [Doc 01](./01-product-requirements.md) §1 as a contract, revisited only at phase boundaries.
