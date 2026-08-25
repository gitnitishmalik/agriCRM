# 13 · Roadmap & Build Phases

## 1. Guiding sequence

**Correctness before polish.** The expensive mistakes in this system are structural — a data model that can't express the farmer↔FPO↔mill graph, a consent design that can't prove itself, a partitioning decision deferred past 5M rows. UI polish is cheap to add later; those three are not.

**Django Admin is your Phase 1 UI.** It is genuinely usable for internal data-ops work. Shipping schema + ingestion + admin in ten weeks means real data enters the system in month three, and the React UI in Phase 2 gets designed around screens people have actually used rather than screens you imagined.

**Every phase ends with something in production.** No six-month integration phase.

---

## 2. Phase plan (52 weeks)

### Phase 0 — Foundation · Weeks 1–3

| | |
|---|---|
| **Deliverable** | Empty but deployable system |
| Tasks | Repo, monorepo layout, Docker Compose dev env · Terraform for staging · Postgres 16 + extensions · **`sql/schema.sql` applied and `smoke_test.sql` green in CI** · Django project + apps skeleton · auth with JWT + MFA · CI/CD to staging · Sentry |
| **Exit criteria** | A migration merged to main deploys to staging automatically; smoke test runs in CI |

### Phase 1 — Organisation Registry · Weeks 4–9

| | |
|---|---|
| **Deliverable** | 🔴 **A working FPO/mill/ACS registry with real data in it** |
| Tasks | `ref` geography loaded from LGD (do this first) · organisation + type profiles · people, roles, contact points with masking · Django Admin configured properly (list displays, filters, inlines, bulk actions) · bulk import: mapping, dry-run, error file, commit with legal-basis gate · collectors: `lgd_sync`, `mca_master`, `sfac_fpo`, `isma_directory`, `nfcsf_directory` · duplicate detection at create |
| **Exit criteria** | 20,000+ FPOs and 500+ mills loaded; data-ops team using Admin daily; imports reversible |
| **Why first** | Institutional data has no consent dependency, so you can move fast and prove the pipeline before the legally sensitive data arrives. It is also what your BD team needs *now*. |

### Phase 2 — Farmer Core & Consent · Weeks 10–15

| | |
|---|---|
| **Deliverable** | Farmer master with a defensible consent ledger |
| Tasks | Farmer table + partitions · land parcels, crops, livestock, org links · **consent ledger + suppression + messageable view** · 🔴 **Theta legacy data audit and classification (Green/Amber/Red)** · Green batches imported with provenance · privacy notice drafted and lawyer-reviewed · DSR workflow · retention/anonymisation jobs |
| **Exit criteria** | Theta data classified and imported at the correct tier; a DSR can be fulfilled end to end; nothing messageable that shouldn't be |
| **Risk** | The legacy audit may take longer than planned and may disqualify data you were counting on. **Start it in week 1 of Phase 0, in parallel** — do not wait for Phase 2. |

### Phase 3 — Commercial Modules · Weeks 16–22

| | |
|---|---|
| **Deliverable** | Project Registry, BD Tracker, Agent Tracker |
| Tasks | Projects + parties + contacts + sites + milestones · leads → opportunities → projects · stage history triggers, ageing automation, forecast · agents, territories, targets · **RLS policies applied and tested** · visits (web entry first, mobile in Phase 6) · activity feed across all modules · tasks and notifications |
| **Exit criteria** | BD team running their entire pipeline in the system; forecast produced from it, not from a spreadsheet |

### Phase 4 — Engagement Engine · Weeks 23–29

| | |
|---|---|
| **Deliverable** | Consent-governed WhatsApp + email |
| Tasks | Meta WABA setup, business verification, number registration · template management + Meta sync · send pipeline with dispatch-time consent re-check, quiet hours, frequency cap, throttling · webhook receiver with signature verification · **STOP handling across languages** · SES with SPF/DKIM/DMARC, domain warming, bounce/complaint → suppression · segment builder with exclusion breakdown · campaign approval and launch · auto-pause guardrails |
| **Exit criteria** | A 5,000-recipient utility campaign sent with >95% delivery, <0.3% opt-out, quality rating Green |
| **Risk** | Meta business verification takes 1–3 weeks and can stall on documentation. 🔴 **Start it in Phase 1**, not Phase 4. |

### Phase 5 — Data Intelligence · Weeks 30–36

| | |
|---|---|
| **Deliverable** | The quality layer that makes the data organic |
| Tasks | Quality tiers + completeness scoring · decay jobs · entity resolution (blocking, scoring, thresholds) tuned against a labelled set · dedupe review queue + merge UI · coherence rules → contradiction queue · **Theta satellite cross-check for land area** ← the differentiator · source scorecard · Data Health dashboard · verification workflows |
| **Exit criteria** | Tier distribution published weekly; verification throughput exceeds decay rate |

### Phase 6 — Field Mobile App · Weeks 37–44

| | |
|---|---|
| **Deliverable** | Offline-first Android app for agents |
| Tasks | React Native/Expo shell · local SQLite + sync engine · offline visit logging with GPS + `client_uuid` · offline farmer creation **with consent capture and notice display in local language** · day plan · targets vs. actuals · photo capture and deferred upload · conflict logging · pilot with 5 agents, then rollout |
| **Exit criteria** | An agent completes a full day with no connectivity and loses nothing on sync |
| **Risk** | Highest-risk phase. Pilot with real agents in real villages before rolling out — an app that fails once in the field is abandoned permanently. |

### Phase 7 — Scale & Harden · Weeks 45–52

| | |
|---|---|
| **Deliverable** | Production-grade at 10× current volume |
| Tasks | Partition maintenance automation (pg_partman or cron) · read replica for analytics · query optimisation pass, partition-pruning assertions · OpenSearch if search p95 >600ms · penetration test + remediation · DR drill (restore from backup, measure RTO) · **incident runbook drill** · full audit-log review · load test at 3× projected peak · documentation and handover |
| **Exit criteria** | Load test passes; DR drill meets RTO; pen-test findings closed |

---

## 3. Timeline at a glance

```
Week   1    5   10   15   20   25   30   35   40   45   50
       │    │    │    │    │    │    │    │    │    │    │
P0 ████
P1     ████████████
P2                 ████████████
P3                             ██████████████
P4                                           ██████████████
P5                                                        ████████████
P6                                                                    ████████████
P7                                                                                ██████████

Parallel from week 1:
  Legal review ─────────────────────
  Theta data audit ─────────────
  Meta business verification ────────────
  BD partnership outreach (fills the database) ──────────────────────────────────────
```

🔴 **Three things must start in week 1 regardless of phase:** the lawyer engagement, the Theta legacy data audit, and Meta business verification. All three have external dependencies with multi-week lead times, and all three block later phases if left until their "own" phase.

---

## 4. Team

### Minimum viable (you + 2)

| Role | Focus |
|---|---|
| You / tech lead | Architecture, schema, code review, the hard problems |
| Full-stack engineer | Django + React feature work |
| Data ops analyst (from month 3) | Imports, quality, verification — **this hire pays for itself faster than a second engineer** |

Timeline stretches to ~15 months.

### Comfortable (5)

| Role | Focus |
|---|---|
| Tech lead | Architecture, review, integrations |
| Backend engineer | Django, pipelines, collectors |
| Frontend engineer | React, dashboards, grids |
| Mobile engineer (from month 8) | React Native, sync |
| Data ops analyst | Quality, imports, verification |

52 weeks as planned. Add a part-time DevOps engineer for Phase 0 and Phase 7.

### External

- 🔴 **Data protection lawyer** — engaged week 1. Privacy notice, consent language, MoU template, retention schedule, processor agreements. Not optional.
- **Penetration tester** — Phase 7, one engagement
- **Designer** — 4–6 weeks across Phases 3 and 6

---

## 5. Milestones and success criteria

| Milestone | Week | Success looks like |
|---|---|---|
| M1 · Registry live | 9 | 20,000+ FPOs, 500+ mills; data ops working in it daily |
| M2 · Farmer core live | 15 | Theta data classified and imported at correct tiers; DSR fulfilled end to end |
| M3 · BD running in CRM | 22 | Forecast produced from the system; zero pipeline spreadsheets in use |
| M4 · First campaign | 29 | 5,000 recipients, >95% delivery, <0.3% opt-out, Green quality |
| M5 · Quality layer | 36 | Tier distribution published weekly; verification rate > decay rate |
| M6 · Field app | 44 | 20 agents using it; >90% of visits logged in-app; zero data loss reports |
| M7 · Production hardened | 52 | Load test 3× peak passes; DR drill meets RTO; pen-test closed |

---

## 6. Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| 🔴 Theta legacy data has no documented lawful basis | High | **Medium** | Audit in week 1. Plan a re-consent campaign through partner institutions as the fallback. Assume 30–50% may be unusable for outbound. |
| 🔴 Meta business verification delayed or rejected | High | Medium | Start week 1. Have a BSP as a contingency (2-week integration instead of 6). |
| Partnership acquisition slower than modelled | High | Medium | Model at 50% of target. Field collection and mill partnerships as parallel channels. |
| Field app rejected by agents | Medium | Medium | Pilot with 5 agents for 3 weeks. Design *with* two agents, not for them. |
| Data quality worse than expected in legacy data | Medium | **High** | Assume 30–40% of legacy records are unusable. The quality layer exists to find them. |
| Scope creep (payments, trading, farmer app) | High | **High** | The out-of-scope list in [Doc 01](./01-product-requirements.md) §1 is a contract. Revisit only at phase boundaries. |
| Key-person dependency | High | Medium | Document as you build. These 15 documents are the start, not the end. |
| Public source layout change breaks a collector | Low | High | Raw landing zone + row-count alerting. Expect to fix a collector monthly. |
| DPDP enforcement action | High | Low if compliant | [Doc 05](./05-data-sourcing-and-legal.md) and [Doc 12](./12-security-rbac.md), followed properly |

The two marked **High likelihood** — legacy data quality and scope creep — are the ones that actually happen. Plan for both.

---

## 7. What to do in the first two weeks

Concrete, in order:

1. **Engage a data protection lawyer.** Brief them with [Doc 05](./05-data-sourcing-and-legal.md). Ask for: privacy notice, consent language in English + Hindi, partner MoU data-sharing clause, retention schedule, processor agreement template.
2. **Start the Theta legacy audit.** For each batch: how was it collected, when, by whom, under what consent, for what purpose. A spreadsheet is fine. This determines what you actually have.
3. **Start Meta business verification.** Gather incorporation certificate, GST registration, business address proof, and a website with a visible privacy policy.
4. **Stand up the repo and Postgres.** Apply `sql/schema.sql` and `sql/seed_reference.sql`, run `sql/smoke_test.sql`, get it green in CI.
5. **Download the LGD data** and load `ref.state` / `district` / `block` / `village`. Everything joins to this.
6. **Pick your first two states.** Almost certainly UP and Maharashtra given the sugar focus. Depth in two states beats breadth in twenty — the whole system is more valuable where the coverage is complete.
7. **List your first 20 target FPO/mill partnerships** and start the conversations. The BD motion and the data acquisition are the same activity; starting it now means Phase 2 has real partner data to import.

Item 7 is the one that gets deferred and shouldn't be. Every week of partnership outreach that starts now is a week of consented farmer data that exists when the system is ready for it.
