# 08 · FPO, ACS & Sugar Mill Registry — Module Specification

## 1. Purpose

One registry, one record per real-world organisation, covering FPOs, cooperative societies (ACS/PACS/cane societies), sugar mills, federations, dealers and the institutions around them — with the people inside them, the relationships between them, and a provenance trail on every field.

This is the module that answers the questions your BD team actually asks:

- Which FPOs in this district handle sugarcane, have over 500 members, and have a working phone number for their CEO?
- Who is the cane manager at every mill within 80 km of this location?
- Which cooperative societies feed which mill, and how many growers does that represent?
- Which of our 400 FPO contacts have we not spoken to in six months?
- Which mills have a CBG or ethanol plant already — and which don't but have the capacity to justify one?

---

## 2. The universe you are cataloguing

| Entity | Approximate national count | Primary sources |
|---|---|---|
| Registered FPOs / producer companies | ~35,000 (10,000 FPO scheme + earlier cohorts + state programmes) | MCA21, SFAC, NABARD, NCDC, NAFED, state agri departments |
| Sugar mills (all, incl. non-operating) | ~700–750 | ISMA, NFCSF, state sugarfeds, DFPD |
| Sugar mills operating in a given season | ~520–540 | ISMA/NFCSF season reports, cane commissioners |
| PACS (Primary Agricultural Credit Societies) | ~95,000+ | State cooperative registrars, NABARD |
| Cane growers' cooperative societies | ~2,500 (concentrated in UP, MH, KA, TN, GJ) | State cane commissioners |
| Cooperative federations / sugarfeds | ~30 | NFCSF, state portals |

Your realistic v1 scope: **all FPOs, all sugar mills, all federations, and cane societies in your operating states.** The 95,000 PACS are a phase-2 target — catalogue them state by state as you enter each state, not all at once.

---

## 3. FPO record specification

### 3.1 Identity block

| Field | Required | Source | Notes |
|---|---|---|---|
| `org_code` | auto | system | `FPO-{STATE}-{NNNNNN}` |
| `name` | ✅ | MCA / SFAC | Registered name, exactly as registered |
| `name_local` | | field | Devanagari or local script |
| `short_name` | | manual | What people actually call it |
| `aliases[]` | | all | GIN-indexed. FPOs typically have 3–4 name variants in circulation. |
| `cin` | ✅ where a producer company | MCA | 21 chars. **Your join key to everything MCA publishes.** |
| `registration_no` | ✅ where a cooperative | State registrar | |
| `registration_act` | | registrar | e.g. "UP Cooperative Societies Act 1965" |
| `registration_date` | | MCA / registrar | |
| `legal_form` | ✅ | derived | producer_company / cooperative_society / section_8_company |
| `established_year` | | MCA | |
| `status` | ✅ | derived | prospect / active / dormant / defunct |

### 3.2 Location block

State → district → block → village, each resolved to an **LGD code**, plus address lines, pincode and lat/long. Village-level resolution matters: FPO catchments are village clusters, and "which FPOs operate near this mill" is a query you will run constantly.

### 3.3 Scale and operations (`core.fpo_profile`)

| Field | Why it matters commercially |
|---|---|
| `member_count`, `women_member_count` | Primary sizing metric. Women-member share also determines eligibility for several schemes. |
| `shareholder_count`, `paid_up_capital`, `authorised_capital` | Financial seriousness. A ₹5,000 paid-up FPO is a shell; a ₹15 lakh one is a business. |
| `business_lines[]` | input_sale / output_aggregation / custom_hiring / processing / seed_production / storage |
| `licences[]` | **seed, fertiliser, pesticide, FSSAI, mandi licence.** These determine what an FPO can legally trade — the single best qualification signal for whether an FPO is actually trading. |
| `has_storage`, `storage_capacity_mt` | |
| `has_processing_unit`, `processing_details` | |
| `custom_hiring_centre` | Machinery availability — relevant for mechanisation projects |
| `annual_turnover_inr` + `turnover_fy` | The real activity indicator |
| `equity_grant_received`, `credit_guarantee` | Scheme support received |
| `cbbo_name`, `implementing_agency` | **SFAC / NABARD / NCDC / NAFED.** Knowing the CBBO gives you a partner who can introduce you to 20 more FPOs at once — this field is a BD multiplier. |
| `last_agm_date`, `last_annual_return_fy` | 🔴 **The best dormancy signal available.** An FPO that hasn't filed an annual return in two years is almost certainly not operating, whatever the registry says. |
| `primary_crops[]` | Segmentation |

### 3.4 People (`core.person` + `core.person_org_role`)

For each FPO, capture:

| Role | Priority | Typical source |
|---|---|---|
| **Managing Director / CEO** | 🔴 Highest — this is the decision-maker | MCA directors, field visit, FPO website |
| **Chairman** | High — often the political/community authority | MCA, field |
| **Directors (board, typically 5–15)** | Medium | **MCA master data — names + DINs published by statute** |
| **Secretary / Accountant** | Medium — operational gatekeeper | Field |
| **CBBO resource person** | Medium — external, but the introducer | CBBO |

Each role carries `valid_from`/`valid_to`, `is_primary_contact`, `is_decision_maker`. Board elections happen annually — close old roles, never overwrite them.

Contact points hang off the person, not the FPO. A director's mobile is a person's contact point; the FPO's landline is the organisation's.

🔴 **On directors:** MCA publishes names and DINs. It does not publish personal mobile numbers, and you should not attempt to obtain them by other routes. You reach a director through the FPO's published contact channels or by meeting them — which is what a field agent is for. The name alone is enormously valuable: "May I speak to Mr Ramesh Chaudhary, the MD?" is a completely different call from "May I speak to whoever's in charge?"

### 3.5 Members

`core.farmer_org_link` with `relationship = 'fpo_member'`, `member_code`, `shares_held`, `joined_on`, `left_on`, `is_active`. Populated only via a partner MoU or field collection — never inferred.

---

## 4. Sugar mill record specification

### 4.1 Capacity and operations (`core.sugar_mill_profile`)

| Field | Notes |
|---|---|
| `ownership` | private / cooperative / public_sector / joint_sector. **Determines the entire sales motion** — a private group buys centrally; a cooperative decides by board. |
| `crushing_capacity_tcd` | Tonnes cane per day. The sizing metric. Range 1,250 (small co-op) to 20,000+ (large private). |
| `installed_year` | |
| `cogeneration_mw` | Bagasse power |
| `distillery_capacity_klpd` | Ethanol |
| `has_ethanol_plant`, `has_cbg_plant`, `refinery_capacity_tpd` | Diversification profile — directly qualifies biogas/CBG opportunities |
| `avg_recovery_pct` | Sugar recovery. 9–13% typical. **A recovery problem is a cane-quality problem, which is exactly what analytics sells into.** |
| `registered_cane_growers` | 20,000–90,000 typical. Your farmer-acquisition prize. |
| `cane_command_villages` | |
| `cane_price_srp_inr` | State Advised Price paid |
| `season_start_month`, `season_end_month` | Typically Nov–Apr. 🔴 **Drives your entire sales calendar** — a mill's decision-makers are unreachable during crushing and available May–September. |
| `is_operational` | Mills close and reopen between seasons |
| `federation_membership[]` | ISMA / NFCSF / state sugarfed |
| `cane_payment_status`, `cane_arrears_inr_cr` | Arrears are public, politically sensitive, and a live commercial signal. A mill in arrears has different priorities. |

### 4.2 Command area (`core.mill_command_village`)

The reserved cane area, as village rows with distance, registered growers and cane area per season.

**Why this is the highest-value table in the module:** it lets you answer, with a single query, "how many of our consented farmers are inside Mill X's command area" — which is the entire pitch when you walk into that mill. Nobody else can answer it.

Source: state cane commissioner allocations, published annually in most cane states.

### 4.3 People at a mill

| Role | Why |
|---|---|
| **Cane General Manager / Cane Manager** | 🔴 Owns the grower relationship. Your primary contact for anything farmer-facing. |
| **Unit Head / General Manager (Works)** | Operations, capex |
| **Managing Director** (group level) | Multi-unit groups decide centrally |
| **Procurement Head** | |
| **Chairman + board** (cooperatives) | For a cooperative mill, the elected board decides, not the management |
| **Cane Development Officers** | Field-level, often the actual users of anything you deploy |

For a cooperative mill the board matters more than the management; for a private group the group MD matters more than the unit head. Model both — `parent_org_id` links units to the group.

---

## 5. ACS / cooperative society specification

`core.cooperative_profile`: `society_type` (PACS / cane society / dairy / marketing / credit / multipurpose), `registration_act`, `affiliated_to_org_id` (the parent federation or mill), `is_pacs`, `is_computerised`, `deposit_base_inr`, `loan_outstanding_inr`, `area_of_operation`, `villages_covered`.

**Cane societies specifically** are the interesting sub-type for your sugar positioning: they sit between the grower and the mill, handle cane supply logistics and often the payment flow, and are frequently the actual point of contact for a mill's grower base. Link them with `affiliated_to_org_id` → the mill.

`is_computerised` is a real segmentation axis — the PACS computerisation programme created a cohort of societies with digital infrastructure and a mandate to use it, which makes them a far easier sell for anything software-shaped.

---

## 6. Screens

### 6.1 Registry list

Virtualised table (AG Grid), server-side pagination, with:

**Filters:** type · status · state / district / block · legal form · member count range · capacity TCD range · quality tier · completeness range · has verified contact (Y/N) · has named decision-maker (Y/N) · owner · tags · last activity date · implementing agency · business lines · licences held

**Columns (configurable, saved per user):** name · type · district · members/capacity · primary contact name · phone (masked) · quality tier · completeness · owner · last activity

**Bulk actions:** assign owner · add tag · export (🔴 with reason capture) · add to campaign segment · queue for verification

**Saved views**, private or shared. Expect these to be created constantly: "UP cane FPOs >500 members with a verified MD phone" is a view someone will want every week.

### 6.2 Organisation detail

```
┌───────────────────────────────────────────────────────────────────┐
│ Bhainswal Kisan Producer Company Limited          [🥇 Gold] [82%] │
│ FPO · Producer Company · Muzaffarnagar, Uttar Pradesh             │
│ CIN U01100UP2021PTC123456 · 1,250 members (310 women) · est. 2021 │
│ Owner: A. Sharma · Last activity: 12 days ago    [Verify] [Edit]  │
├───────────────────────────────────────────────────────────────────┤
│ Overview │ People │ Members │ Activity │ Projects │ Documents │ DQ │
├───────────────────────────────────────────────────────────────────┤
│ PEOPLE                                                             │
│ ★ Ramesh Chaudhary    MD & CEO       +91 98XXX XX210  [reveal]    │
│   Sunil Kumar         Chairman        +91 99XXX XX445  [reveal]    │
│   Board (11 directors from MCA)                       [expand]     │
│                                                                    │
│ PROFILE                                                            │
│ Business lines: input sale · output aggregation · custom hiring    │
│ Licences: seed · fertiliser                                        │
│ Paid-up capital ₹12.5L · Turnover FY2024-25 ₹1.8Cr                │
│ Last AGM 14 Sep 2025 · Annual return filed FY2024-25 ✓             │
│ Implementing agency NABARD · CBBO: Agrivision Services             │
│ Primary crops: Sugarcane, Wheat                                    │
│                                                                    │
│ RELATIONSHIPS                                                      │
│ 1,250 members · 842 linked in CRM · 610 consented                  │
│ Supplies: Khatauli Sugar Mill (16,000 TCD)                         │
│                                                                    │
│ DATA QUALITY                                                       │
│ Sources: MCA master data · SFAC list · Field visit 12 Aug 2026     │
│ Last verified 12 days ago · 0 open contradictions                  │
└───────────────────────────────────────────────────────────────────┘
```

The **DQ tab** shows every field with its source, collected date, confidence and verification state, plus any open contradictions with a resolve action.

### 6.3 Create / edit with duplicate blocking

On typing a name + district, the form queries the blocking predicate live. Matches above 0.6 similarity render as a blocking panel: *"3 similar organisations exist in Muzaffarnagar — is it one of these?"* with links and an explicit "No, this is different" override that is logged.

🔴 This one interaction prevents more duplicates than every batch dedupe job combined. Preventing a duplicate at creation costs one second; merging one later costs an analyst ten minutes and risks data loss.

### 6.4 Map view

MapLibre, with layers: FPO points clustered by district · mill points sized by TCD · mill command areas as polygons · consented-farmer density choropleth · agent territories. Click a mill → command villages highlight → "842 consented farmers in this command area" → one click to a campaign segment.

### 6.5 Merge

Side-by-side field comparison with per-field radio selection, a preview of the merged record, a count of children that will be re-pointed, and a confirmation. Writes `dq.merge_event`; reversible for 30 days.

---

## 7. Bulk import

The FPO/mill registry is built primarily by import. The flow ([Doc 06](./06-ingestion-pipeline.md) §3.3): upload → column mapping (saved per partner) → dry run with counts and a 20-row preview → 🔴 legal-basis confirmation → commit in 5,000-row transactions → downloadable error file → reversible for 7 days.

Ship a **template XLSX** per entity type with the expected columns, an example row, and a data-dictionary sheet. Partners will use it if you give it to them, and it eliminates most mapping work.

---

## 8. Build sequence for this module

| Sprint | Deliverable |
|---|---|
| 1 | `ref` geography loaded from LGD (this must be first — everything joins to it) |
| 2 | `core.organisation` + three type profiles, Django admin usable, manual CRUD |
| 3 | People, roles, contact points; masking; primary-contact rules |
| 4 | Bulk import: mapping UI, dry run, error file, commit |
| 5 | Collectors: `lgd_sync`, `mca_master`, `sfac_fpo` |
| 6 | Collectors: `isma_directory`, `nfcsf_directory`, `state_sugarfed` (UP + MH first) |
| 7 | React registry list, filters, saved views, detail page |
| 8 | Duplicate blocking at creation, merge UI, dedupe queue |
| 9 | Map view, command-area layer |
| 10 | Quality tiers, completeness scoring, DQ tab |

Ten sprints ≈ 20 weeks with one full-time engineer, or 10–12 weeks with two. Django Admin makes sprints 2–6 usable by your data-ops team **before** the React UI exists — start loading real data in week 4, not week 20.
