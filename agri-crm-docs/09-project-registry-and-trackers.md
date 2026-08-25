# 09 · Project Registry, BD Tracker & Agent Tracker

Three modules that share a spine: the **activity feed**. Everything that happens — a call, a WhatsApp, a visit, a stage change, a document shared — lands in `crm.activity` against a subject, and every screen in this document is a different lens on that feed.

---

## Part A — Project Registry

### A1. What a project is

A discrete piece of work with a counterparty, a value, a timeline and an outcome. A biogas plant at a mill. A cane-yield analytics pilot across three FPOs. A carbon MRV programme. A mechanisation rollout. Training delivery under a scheme.

Distinct from an **opportunity**, which is a *potential* project in the sales pipeline. An opportunity that reaches `won` spawns a project. The link is `crm.project.opportunity_id` ↔ `crm.opportunity.project_id`.

### A2. Record structure

| Block | Fields |
|---|---|
| **Identity** | `project_code` (PRJ-2026-0042), `name`, `type`, `description` |
| **Type** | biogas_cbg · cane_yield_analytics · carbon_mrv · farm_mechanisation · irrigation · input_supply · output_procurement · training_capacity · credit_linkage · digital_advisory · other |
| **Stage** | identified → feasibility → proposal → approved → contracting → implementation → operational → completed (or on_hold / cancelled) |
| **Commercial** | `value_inr`, `currency`, `funding_source` |
| **Timeline** | `start_date`, `expected_end_date`, `actual_end_date` |
| **Impact** | `farmers_impacted`, `area_covered_ha` |
| **Ownership** | `manager_user_id`, `health` (green/amber/red) + `health_note` |
| **Origin** | `opportunity_id` |

### A3. Multi-party structure

A project rarely has one counterparty. Three link tables:

**`crm.project_organisation`** — many organisations, each with a role: `client` · `implementation_partner` · `funder` · `aggregator` · `vendor` · `technical_partner`. A CBG project might have a mill as client, an FPO as aggregator, NABARD as funder and an EPC firm as vendor. Flatten that into one field and you lose the ability to ask "which funders do we work with most."

**`crm.project_contact`** — people attached with a role *on this project*: `sponsor` · `day_to_day` · `technical` · `finance` · `site`. The same person can be day-to-day on one project and sponsor on another.

**`crm.project_site`** — a project spans villages and/or organisations, each with its own status and coordinates. A mechanisation rollout across 40 villages is 40 site rows, and the map view of project progress falls straight out of it.

### A4. Milestones

`crm.project_milestone` — name, due date, completed date, owner, status, sort order. Auto-generate a default milestone set per project type from a template, then let the manager edit. Overdue milestones raise a `crm.task` for the owner.

### A5. Project screens

**List:** filter by type, stage, health, manager, state, value range, date range. Group by stage (kanban) or list. Columns: code, name, type, stage, value, client, manager, health, next milestone.

**Detail tabs:** Overview · Parties · Sites (with map) · Milestones · Activity · Documents · Financials

**Portfolio dashboard:**
- Value by stage (funnel)
- Health distribution with a drill-down to red projects
- Projects by type and state
- Farmers impacted and area covered, cumulative
- Milestone slippage: projects with overdue milestones, ranked by value
- Delivery load per manager

---

## Part B — Business Development Tracker

### B1. Lead → Opportunity → Project

```
LEAD                    OPPORTUNITY                   PROJECT
(unqualified)           (qualified, in pipeline)      (won, being delivered)
     │                        │                            │
  source: referral,      stage: new → contacted →     stage: identified →
  campaign, field,       qualified → proposal_sent    ... → operational
  inbound, event, list   → negotiation → won/lost
     │                        │                            │
  crm.lead ──convert──►  crm.opportunity ──won──────► crm.project
```

### B2. Lead capture

`crm.lead` deliberately accepts **raw** values (`org_name_raw`, `contact_name_raw`, `contact_phone`, `contact_email`) alongside optional FK links. A lead often arrives before you know whether the organisation exists in the registry. Forcing organisation creation at capture time is how leads stop getting logged.

Sources: manual entry · bulk import · web form · campaign reply · inbound WhatsApp · event/exhibition · referral

On conversion: resolve or create the organisation and person, create the opportunity, link everything, set `lead.status='converted'`.

### B3. Pipeline stages and probabilities

| Stage | Default probability | Definition — what must be *true* to be here |
|---|---|---|
| `new` | 5% | Logged, no contact attempted |
| `contacted` | 10% | Reached a human, interest unknown |
| `qualified` | 25% | Confirmed need, budget indication, decision-maker identified |
| `proposal_sent` | 45% | Written proposal with pricing delivered |
| `negotiation` | 70% | Commercial terms under discussion |
| `won` | 100% | Signed |
| `lost` | 0% | 🔴 `loss_reason` required (enforced by DB check constraint) |
| `dormant` | 0% | No response for 90+ days; revivable |

**Write the definitions down and enforce them in the UI.** A pipeline where "qualified" means whatever each agent feels produces a forecast that is worse than no forecast at all. Show the definition as helper text on the stage dropdown.

Probabilities are defaults; the owner can override per opportunity. `weighted_value_inr` is a generated column — always accurate, never stale.

### B4. Stage ageing and automation

`crm.opportunity_stage_history` is written by trigger on every stage change, with `days_in_from_stage`.

| Rule | Threshold | Action |
|---|---|---|
| Stuck in stage | 21 days (configurable per stage) | Auto-task to owner: "Advance or mark dormant" |
| No activity | 14 days | Auto-task: "No contact in 2 weeks" |
| Past expected close | any | Flag on dashboard, task to manager |
| High value, low activity | >₹25L and <2 activities in 30 days | Alert to manager |
| Won | — | Auto-create project, copy parties and contacts |
| Lost | — | Require reason; if `unresponsive`, offer to move the contact to a nurture campaign |

### B5. Loss reason taxonomy

Fixed list, not free text: `price` · `competitor` · `no_budget` · `no_decision` · `timing` · `unresponsive` · `not_a_fit` · `lost_to_inaction` · `internal_deprioritised`

Plus `competitor` name where applicable. A quarterly loss-reason breakdown is the highest-signal report a BD function produces, and free-text reasons make it impossible.

### B6. Forecasting

**Views:** this month · this quarter · next quarter · rolling 12 months

**Three numbers, always shown together:**
- **Committed** — `negotiation` stage, close date in period
- **Best case** — `proposal_sent` + `negotiation`
- **Weighted** — `sum(weighted_value_inr)` across all open stages

Plus: won-so-far vs. target, and pipeline coverage ratio (open weighted pipeline ÷ remaining target — below 3× is a warning).

### B7. BD dashboard

- Funnel by stage: count and value
- Weighted forecast vs. target, by month
- Win rate: overall, by type, by state, by agent
- Average sales cycle by project type
- Stage-conversion rates — where deals actually die
- Loss reasons, last 4 quarters
- Ageing report: opportunities by days in current stage
- Leaderboard: pipeline created, pipeline advanced, value won

---

## Part C — Agent Tracker

### C1. Purpose

Know what the field and BD team is doing, where, and whether it is working — without turning the system into surveillance. The distinction matters practically as well as ethically: agents who feel tracked stop logging accurately, and inaccurate activity data is worse than none.

🔴 **Design constraint:** GPS is captured **at the moment a visit is logged**, and only then. No continuous background location. It is disproportionate, it creates DPDP exposure for employee data, and it destroys the trust that makes agents log honestly.

### C2. Agent record

`crm.agent` — linked to a user, with `employee_code`, `full_name`, `phone`, `email`, `reports_to_id` (hierarchy), `designation`, `date_joined`, `base_district_id`, `is_active`.

`crm.agent_territory` — state / district / block scope with `valid_from` / `valid_to`. Territory changes are historical, never overwritten — you need to know who owned a district last March.

🔴 Territory drives **row-level security**: an agent sees the organisations and farmers in their territory, full stop. Enforced in Postgres RLS, not just in the API ([Doc 12](./12-security-rbac.md)).

### C3. Visit logging

`crm.field_visit`:

| Field | Notes |
|---|---|
| `client_uuid` | 🔴 Device-generated, unique — the idempotency key that makes offline sync safe |
| `agent_id` | |
| `organisation_id` / `farmer_id`+`farmer_state_id` / `person_id` | Who was visited |
| `visit_purpose` | introduction · follow_up · proposal · data_collection · verification · training · issue_resolution · collection |
| `outcome` | interested · not_interested · needs_followup · meeting_scheduled · data_collected · not_available |
| `notes`, `next_action`, `next_action_due` | `next_action_due` auto-creates a `crm.task` |
| `latitude`, `longitude`, `gps_accuracy_m` | Captured at log time |
| `visited_at`, `device_recorded_at`, `synced_at` | Three timestamps: when it happened, when the device recorded it, when it reached the server. The gap between the last two is your offline-usage metric. |
| `photo_urls[]` | Uploaded separately, Wi-Fi by default |

**Visit validation:** if GPS is more than 5 km from the organisation's recorded location, flag for review — usually the *organisation's* coordinates are wrong, and this is a free data-quality signal. Do not treat it as an accusation.

### C4. Targets

`crm.agent_target` — per agent, per period, per metric:

| Metric | Typical monthly target |
|---|---|
| `visits` | 60–80 |
| `new_orgs` | 8–15 |
| `consented_farmers` | 400–1,200 |
| `verifications` | 150–300 |
| `pipeline_value` | ₹15–50L created |
| `partnerships_signed` | 2–6 |

`achieved_value` is recomputed nightly from actuals. Targets are set by the manager, per period, and history is preserved.

### C5. Day plan

The mobile home screen answers one question: **what do I do today?** Generated server-side from:

1. Tasks due today (highest priority first)
2. `next_action_due` from previous visits
3. Opportunities stuck in stage in this territory
4. Gold contacts about to decay on accounts with open opportunities
5. High-value unverified records nearby
6. Scheduled meetings

Ordered by a simple score (priority × value × proximity), with a map view. An agent should open the app and see five things to do, not a database.

### C6. Agent dashboard

**For the agent:** today's plan · this month target vs. actual per metric · my pipeline · my accounts by last-contact age · my data-quality contribution (records verified, consents captured)

**For the manager:** team target vs. actual · visits per agent per week · new orgs and consented farmers per agent · pipeline created and advanced per agent · **coverage map** (which districts have had no visit in 30 days) · activity-to-outcome ratio (visits per qualified opportunity)

That last metric is the useful one. An agent logging 90 visits a month and creating two opportunities has a targeting problem, not an effort problem — and the number tells you to help rather than to push.

### C7. What to measure, and what not to

**Measure:** outcomes (opportunities created, partnerships signed, farmers consented, records verified) and the leading activity that produces them (visits, calls, follow-ups completed on time).

**Do not measure:** hours logged in, location during the day, message response times, or anything that rewards presence over result. It produces gaming, and in an offline-first field app, gaming is undetectable.

---

## Part D — Shared activity feed

`crm.activity` is polymorphic (`subject_type` + `subject_id`), partitioned monthly, and written by every module:

| Written when | Type | Subject |
|---|---|---|
| Agent logs a visit | `field_visit` | organisation / farmer |
| Call logged | `call` | organisation / person |
| WhatsApp sent or received | `whatsapp` | farmer / person |
| Email sent or received | `email` | person |
| Opportunity stage change | `system_event` | opportunity |
| Document shared | `document_shared` | project / opportunity |
| Note added | `note` | anything |
| Import touched a record | `system_event` | any entity |

**This makes the "last activity" field real everywhere.** An organisation page shows the complete history — every touch, by anyone, in order. Which is the actual reason people trust a CRM: when an agent leaves, the relationship stays in the system.

**Auto-logging beats manual logging.** WhatsApp and email activities are written automatically from the messaging pipeline. Visits are logged in the field app in under 30 seconds. The only thing an agent should have to type by hand is a note.
