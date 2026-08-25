# 07 · Data Quality — Making the Data Real and "Organic"

You asked how to make the data organic and real for everything. This document is the answer. It is the module you described as "a section of data where useful important information and unuseful" — formalised into something the system can compute, act on, and prove.

## 1. What "organic and real" actually means, operationally

Four properties, in order of how hard they are to achieve:

| Property | Question it answers | How the system delivers it |
|---|---|---|
| **Provenance** | Where did this value come from? | `dq.field_provenance` + `dq.source` on every tracked field |
| **Verification** | Has a human or a machine confirmed it's true, and when? | `verified_at`, `verified_by`, verification workflows |
| **Freshness** | Is it still true today? | Confidence decay + re-verification cycles |
| **Coherence** | Does it agree with everything else we know? | Cross-field rules + `dq.contradiction` |

A record with all four is *organic* — it grew from a real interaction with a real farm, and the system can show its roots. A scraped record has none of them: no provenance you can defend, no verification, no freshness signal, and no way to detect when it goes wrong.

**The strategic point:** data quality is not hygiene work you do after building the CRM. It *is* the product. Anyone can accumulate rows. A database where every row can answer "how do you know?" is the thing that is worth money.

---

## 2. Quality tiers

Every farmer, organisation and person record carries a `quality_tier`. This is your useful/not-useful split, with rules instead of opinions.

### 🥇 Gold — verified and fresh

**Criteria (all must hold):**
- At least one contact point with `verification = 'verified'`
- Verified within the last **180 days**
- `completeness_score` ≥ 70
- Consent recorded where the record is personal data
- Zero open contradictions
- Zero delivery failures in the last 90 days

**Meaning:** you can call this person today and reach them. Use for outbound campaigns, sales targeting, client-facing counts, and anything you put in a proposal.

### 🥈 Silver — authoritative but unverified by us

**Criteria:**
- Sourced from a `public_registry`, `partner_agreement` or `open_government_data` source
- `completeness_score` ≥ 45
- No open contradictions
- Either never verified by us, or verified 180–540 days ago

**Meaning:** probably true, not confirmed. Use for market sizing, territory planning, and as the work queue for verification. Messageable **only** if consent exists independently.

### 🥉 Bronze — unverified, incomplete, or inferred

**Criteria:**
- `completeness_score` < 45, **or**
- Only source is `inferred` / `manual_entry` / unaudited legacy, **or**
- Last verified more than 540 days ago

**Meaning:** a lead, not a fact. Never in a client-facing number. Never messaged. Shown in the UI with a visible "unverified" badge.

### 🚫 Quarantine — do not use

**Triggers (any one):**
- ≥3 delivery failures on all its contact points
- An unresolved contradiction older than 30 days
- Source is not `is_approved`, or the legal basis was withdrawn
- Flagged by an analyst as suspect
- A DSR erasure request is in flight
- Duplicate of a record that won a merge, pending cleanup

**Behaviour:** excluded from search by default, excluded from every campaign (enforced in `comm.v_messageable_farmer`), excluded from all reported counts, but **never silently deleted**. Quarantine is a review queue, not a bin.

### Tier distribution to aim for

| Tier | Realistic target at 12 months |
|---|---|
| Gold | 15–25% |
| Silver | 40–50% |
| Bronze | 25–35% |
| Quarantine | <5% |

If Gold is under 10% you are collecting faster than you are verifying — the classic failure mode. If Quarantine exceeds 10%, a source has gone bad and you should find out which.

---

## 3. Completeness scoring

A weighted checklist per entity type, producing 0–100. Recomputed nightly and on every write.

### Farmer (100 points)

| Field | Points |
|---|---|
| Name (first + last) | 10 |
| Father/spouse name | 8 |
| Verified mobile number | 15 |
| Village resolved to an LGD code | 12 |
| District + block | 5 |
| `total_area_ha` present | 10 |
| `area_source` = document or satellite | 8 |
| At least one `land_parcel` | 7 |
| At least one `farmer_crop` for the current year | 8 |
| Gender | 3 |
| FPO or mill linkage | 7 |
| Consent recorded | 7 |

### Organisation (100 points)

| Field | Points |
|---|---|
| Name + type + legal form | 8 |
| CIN or registration number | 12 |
| Full address to village/block | 10 |
| Verified organisation phone | 12 |
| Email | 6 |
| **At least one named person with a role** | 15 |
| **Primary contact identified** | 10 |
| Member count (FPO) / capacity TCD (mill) | 10 |
| Established year | 4 |
| Type-profile extension row populated | 8 |
| At least one document attached | 5 |

Note the weighting: **25 of 100 points hang on knowing a named human being and which one to call.** That is deliberate. An organisation record with a perfect address and no name is a directory entry; with a named MD and a working number it is a sales asset.

### Person (100 points)

Name 15 · role at an organisation 20 · verified phone 25 · email 10 · designation 10 · father/spouse 5 · district 5 · DIN 10

---

## 4. Freshness and decay

Data does not stay true. In rural India it decays fast:

| Fact | Realistic annual change rate |
|---|---|
| Farmer phone number | 15–20% |
| FPO board composition | 25–35% (annual elections) |
| FPO operational status | 10–15% become dormant |
| Mill cane officer | 20–30% |
| Landholding | 3–5% |
| Cropping pattern | 20–40% (season to season) |
| Mill crushing capacity | <5% |

### Decay function

```
effective_confidence = base_confidence × decay_factor(days_since_verified, field_class)

field_class          half_life
─────────────────────────────
contact              365 days     (phones, emails)
role                 540 days     (who holds which post)
operational          270 days     (is this FPO still active, is the mill crushing)
attribute            1095 days    (land area, capacity, established year)
static               ∞            (CIN, registration number, village location)

decay_factor = 0.5 ^ (days_since_verified / half_life)
```

### Automatic tier transitions

Run weekly:

| Condition | Action |
|---|---|
| Gold, last verified >180 days | → Silver |
| Silver, last verified >540 days | → Bronze |
| Any tier, ≥3 delivery failures | → Quarantine |
| Any tier, open contradiction >30 days | → Quarantine |
| Bronze, newly verified | → Gold |

This is what stops the database from quietly becoming fiction. A CRM without decay looks healthy forever and is wrong within two years.

---

## 5. Verification methods, cheapest to most expensive

| Method | Cost/record | Confidence gained | Scale | Use for |
|---|---|---|---|---|
| **WhatsApp delivery receipt** | ~₹0.12 | 0.75 | Unlimited | Confirms a number is live. Cheapest signal you have. |
| **WhatsApp read receipt** | included | 0.85 | Unlimited | Confirms a *person* is behind it |
| **Reply to a message** | included | 0.95 | Depends on engagement | Strongest passive signal |
| **Email engagement (open/click)** | ~₹0.01 | 0.70 | Unlimited | Weaker (image proxies inflate opens) — trust clicks, not opens |
| **Missed-call / IVR confirmation** | ~₹0.30 | 0.90 | High | Good for low-literacy verification |
| **OTP verification** | ~₹0.15 | 0.95 | High | Number + person, decisively |
| **Outbound call by tele-caller** | ~₹8–15 | 0.95 | ~80/day/caller | Organisation contacts, board changes |
| **Field visit with GPS** | ~₹40–120 | 1.00 | 50/day/agent | Land, identity, high-value accounts |
| **Document capture** | ~₹20 | 1.00 | Moderate | Land records, registration certificates |
| **Satellite cross-check (Theta's own capability)** | ~₹2 | 0.85 | Unlimited | **Land area and crop verification — your unfair advantage** |

### The Theta advantage, spelled out

You already do satellite analytics. That gives you a verification loop nobody else building an agri CRM has:

> Farmer declares 3.5 ha of sugarcane in village X.
> → Theta's imagery for that parcel shows 2.1 ha under cane.
> → System flags the discrepancy, sets `area_source='self_declared'`, confidence 0.45, raises a `dq.contradiction`.
> → Agent visits, walks the boundary on GPS, records 2.3 ha with `verification_method='gps_walk'`.
> → `area_verified=true`, confidence 1.00, tier → Gold.

**Build this loop in Phase 5.** It converts your existing analytics capability into a data-quality moat, and it is the single most defensible feature in the whole system. No competitor with a scraped list can do it at all.

---

## 6. Coherence rules — cross-field validation

Run nightly across the whole database; violations raise `dq.contradiction`.

| Rule | Severity |
|---|---|
| `sum(land_parcel.area_ha)` differs from `farmer.total_area_ha` by >10% | Medium |
| `sum(farmer_crop.area_ha)` for a season exceeds `total_area_ha` × 1.1 | High |
| `irrigated_area_ha > total_area_ha` | High (also a DB check constraint) |
| Farmer's village not inside the district of their claimed FPO's operating area | Medium |
| Farmer's supplying mill more than 100 km away | Medium (possible but unusual) |
| FPO `member_count` less than the count of linked member farmers | High |
| `women_member_count > member_count` | High (also a DB check constraint) |
| Mill `crushing_capacity_tcd` outside 100–50,000 | High |
| Mill `avg_recovery_pct` outside 7–14 | Medium |
| FPO marked `active` but no activity in 24 months | Low → "possibly dormant" flag |
| Two organisations sharing a phone number | Medium → dedupe candidate |
| More than 20 farmers sharing one phone number | High → likely an agent's or a dealer's number entered repeatedly |
| Farmer DOB implying age <18 or >100 | High |
| `established_year` after `registration_date` | Medium |

That "20 farmers sharing one phone" rule catches one of the most common real-world data problems in Indian agri datasets: a field agent or an input dealer enters their own number for everyone they register. It quietly destroys the messaging value of thousands of records, and nothing but a coherence rule will find it.

---

## 7. The Data Intelligence UI

### 7.1 Data Health dashboard

- Tier distribution (stacked area, over time, by state)
- Completeness histogram by entity type
- Verification funnel: total → has contact → verified contact → verified in last 180 days
- Decay pipeline: how many records fall a tier in the next 30/60/90 days
- Source scorecard: per source — records contributed, average completeness, contradiction rate, delivery success rate
- Coverage map: consented farmers per district, and the gap to estimated farmer population

**The source scorecard is the most actionable panel.** When one source consistently produces a 30% contradiction rate and 60% delivery failure, you stop using it. Without the scorecard you never find out.

### 7.2 Work queues

| Queue | Contents | Worked by |
|---|---|---|
| **Contradictions** | Open `dq.contradiction`, both values, both sources, both timestamps, side by side | Data Ops |
| **Dedupe review** | `dq.dedupe_candidate` scored 0.75–0.92, with a field-by-field diff | Data Ops |
| **Verification queue** | Silver records in an agent's territory, prioritised by commercial value | Field agents |
| **Quarantine review** | Quarantined records with the reason, and a path back | Data Ops |
| **Import errors** | Failed rows with reasons, downloadable and re-uploadable | Data Ops |
| **Stale high-value** | Gold records about to decay, on accounts with open opportunities | BD |

The last queue is the one people forget and the one that pays. A Gold contact at an account with a ₹45 lakh opportunity, about to go stale, is worth a phone call today.

### 7.3 Record-level display

Every record page shows a quality strip: tier badge, completeness bar, "last verified N days ago", source chips, and a "Verify" button. Every field with provenance shows a small source icon on hover: *"MCA master data, collected 12 Mar 2026, confidence 0.85."*

Making provenance visible in the UI changes behaviour. People stop trusting numbers they can see are two years old and unverified — which is exactly what you want.

---

## 8. Governance

| Role | Responsibility |
|---|---|
| **Data Steward** (one per domain: farmer, org, project) | Owns definitions, resolves escalated contradictions, approves merges over 1,000 records |
| **Data Ops Analyst** | Works the queues daily |
| **Compliance Officer** | Approves new sources, audits consent, handles DSRs |
| **Engineering** | Pipeline reliability, scoring correctness, keeps the rules running |

**Weekly data quality review** — 30 minutes, four numbers:
1. Tier distribution change week on week
2. Open contradictions and the oldest one's age
3. Source scorecard outliers
4. Verification throughput vs. decay rate

That fourth number is the one that determines whether your database is getting better or worse. If you verify 4,000 records a week and 6,000 decay, you are going backwards while the row count goes up — which is exactly how a database can look like it's growing while becoming worthless.

---

## 9. 90-day plan to make the existing data organic

| Weeks | Action |
|---|---|
| 1–2 | Classify every Theta legacy batch Green/Amber/Red ([Doc 05](./05-data-sourcing-and-legal.md) §7). Nothing else starts until this is done. |
| 3–4 | Import Green batches with full provenance. Import Amber as `bronze`, non-messageable. Leave Red out. |
| 5–6 | Run coherence rules across everything. Expect a large contradiction backlog — this is the system working, not failing. |
| 7–8 | WhatsApp delivery sweep across all Green consented numbers. Every delivery receipt is a free verification; every failure is a quarantine candidate. |
| 9–10 | Re-consent campaign for Amber data, run **through the original partner institution**, not cold. |
| 11–12 | Field verification sprint on the top 2,000 commercially valuable records (accounts with open opportunities, large landholdings, FPO decision-makers). |
| 13 | Publish the first Data Health report. Set the baseline. Every subsequent week is measured against it. |

By the end of this you will have fewer records than you started with, and they will be worth considerably more. That trade is the whole point.
