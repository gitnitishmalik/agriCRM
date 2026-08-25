# 05 · Data Sourcing & Legal Framework

🔴 **This is the most important document in the set. Read it before writing a single collector.**

*This document explains the law as it applies to your build and gives engineering rules that follow from it. It is not legal advice — before you go live, have an Indian data-protection lawyer review your privacy notice, consent language, retention schedule and partner MoU template. That review costs a few lakh rupees and is the cheapest insurance in the project.*

---

## 1. The regulatory position, as of August 2026

Three regimes apply to what you are building:

### 1.1 Digital Personal Data Protection Act, 2023 + DPDP Rules, 2025

The **DPDP Rules 2025 were notified on 13 November 2025** (G.S.R. 846(E)), and the **Data Protection Board of India was established** at the same time (G.S.R. 844(E)). Enforcement operations began ramping through 2026. The compliance deadline sits between **November 2026 and May 2027** depending on final gazette confirmation of the accelerated timeline.

**In other words: this is live law with a live regulator, and your compliance runway is measured in months, not years.** Building a farmer database in 2026 without designing for DPDP is building a liability.

**What the Act requires of you (you are a "Data Fiduciary"):**

| Requirement | What it means in practice |
|---|---|
| **Lawful basis** (s.4) | Every piece of personal data needs either consent or a listed "legitimate use". There is no "we found it online" basis. |
| **Notice** (s.5, Rule 3) | Before collecting, show a clear, plain-language notice: what data, what purpose, how to withdraw, how to complain. Must be available in English and the Eighth Schedule languages. |
| **Consent** (s.6) | Free, specific, informed, unconditional, unambiguous, by **affirmative action**. Pre-ticked boxes are invalid. Bundled consent is invalid. |
| **Withdrawal** (s.6(4)) | Must be as easy to withdraw as to give. |
| **Purpose limitation** | Data collected for cane advisory cannot be repurposed for selling insurance without fresh consent. |
| **Erasure** (s.8(7), Rule 8) | Delete when the purpose ends, consent is withdrawn, or retention expires. |
| **Security safeguards** (Rule 6) | Seven mandatory controls: encryption at rest and in transit, access restrictions, **data masking**, activity monitoring, **one-year log retention**, incident response procedures, and equivalent safeguards imposed on processors by contract. |
| **Breach notification** (Rule 7) | Notify the Board immediately on discovery; notify affected individuals within **72 hours**. |
| **Grievance redressal** | A named contact and a working process. |

**Penalties:**

| Failure | Maximum penalty |
|---|---|
| Failure to implement reasonable security safeguards | **₹250 crore** |
| Failure to notify the Board or individuals of a breach | **₹200 crore** |
| Non-compliance with children's data protections | **₹200 crore** |
| Failure to meet Significant Data Fiduciary obligations | **₹150 crore** |

### 1.2 The "publicly available data" question — read this carefully

Section 3(c)(ii) exempts personal data "made or caused to be made publicly available **by the Data Principal**" or by someone under a legal obligation to publish it.

The operative words are *by the Data Principal*. A farmer whose phone number appears in a state subsidy beneficiary list did not make it public — a government department did, for a different purpose. That data is **not** exempt.

This is where the "just scrape it, it's public" reasoning fails. The exemption is about a person's own act of publication, not about whether a URL is reachable without a password. Indian legal commentary has consistently read it this way, and the Rules' emphasis on purpose limitation reinforces it.

**What genuinely is exempt or out of scope:**
- Data a person deliberately published about themselves in that capacity (a business owner listing their office number on their own company website)
- **Non-personal data** entirely — the Act only covers personal data. Aggregate statistics, village-level cropping areas, mandi prices, mill crushing volumes: all outside the Act.
- **Business/institutional data** — a company's registered address, its CIN, its board composition as published by the MCA. Directors' names and DINs are published under a statutory obligation, which is the second limb of the exemption.

### 1.3 Information Technology Act, 2000

Section 43 makes unauthorised access to a computer resource, and downloading data from it, actionable. Automated scraping in breach of a portal's terms of use, or that circumvents rate limits, CAPTCHAs or authentication, engages this section. Section 66 makes it a criminal offence when done dishonestly or fraudulently.

Government portals almost universally prohibit automated bulk extraction in their terms of use.

### 1.4 TRAI (for SMS and voice) and Meta's WhatsApp policy

- **TRAI TCCCPR**: commercial SMS and voice require registration on the DLT platform, registered headers, pre-registered content templates, and respect for the DND registry. Sending to DND numbers without valid consent carries penalties independent of DPDP.
- **Meta WhatsApp Business Policy**: opt-in is mandatory for every recipient, obtained through a channel where the person clearly indicated they want messages from *your business*, with your business name stated. Meta explicitly prohibits purchased, rented or scraped lists.

---

## 2. Why scraping farmer PII fails commercially, before it even fails legally

You asked for a nationwide scrape of farmer names, phones, emails, addresses and landholdings. Here is what actually happens if you build it, in the order it happens:

**Week 1–4 — you get the data.** Several million rows. It feels like a win.

**Week 5 — you send the first WhatsApp campaign.** Because these people never opted in, block-and-report rates run 5–15%. (A consented list runs under 0.5%.)

**Week 6 — Meta drops your quality rating to Yellow, then Red.** Your messaging tier is capped. Template approvals start getting rejected.

**Week 7–8 — the WABA is disabled.** Appeals for policy violations on cold lists almost never succeed. You have now lost the single channel with the best reach into rural India, and re-establishing it under a new entity is both slow and itself a policy violation.

**Month 3–6 — the data rots.** Rural phone churn is 15–20% annually and scraped data has no verification loop. Within eighteen months a third of it is dead, and you have no way to tell which third.

**Month 6+ — diligence.** A sugar mill's compliance team, a bank, a carbon-credit buyer, or an acquirer asks: *what is the provenance of these records, and where is the consent?* "We scraped it" ends that conversation. A database you cannot explain is a liability on the balance sheet, not an asset.

**And at any point — a complaint.** One farmer, one grievance to the Data Protection Board, and you are explaining a multi-million-record unconsented database to a regulator that has ₹250 crore of penalty authority and a mandate to establish precedent.

**The comparison that matters:**

| | Scraped list | Consented list |
|---|---|---|
| 1,000,000 records acquired | 1,000,000 | 1,000,000 |
| Valid, reachable numbers | ~600,000 | ~950,000 |
| Legally messageable | **0** | 950,000 |
| WhatsApp opt-in conversion from cold outreach | ~1–2% → ~10,000 usable | n/a — already opted in |
| Regulatory exposure | Up to ₹250 crore | Documented and defensible |
| Value in a diligence room | Negative | Positive |

**A scraped list of a million is worth less than a consented list of fifty thousand.** That is the whole argument.

---

## 3. The approved source catalogue

These are seeded into `dq.source` in [`sql/seed_reference.sql`](./sql/seed_reference.sql). 🔴 **Every collector's first action is to assert `source.is_approved` and fail loudly if not.**

### Tier A — Institutional data (no personal data; collect freely)

| Source | What you get | Method | Cadence |
|---|---|---|---|
| **MCA21 master data** | Every registered FPO/producer company: CIN, registered address, incorporation date, authorised & paid-up capital, status, **directors' names and DINs** | Bulk master-data files published by MCA; per-company lookups for enrichment | Quarterly |
| **SFAC state-wise FPO lists** | FPOs registered under the 10,000 FPO scheme, by state, with promoting agency and cluster | Published PDF/XLSX downloads | Quarterly |
| **NABARD / NCDC / NAFED FPO lists** | FPOs promoted by each implementing agency | Published reports | Quarterly |
| **ISMA member directory** | Private sugar mills: name, location, capacity, group | Published directory | Quarterly |
| **NFCSF directory** | Cooperative sugar factories, state federation membership | Published directory | Quarterly |
| **State Sugarfed / Cane Commissioner** | Licensed mills, **cane command area allocations**, State Advised Price, crushing licences | State portals; many publish season-wise | Annual |
| **State cooperative registrars** | Registered societies (PACS, cane societies), registration numbers, area of operation | State portals; often RTI-able | Annual |
| **LGD (Local Government Directory)** | State/district/block/village codes and hierarchy — ~660k villages | Bulk download | Quarterly |
| **Organisation websites** | Official contact numbers, emails, addresses, named officials as the org itself publishes them | Targeted, low-rate, ToS-respecting fetch | Annual |

**On MCA director data specifically:** names and DINs of company directors are published under a statutory obligation. This is your legally cleanest route to *named decision-makers* at 35,000+ FPOs — which is exactly the "MD, Director, who is who" requirement you described. The MCA does not publish directors' personal mobile numbers, and you should not try to obtain them by other means; you get the name and the organisation, and you reach the person through the organisation's published channels. That is how B2B sales works everywhere.

### Tier B — Aggregate and statistical data (non-personal; collect freely)

| Source | What you get |
|---|---|
| **data.gov.in** | Hundreds of agriculture datasets under the Government Open Data Licence — India, with a documented API |
| **AGMARKNET** | Daily mandi arrivals and prices by commodity and market |
| **e-NAM** | Trade volumes by mandi |
| **Agriculture Census / Land Use Statistics** | Operational holdings by size class, by district |
| **Directorate of Sugar / DFPD** | Mill-wise cane crushed, sugar produced, recovery, by season |
| **Ministry of Agriculture crop estimates** | Area, production, yield by crop, state and district |
| **State horticulture / agriculture department portals** | Block-level cropping patterns |

This tier is how you achieve **targeting at scale without holding a single unconsented phone number.** You can know that Block X in Muzaffarnagar has 18,400 hectares of cane supplying three mills with a combined 42,000 TCD — and build an entire sales strategy on it — without any personal data at all.

### Tier C — Personal data (consent or contract required)

| Source | Legal basis | How it works |
|---|---|---|
| **Partner MoU with an FPO / mill / cooperative** | Contract + consent clause executed with members | The FPO's board resolves to share member data; the membership consent form (or a fresh consent drive) includes a clause naming Theta Analytics and the purposes. Consent artefact filed against the import batch. **This is your highest-volume clean channel.** |
| **Field-agent collection** | Consent from the data principal at the point of collection | Agent shows the notice on-screen in the farmer's language, farmer taps to consent, consent event written with notice version, language, timestamp, GPS, agent ID |
| **Inbound self-registration** | Consent at signup | Web form, WhatsApp opt-in flow, missed-call/IVR, QR code at an FPO office or mill gate |
| **Theta Analytics legacy database** | 🔴 **Pending review — see §7** | Seeded as `is_approved = false` until the basis is documented |
| **Licensed commercial datasets** | Contract with warranties | Only with a written warranty of lawful collection and consent, plus an indemnity. Verify by sampling, don't take it on trust. |

### 🔴 Explicitly prohibited

Do not build, and do not accept from a vendor:

- Bulk extraction of individual farmer contact details from any government beneficiary portal (PM-KISAN, state subsidy lists, land record portals, AgriStack, crop insurance portals)
- Extraction from mobile apps by API reverse-engineering or traffic interception
- Anything requiring credential sharing, CAPTCHA circumvention or rate-limit evasion
- Purchased lists with no documented provenance
- Scraping social media profiles for farmer contact details
- Aadhaar numbers in plaintext, anywhere, ever

---

## 4. How you actually reach millions — the growth model

You said you want scale. Here is the arithmetic that gets you there legally, and faster than scraping would.

### Channel 1 — Partnership ingestion (highest volume)

Your CRM's own BD pipeline is the acquisition engine.

- An average scheme FPO has **500–1,200 members**
- A single MoU with a consent clause brings the whole membership
- A BD agent can realistically close **4–8 FPO partnerships per month** once the pitch is working

| Partnerships signed | Farmers acquired (at 800 avg) |
|---|---|
| 100 | 80,000 |
| 500 | 400,000 |
| 1,500 | 1,200,000 |

**What you offer the FPO in return** (this is the part that makes it work — nobody signs an MoU to give you data for free):
- Free WhatsApp advisory to their members, branded as the FPO's
- A member management dashboard the FPO's CEO can actually use
- Theta's satellite yield analytics for their members' plots
- Aggregated market price intelligence
- Help with their annual return and AGM compliance paperwork

At 35,000 registered FPOs nationally, 1,500 partnerships is **4% penetration** over two to three years. That is a realistic BD target, not a fantasy — and every one of those relationships is also a sales relationship.

### Channel 2 — Mill supplier bases

A single sugar mill has **20,000–90,000 registered cane growers**, already in a database, already with a supplier code, already contactable. Mills have a genuine operational need for grower communication (harvest slips, payment schedules, cane calendar) and generally lack good tooling.

**The offer:** run their grower communication for them, on their WhatsApp number or a co-branded one, in exchange for a data-sharing arrangement covering your own advisory. Five mills = 200,000+ farmers, with the mill's own consent process behind it.

This is the single highest-leverage move available to you given Theta's sugar-sector positioning.

### Channel 3 — Field collection

- 40–80 farmers per agent-day with in-app consent capture
- 20 agents × 22 days × 55 farmers = **~24,000/month**
- Slower and more expensive per record, but the highest quality tier and it reaches farmers no institution has

### Channel 4 — Inbound

- QR codes at FPO offices, mill gates, input dealer shops
- Missed-call number advertised on cane payment slips
- WhatsApp "start" keyword campaigns run *by the partner*, to their own opted-in base
- Cost per record is near zero; volume depends entirely on partner cooperation

### Combined trajectory

| Month | Partnerships | Field | Inbound | Cumulative consented farmers |
|---|---|---|---|---|
| 6 | 20,000 | 40,000 | 5,000 | ~65,000 |
| 12 | 120,000 | 150,000 | 30,000 | ~300,000 |
| 24 | 500,000 | 400,000 | 120,000 | ~1,020,000 |
| 36 | 1,400,000 | 650,000 | 300,000 | ~2,350,000 |

Every one of those records has a consent artefact, a named source, and a verification date. All of it is messageable. All of it survives diligence.

---

## 5. Engineering rules that follow from the law

These are not suggestions. Wire them into the code.

| # | Rule | Where enforced |
|---|---|---|
| R1 | A collector asserts `dq.source.is_approved` before its first request; if false it raises and exits non-zero | Collector base class |
| R2 | Every collector sets a descriptive `User-Agent` with a contact email, respects `robots.txt`, and rate-limits to ≤1 request/second | Collector base class |
| R3 | No collector authenticates, solves a CAPTCHA, or evades a rate limit. If it needs to, stop and ask legal. | Code review |
| R4 | Personal data may only enter via a source of kind `partner_agreement`, `field_collection`, `inbound_signup`, or an approved `theta_analytics` / `purchased_licensed` batch | `dq.source.contains_pii` + import validator |
| R5 | An import batch cannot commit unless `legal_basis_confirmed = true`, set by a named user | `dq.import_batch` + API |
| R6 | Outbound recipients come only from `comm.v_messageable_farmer`. Never from `core.farmer`. | CI grep + code review |
| R7 | Consent is re-checked at dispatch time, not only at segment-preview time | Messaging worker |
| R8 | Aadhaar is stored as salted SHA-256 + last 4 only. Plaintext never touches the database or a log. | Model layer + log scrubber |
| R9 | PII is masked by default in the UI; unmasking requires the `view_full_contact` permission and writes `audit.data_access_log` | Serializer + permission class |
| R10 | Exports over 1,000 PII records require a typed reason and trigger an alert | Export endpoint |
| R11 | Staging and dev never contain production PII | Deployment pipeline check |
| R12 | Logs retained one year (Rule 6 requirement), PII scrubbed from application logs | Log config |
| R13 | Breach runbook exists and is tested: Board notified on discovery, individuals within 72 hours | Runbook + quarterly drill |

---

## 6. Consent design that actually works in the field

**The notice** must be short enough that a farmer will actually hear it. Long legal text read aloud by a field agent gets skipped, and skipped notices invalidate the consent.

Recommended structure — in the farmer's language, on screen, read aloud by the agent:

> **Theta Analytics** wants to send you farming advice, weather alerts and market prices on WhatsApp.
> We will store: your name, village, phone number and how much land you farm.
> We will use it only for: farm advisory, market information, and connecting you to buyers.
> We will **not** sell your information to anyone.
> You can stop anytime by sending **STOP** on WhatsApp, or by telling any of our agents.
> Questions or complaints: [phone] / [email]
>
> ☐ **Yes, I agree** — tap here

**What to record with every consent** (all of it is columns on `comm.consent_event`):
`notice_version` · `language` shown · `evidence_type` · `evidence_ref` · timestamp · agent ID · GPS · device ID · channel · purpose

**Purpose-specific consent, not one blanket tick.** Separate the purposes: advisory, market info, project-specific, marketing. A farmer who agreed to weather alerts did not agree to insurance marketing. The `comm.consent_purpose` enum exists precisely so this is structurally impossible to get wrong.

**For an FPO/mill MoU**, the clause needs to name: Theta Analytics as a recipient, the categories of data, the purposes, the retention period, the member's right to withdraw, and the grievance contact. The FPO must have obtained that consent from members — get a copy of the artefact and file it against the import batch. 🔴 An FPO's board resolution alone is *not* member consent; be careful about accepting it as such.

---

## 7. 🔴 The Theta Analytics legacy database — do this first

You already have farmer data. It is your best asset, and it is also the item with the highest unresolved risk. Before importing a single row:

1. **Document how each batch was collected.** Field survey? Client project? A partner? Purchased? Write it down per batch, not in aggregate.
2. **Find the consent artefacts.** Signed forms, app checkboxes, MoU clauses. If they exist, digitise them and link them to the import batch.
3. **Check the original purpose.** If it was collected for a specific client's yield project, using it for Theta's own outbound marketing is a purpose change requiring fresh consent.
4. **Classify each batch:**
   - **Green** — documented consent, compatible purpose → import as `silver`/`gold`, messageable
   - **Amber** — legitimate collection, unclear or narrower consent → import as `bronze`, **not messageable**; run a re-consent campaign through the original partner
   - **Red** — no documented basis → import as `quarantine` or do not import; if retained at all, it is for reference only and never for outbound
5. **Only then** set `dq.source.theta_analytics.is_approved = true`, batch by batch.

**Re-consent is a legitimate and effective path for Amber data.** If the original relationship was real (a mill's growers, an FPO's members), go back through that institution and run a proper consent drive. Response rates through a trusted institution run 30–60% — far better than anything a cold list produces, and the result is clean.

---

## 8. Compliance checklist before go-live

- [ ] Privacy notice drafted, lawyer-reviewed, translated into Hindi + your operating states' languages
- [ ] Consent flows implemented on web, mobile and WhatsApp, with `notice_version` recorded
- [ ] `dq.source` populated; every entry has a written `legal_basis`; unapproved sources blocked in code
- [ ] Grievance officer named, contact published, response process documented
- [ ] DSR workflow live (`audit.dsr_request`) with due-date tracking
- [ ] Retention schedule configured and an automated anonymisation job running
- [ ] Breach runbook written and drilled — Board on discovery, individuals within 72 hours
- [ ] Rule 6 safeguards in place: encryption at rest and in transit, access controls, data masking, activity monitoring, **one-year log retention**, incident response, processor contracts
- [ ] Processor agreements signed with every vendor touching PII (hosting, messaging, analytics)
- [ ] Theta legacy data classified Green/Amber/Red; only Green is messageable
- [ ] WhatsApp opt-in flow reviewed against Meta's Business Policy
- [ ] Staging confirmed free of production PII
- [ ] Export alerting live
- [ ] Team trained: everyone who touches the CRM understands what they may and may not do with a farmer's number

---

## Sources

- [DPDP Rules, 2025 Notified — Press Information Bureau](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf)
- [DPDP Rules 2025: India's Complete Compliance Guide — Seclore](https://www.seclore.com/fundamentals/dpdp-rules-2025-compliance-guide/)
- [DPDP Act Penalties: Fines Up to ₹250 Crore — TCSA](https://www.tcsa.in/frameworks/dpdp/penalties-enforcement)
- [Digital Personal Data Protection Act 2023, Section 3 — dpdpa.com](https://www.dpdpa.com/dpdpa2023/chapter-1/section3.html)
- [Scraping public data in India: Innovation enabler or privacy threat? — IAPP](https://iapp.org/news/a/scraping-public-data-in-india-innovation-enabler-or-privacy-threat-)
- [Publicly accessible personal data under the DPDP Act — nasscom Community](https://community.nasscom.in/communities/public-policy/publicly-accessible-personal-data-under-dpdp-act-ai-training-and-other)
- [Data scraping consent and India's DPDPA exemption — Law.asia](https://law.asia/india-data-scraping-regulation/)
- [Statewise list of Farmer Producer Organisations (FPOs) — SFAC](https://sfacindia.com/PDFs/List-of-FPO%20identified-by-SFAC/Statewise%20list%20of%20FPOs.pdf)
- [Farmer Producer Organization Scheme — SFAC](https://sfacindia.com/FPOS.aspx)
- [Indian Sugar Mills Association](https://www.indiansugar.com/)
- [National Federation of Cooperative Sugar Factories](https://coopsugar.org/)
- [Open Government Data Platform India — Agriculture sector](https://www.data.gov.in/sector/agriculture)
- [OGD Platform India — APIs](https://www.data.gov.in/apis/?sector=Agriculture)
