# 02 · Data Model

The runnable DDL is [`sql/schema.sql`](./sql/schema.sql). It has been executed against **PostgreSQL 16.13** and passes a 15-assertion behavioural test suite ([`sql/smoke_test.sql`](./sql/smoke_test.sql)) covering partition routing, derived columns, generated columns, consent append-only enforcement, opt-out propagation, suppression precedence and stage-history triggers. Do not hand-edit the SQL without re-running the tests.

## 1. Design principles

**One organisation table, typed extensions.** An FPO, a sugar mill and a PACS share 80% of their fields (name, address, registration, contacts, owner, quality). Modelling them as three tables triples every join, every search, every permission rule. Instead: `core.organisation` with an `org_type` discriminator, plus `fpo_profile`, `sugar_mill_profile`, `cooperative_profile` extension tables holding only what is genuinely type-specific.

**People are not contacts-of-a-company.** In this sector a person is an MD of an FPO, a director of a mill's cooperative, and a farmer, sometimes simultaneously, and moves between roles. `core.person` + `core.person_org_role` (with `valid_from`/`valid_to`) models that. When a board changes, you close the old role row — you never overwrite it, so you keep the ability to say "we knew this person when he was at X."

**Contact points have their own lifecycle.** A phone number is not a column on a person. It is a row with a verification state, a delivery-failure counter, a WhatsApp-capability flag and a source. Rural phone churn is 15–20%/year; treating a number as an attribute of identity guarantees data rot.

**Consent is an append-only ledger.** `comm.consent_event` can only be inserted into — a trigger raises an exception on UPDATE or DELETE. Current state lives in `comm.consent_current`, maintained by trigger. When a regulator or a client asks "prove this farmer agreed on 12 March", you have the row, the evidence type, the notice version and the language it was shown in.

**Provenance is a first-class table, not a comment.** `dq.field_provenance` records where each tracked field's value came from, when, from which source, and with what confidence. `dq.source` holds the compliance whitelist — every source carries a mandatory written `legal_basis`, and a collector must refuse to run against a source that is not `is_approved`.

**Nothing is hard-deleted.** `is_deleted` soft flags, `merged_into_id` pointers, `dq.merge_event` with a full JSONB snapshot so a merge can be reversed. In a system fed by imports and scrapers, an irreversible bulk operation is a matter of time.

**Partition before you need to.** `core.farmer` is `PARTITION BY LIST (state_id)` — 36 partitions, one per state/UT. `comm.message`, `crm.activity`, `audit.change_log` and `audit.data_access_log` are `PARTITION BY RANGE` on their timestamp, monthly. Retrofitting partitioning onto a live 10M-row table is a weekend of downtime; doing it on day one costs nothing.

## 2. Schema map

| Schema | Contains |
|---|---|
| `ref` | Geography (state/district/block/village, LGD-coded), crops, varieties |
| `core` | Organisations, people, roles, contact points, farmers, land, crops, documents |
| `comm` | Consent ledger, suppression, templates, campaigns, messages, inbound |
| `crm` | Projects, leads, opportunities, agents, territories, visits, activities, tasks |
| `dq` | Sources, field provenance, contradictions, imports, merges, dedupe candidates |
| `audit` | Change log, data-access log, DSR requests |

## 3. Entity relationship — the core graph

```
                    ref.state ── ref.district ── ref.block ── ref.village
                         │            │                          │
        ┌────────────────┴────────────┴──────────────────────────┴──────────┐
        │                                                                    │
core.organisation ◄──── core.person_org_role ────► core.person              │
   │  │  │                                              │                    │
   │  │  └── core.fpo_profile                           ├── core.contact_point
   │  │  └── core.sugar_mill_profile                    │        │
   │  │  └── core.cooperative_profile                   │        │
   │  │  └── core.org_annual_metric                     │   comm.consent_event
   │  │                                                 │        │ (append-only)
   │  └── core.mill_command_village ──► ref.village     │        ▼
   │                                                    │   comm.consent_current
   │        ┌───────────────────────────────────────────┘        │
   │        │                                                    ▼
   └──► core.farmer_org_link ──► core.farmer ◄──────────  comm.v_messageable_farmer
                                    │  │  │                      │
                                    │  │  ├── core.land_parcel   │  filtered by
                                    │  │  ├── core.farmer_crop   │  comm.suppression
                                    │  │  └── core.farmer_livestock
                                    │  │
                                    │  └── dq.field_provenance ──► dq.source
                                    │
crm.lead ──► crm.opportunity ──► crm.project ──► crm.project_site
                   │                   │  │  └──► crm.project_milestone
                   │                   │  ├──► crm.project_organisation
                   │                   │  └──► crm.project_contact
                   ▼                   │
      crm.opportunity_stage_history    │
                                       ▼
crm.agent ── crm.agent_territory   crm.activity  (polymorphic, partitioned monthly)
        └──── crm.field_visit ─────────┘
        └──── crm.agent_target
```

## 4. Table reference

### 4.1 `ref` — geography and crops

| Table | Key columns | Notes |
|---|---|---|
| `ref.state` | `id` (= LGD state code), `name`, `is_ut` | Seeded with all 36 states/UTs. `id` doubles as the farmer partition key. |
| `ref.district` | `lgd_code`, `state_id`, `name` | ~780 rows. Trigram-indexed for fuzzy matching. |
| `ref.block` | `lgd_code`, `district_id`, `name` | ~7,000 rows |
| `ref.village` | `lgd_code`, `block_id`, `district_id`, `name`, `pincode`, lat/long | ~660,000 rows. The finest granularity you need. Load from the Local Government Directory. |
| `ref.crop`, `ref.crop_variety` | `code`, `name`, `category`, `default_season` | Seeded with 19 major crops |

**Why LGD codes matter:** every government dataset (AGMARKNET, land records, PM-KISAN, scheme lists) keys on LGD codes. Adopting them at the reference layer means every future dataset joins without a name-matching exercise. Name-matching Indian village names across sources is a genuinely miserable problem — avoid it structurally.

### 4.2 `core.organisation`

The central table. Selected columns:

| Column | Type | Purpose |
|---|---|---|
| `org_code` | text unique | Human-readable ID: `FPO-UP-000123`, `MILL-MH-000045`. Used in conversation and on exports. |
| `type` | `org_type` enum | fpo / acs / sugar_mill / cooperative_federation / input_dealer / ngo_promoting_institution / government_body / private_company / bank_nbfc / other |
| `status` | `org_status` enum | prospect / active / dormant / defunct / merged / blacklisted |
| `legal_form` | `legal_form` enum | producer_company / cooperative_society / section_8_company / … |
| `name`, `name_local`, `aliases[]` | text | `aliases` is GIN-indexed — FPOs are known by 3–4 names in practice |
| `cin` | varchar(21) | MCA Corporate Identity Number. Partial-unique index. Your join key to MCA master data. |
| `pan_masked` | varchar(14) | 🔴 Masked only (`ABCDE****F`). Never store a full PAN. |
| `parent_org_id` | uuid FK self | Sugar groups own multiple mills; federations own societies |
| `member_count`, `women_member_count` | integer | Check constraint: women ≤ total |
| `quality_tier`, `completeness_score`, `primary_source_id` | | The data-intelligence layer, on every record |
| `owner_user_id` | uuid | Account owner for BD. Drives row-level security. |
| `extra` | jsonb (GIN) | Escape hatch for state-specific or scheme-specific fields, so you don't migrate the schema every time a new dataset arrives |
| `merged_into_id`, `is_deleted` | | Soft-delete and merge chain |

**Extension: `core.fpo_profile`** — paid-up capital, shareholder count, `business_lines[]`, `licences[]` (seed/fertiliser/pesticide/FSSAI/mandi — these determine what an FPO can legally trade and are a strong qualification signal), storage capacity, custom hiring centre, equity grant received, CBBO name, implementing agency (SFAC/NABARD/NCDC/NAFED), last AGM date, `primary_crops[]`.

**Extension: `core.sugar_mill_profile`** — `crushing_capacity_tcd` (tonnes cane per day — the single most important sizing metric), ownership (private/cooperative/public/joint), cogeneration MW, distillery KLPD, `has_ethanol_plant`, `has_cbg_plant`, average recovery %, registered cane growers, season start/end month, `federation_membership[]` (ISMA / NFCSF / state sugarfed), cane payment status and arrears. Arrears are a live commercial signal — a mill in arrears is a different sales conversation.

**Extension: `core.cooperative_profile`** — society type (PACS/cane society/dairy/marketing/credit), registration act, affiliation to a parent federation, `is_pacs`, `is_computerised` (the PACS computerisation programme is a real segmentation axis), deposit base, area of operation, villages covered.

**`core.org_annual_metric`** — an EAV-style yearly metrics table keyed `(organisation_id, fy, metric_code)`. Holds cane crushed, sugar produced, recovery, turnover, ethanol produced, by financial year, with a source. This is how you avoid adding a column every time a new annual statistic appears, and it makes year-on-year trend queries trivial.

### 4.3 `core.person` and `core.person_org_role`

`person.full_name` is a **stored generated column** concatenating first/middle/last — trigram-indexed, so fuzzy search never has to compute the concatenation.

`father_or_spouse` is not optional in practice. In rural India, name + village is frequently not unique; name + father's name + village usually is.

`din` (Director Identification Number) is unique-indexed where present — it is your reliable join key to MCA director data and the only genuinely unique identifier available for FPO board members.

`person_org_role` carries `valid_from`/`valid_to`. A partial unique index enforces **one primary contact per organisation at a time**:

```sql
CREATE UNIQUE INDEX uq_por_primary ON core.person_org_role(organisation_id)
  WHERE is_primary_contact AND valid_to IS NULL;
```

Roles available: managing_director, chief_executive, chairman, vice_chairman, director, secretary, treasurer, board_member, member_farmer, shareholder, cane_manager, procurement_head, general_manager, unit_head, accountant, field_officer, promoter, nodal_officer, other.

### 4.4 `core.contact_point`

One row per phone/email, owned by **exactly one** of a person or an organisation (enforced by check constraint). Carries:

- `value_raw` (as entered) and `value_normalised` (E.164 for phones, lowercased for email) — always query the normalised column
- `verification` state: unverified / pending / verified / failed / invalid / **do_not_contact**
- `delivery_failures` counter — the messageable view excludes anything at ≥3
- `is_whatsapp_capable` — populated from Meta's contacts check
- `source_id` — where this number came from

### 4.5 `core.farmer` — partitioned

`PARTITION BY LIST (state_id)`. Composite primary key `(id, state_id)` — Postgres requires the partition key in any unique constraint. Every child table therefore carries `(farmer_id, farmer_state_id)` as its foreign key. This is slightly verbose and entirely worth it: a query filtered to Uttar Pradesh touches one partition, not ten million rows.

Notable columns:

| Column | Notes |
|---|---|
| `total_area_ha` + `area_source` | 🔴 Area is always hectares. `area_source` records self_declared / document / satellite — self-declared land area in India is routinely inflated by 20–40% and you must know which you have. |
| `farmer_class` | **Derived by trigger** from `total_area_ha` using GoI size classes (marginal <1ha, small 1–2, semi-medium 2–4, medium 4–10, large >10). Never entered by hand; test 1 in the smoke suite proves it. |
| `aadhaar_hash` + `aadhaar_last4` | 🔴 SHA-256 with a per-record salt, plus last 4 digits for human confirmation. **Never store plaintext Aadhaar.** Used only for deduplication, never displayed. Consider not collecting it at all in v1 — see [Doc 12](./12-security-rbac.md) §5. |
| `agristack_farmer_id` | Store only if the farmer volunteers it |
| `primary_fpo_id`, `supplying_mill_id`, `mill_supplier_code` | Denormalised fast pointers; the full relationship set lives in `farmer_org_link` |
| `theta_external_id` | Your join key back to Theta Analytics' satellite/yield data |
| `consent_summary` jsonb | Cached read-model; `comm.consent_current` remains authoritative |

Children: `land_parcel` (per-plot area, tenure, irrigation, khasra number, optional PostGIS boundary, `area_verified` flag), `farmer_crop` (unique per farmer × crop × season × year, with expected and actual yield and who it was sold to), `farmer_livestock` (matters for biogas/CBG projects), `farmer_org_link` (many-to-many with a `relationship` label: fpo_member / mill_supplier / society_member / borrower).

### 4.6 `core.mill_command_village`

A sugar mill's cane command area, expressed as village rows with distance, registered growers and cane area for a given season. This is the table that lets you answer *"which mills compete for cane in this block"* and *"how many of our consented farmers sit inside Mill X's command area"* — the highest-value question in the whole system for a sugar-sector sale.

### 4.7 `comm` — consent and messaging

`comm.consent_event` (append-only) → trigger → `comm.consent_current` (one row per subject × channel × purpose).

Each event records: channel, purpose (transactional / service_update / advisory / marketing / survey / project_specific), status, **evidence_type** (signed_form / in_app_checkbox / whatsapp_optin / ivr_confirmation / mou_clause / sms_keyword), evidence reference and URL, language shown, and **notice_version**. That last field is what lets you prove which version of your privacy notice a given farmer actually saw.

`comm.suppression` is a hard block keyed on the normalised value, optionally per channel. It **outranks a fresh opt-in** — smoke test 8 proves this. This is what protects you when someone re-imports an old list containing a number that complained.

`comm.v_messageable_farmer` is the only approved recipient source for campaigns. It requires: an opted-in non-expired consent row, a contact point not marked do_not_contact, fewer than 3 delivery failures, quality tier ≠ quarantine, not soft-deleted, and no suppression match.

🔴 **Enforce this at the query layer.** The API must never let a campaign read from `core.farmer` directly. Make it a code-review rule and add a CI grep.

`comm.message` is partitioned monthly by `sent_at`. At 200k sends/month you will cross 5M rows in two years; the partition makes retention deletes instant (`DROP TABLE` on an old partition) rather than a multi-hour `DELETE`.

### 4.8 `crm` — commercial

`crm.opportunity` has `weighted_value_inr` as a **generated column** (`value_inr * probability_pct / 100`), so forecast queries never recompute it and can index it. A check constraint requires `loss_reason` when `stage = 'lost'` — small rule, enormous downstream value, because a pipeline with unexplained losses teaches you nothing.

A BEFORE UPDATE trigger writes `crm.opportunity_stage_history` on every stage change and resets `stage_entered_at`, which gives you stage-ageing reports for free.

`crm.field_visit.client_uuid` is unique and generated on the device. This is the idempotency key that makes offline sync safe: an agent's phone retrying a sync three times over flaky 2G creates one visit, not three.

`crm.activity` is polymorphic (`subject_type` + `subject_id`) and partitioned monthly. It is the single timeline that shows, on any organisation's page, every call, WhatsApp, email, meeting and visit in order.

### 4.9 `dq` — data quality

| Table | Purpose |
|---|---|
| `dq.source` | The whitelist. `legal_basis` is NOT NULL and `is_approved` requires compliance sign-off. **Collectors must check `is_approved` before running.** |
| `dq.field_provenance` | Sparse per-field lineage: value, source, source_reference, confidence, collected_at, verified_at, `is_current` |
| `dq.contradiction` | When two sources disagree, both values survive and an analyst resolves it |
| `dq.import_batch` | `legal_basis_confirmed` boolean gates commit — an import cannot go live until a named user asserts the lawful basis |
| `dq.import_row_error` | Per-row errors with the raw JSONB, so users get a downloadable error file |
| `dq.merge_event` | Full JSONB snapshot of the merged row → merges are reversible |
| `dq.dedupe_candidate` | Scored pairs awaiting review, with `CHECK (id_a < id_b)` so a pair is stored once |

### 4.10 `audit`

`audit.change_log` (row-level before/after JSONB, partitioned monthly), `audit.data_access_log` (who viewed/exported what, with a **mandatory reason** on exports), `audit.dsr_request` (data-subject access/correction/erasure requests with a due date, so DPDP obligations show up as work items rather than surprises).

## 5. Indexing strategy

| Pattern | Index |
|---|---|
| Fuzzy name search (dedupe, agent search) | `gin (name gin_trgm_ops)` on organisation, person.full_name, farmer.first_name, district, village |
| Array containment (tags, aliases) | `gin` on `tags`, `aliases`, `federation_membership` |
| Flexible attributes | `gin (extra jsonb_path_ops)` |
| Timeline reads | `(subject_type, subject_id, occurred_at DESC)` |
| Pipeline dashboards | `(stage, expected_close_date)`, `(owner_user_id, stage)` |
| Active-row scans | Partial indexes `WHERE NOT is_deleted`, `WHERE status='open'`, `WHERE valid_to IS NULL` |
| Consent lookups | `(channel, purpose) WHERE status='opted_in'` |

Partial indexes matter more than usual here: in a CRM fed by bulk import, a large fraction of rows are soft-deleted, quarantined or historical. Indexing only live rows keeps the working set small.

## 6. Migration and evolution rules

1. **Every change is a reviewed SQL file**, applied by `make db-migrate`; never ad-hoc `psql` against production. There is no migration generator — the DDL carries partitioning, generated columns and triggers no ORM can express, so a schema change is something a person wrote and someone else read.
2. **Additive first.** Add nullable column → backfill in batches → add NOT NULL → drop old column, across separate deploys.
3. **New enum values are appended,** never renamed or removed. `ALTER TYPE ... ADD VALUE` cannot run inside a transaction in older versions — plan the deploy.
4. **Never drop a column in the same release that stops writing to it.** One release to stop writing, one to drop.
5. **Any index on a table over 1M rows uses `CREATE INDEX CONCURRENTLY`.**
6. **New partitions are created a month ahead** by a scheduled job (or use `pg_partman`). A missing partition is an outage — the DEFAULT partitions in the schema are a safety net, not a plan.
7. **Re-run `sql/smoke_test.sql` in CI** on every migration. It takes under a second and it catches trigger regressions that unit tests miss.

## 7. Capacity estimates

| Table | 3-year row estimate | Approx. size |
|---|---|---|
| `core.farmer` | 10,000,000 | ~9 GB + ~6 GB indexes |
| `core.land_parcel` | 18,000,000 | ~5 GB |
| `core.farmer_crop` | 45,000,000 | ~9 GB |
| `core.organisation` | 60,000 | ~120 MB |
| `core.person` | 500,000 | ~400 MB |
| `core.contact_point` | 12,000,000 | ~3 GB |
| `comm.consent_event` | 25,000,000 | ~8 GB |
| `comm.message` | 100,000,000 | ~40 GB (partitioned; drop old partitions) |
| `crm.activity` | 15,000,000 | ~6 GB |
| `audit.change_log` | 200,000,000 | ~60 GB (archive to S3 after 12 months) |
| **Total** | | **~160 GB, comfortably one RDS instance** |

This does not need a data warehouse, a NoSQL store or a microservice fleet. One well-indexed, well-partitioned PostgreSQL instance handles this workload with room to spare. Add a read replica for analytics when dashboard queries start competing with transactional load — not before.
