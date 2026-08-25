# 00 · Executive Summary

## The problem

Theta Analytics sells into the Indian agriculture value chain. Today the knowledge that makes that possible — which FPO in Muzaffarnagar has a working board, which sugar mill's cane officer returns calls, which BD agent spoke to whom last month, which farmer data we already hold — lives in spreadsheets, individual inboxes and people's heads. It does not compound. When an agent leaves, the relationship leaves.

A CRM built for *this* market is not a Salesforce clone. Standard CRMs model Company → Contact → Deal. Indian agri-business models:

```
State → District → Block → Village
                              ↓
   Sugar Mill ← cane command area → Farmer → FPO (a real Companies Act entity with a board)
        ↓                              ↓          ↓
   Procurement officer            Land parcels   MD / CEO / Directors / Member farmers
        ↓                              ↓          ↓
              ─────────  Project / Lead / BD activity  ─────────
```

A farmer is simultaneously a supplier to a mill, a member of an FPO, a landholder, and a messaging recipient with a consent state. No off-the-shelf CRM has that shape.

## What we are building

A single system with six functional pillars:

**1. Master Data Registry.** Farmers, FPOs, ACS/cooperative societies, sugar mills, and the people inside them. One canonical record per real-world entity, with full provenance on every field — we always know where a value came from and when it was last confirmed.

**2. Relationship Graph.** Farmer ↔ FPO membership. Farmer ↔ mill supply. Mill ↔ command-area villages. FPO ↔ directors. Person ↔ multiple organisational roles over time. This graph is the actual product; the contact list is a by-product.

**3. Data Intelligence Layer.** Every field carries a confidence score, a source, and a verification timestamp. The system distinguishes **Gold** (verified by a human in the last 180 days), **Silver** (from an authoritative registry), **Bronze** (unverified/inferred) and **Quarantine** (contradicted, stale, or unlawfully sourced). This is your "useful vs. not useful" section, formalised — see [Doc 07](./07-data-quality-organic.md).

**4. Project Registry.** Every engagement — a biogas plant, a cane-yield analytics pilot, a carbon project, a farm-mechanisation rollout — as a first-class record with stage, value, site, counterparty org, contact persons, documents and a full activity history.

**5. Dual Trackers.**
   - **Agent Tracker** — field/BD staff, their assigned territory and accounts, visits logged with GPS + timestamp, targets vs. actuals, activity streaks.
   - **Business Development Tracker** — the pipeline: leads → qualified → proposal → negotiation → won/lost, with weighted forecast and stage-ageing alerts.

**6. Engagement Engine.** WhatsApp Business Cloud API and transactional/bulk email, driven off consent records, with per-message delivery state written back onto the contact's timeline. Opt-out is honoured system-wide within seconds, across every channel.

## Recommended technology, in one line each

| Layer | Choice | Why |
|---|---|---|
| Database | **PostgreSQL 16** + PostGIS + `pg_trgm` | Geospatial cane command areas, fuzzy name matching for dedupe, JSONB for source payloads, partitioning for 10M+ rows. One database instead of four. |
| Backend | **Django 5 + Django REST Framework** | Admin panel free on day one (huge for a data-heavy CRM), mature ORM, batteries-included auth/permissions/migrations. Python matches your data-science side. |
| Async work | **Celery + Redis** | Scrapers, imports, message sends, exports — all long-running and retryable. |
| Search | **Postgres FTS → OpenSearch at ~2M records** | Don't buy search infra before you need it. |
| Frontend | **React 18 + TypeScript + Vite + TanStack Query + shadcn/ui** | Fast, typed, huge component ecosystem, AG Grid for the 100k-row tables you will absolutely have. |
| Mobile (field agents) | **React Native (Expo)** with offline-first SQLite sync | Rural connectivity is intermittent; offline capture is non-negotiable. |
| Messaging | **WhatsApp Cloud API (direct with Meta)** + **Amazon SES** | Direct Meta integration avoids BSP markup at your volumes. SES is ~₹0.008/email. |
| Hosting | **AWS Mumbai (ap-south-1)** — ECS Fargate + RDS Postgres + S3 | 🔴 Data residency. Indian agri data on Indian soil is both a compliance argument and a sales argument. |

Full rationale, including what we rejected and why, is in [Doc 03](./03-tech-stack.md).

## The hard truth about the farmer data

You asked to scrape every farmer in India — name, phone, email, address, land area — from websites and apps. I am not going to specify that pipeline, and you should not build it. Three concrete reasons, in commercial order:

**1. It gets your WhatsApp Business Account permanently banned.** Meta's policy requires opt-in for every recipient. A scraped list produces block/report rates of 5–15%. Meta's quality algorithm downgrades your number's messaging tier and then disables the WABA. Not "might" — this is the single most common way agri-tech companies lose their WhatsApp channel, and appeals almost never succeed. The channel you most want is the one scraping destroys first.

**2. It is illegal, and the penalty is written in rupees.** Under the Digital Personal Data Protection Act 2023, personal data may only be processed with consent or for a "certain legitimate use." Scraped farmer PII is neither. The exemption for "publicly made available" data applies only where *the data principal themselves* made it public — a farmer whose number sits in a state subsidy portal did not. Penalties run up to ₹250 crore for failure to secure personal data and ₹50 crore for other breaches. Scraping a government portal additionally engages Section 43 of the IT Act 2000.

**3. It makes the asset worthless in a diligence room.** The moment a sugar mill, a bank, a carbon buyer, or an acquirer asks "what is the provenance of these 4 million records?", an answer of "we scraped them" ends the conversation. A smaller, consented, provenance-tracked database is worth more per record *and* in total than a large scraped one.

**What to build instead — this is how you actually get to millions:**

- **Institutional contacts are fair game.** An FPO's registered address, its CIN, and its directors' names and DINs are published by the MCA. A sugar mill's official contact numbers are on its website and in industry directories. This is business data, not personal data, and it is where 100% of your *sellable* B2B pipeline actually comes from. There are ~35,000 registered FPOs and ~530 operating sugar mills — that entire universe is legally compilable.
- **Aggregate farmer data is fair game.** Village-level cropping area, mandi arrivals and prices, land-use statistics, mill-wise cane crushed — all published on data.gov.in, AGMARKNET and state portals. This lets you *target* precisely without holding a single unconsented phone number.
- **Your Theta Analytics data is your moat.** It is consented (verify this), it is yours, and nobody else has it. Doc 06 specifies the ingest, dedupe and consent-tagging pipeline for it.
- **Partnership ingestion scales fastest.** One MoU with an FPO of 1,200 members, signed by its board, with a consent clause, brings 1,200 clean records. Sign 500 FPOs — a realistic two-year BD goal that your own CRM will drive — and you have 600,000 farmers with named provenance and a consent artefact per record. This is *faster* than scraping, because scraped data still requires opt-in before you can message it, and opt-in from a cold scraped list converts at under 2%.
- **Field-agent collection with consent capture** fills the gaps, at roughly 40–80 farmers per agent-day, with the consent recorded in-app.

Doc 05 turns this into a source-by-source catalogue with a legal basis and a collection method for each. The rest of the system is designed on the assumption that you follow it.

## What "good" looks like at the end of 12 months

- 35,000 FPOs and 530 mills in the registry, ~8,000 of them with a verified named decision-maker and a working phone number
- 400,000–800,000 farmers with recorded consent and known provenance, growing 40k/month through partnerships and field capture
- Every BD conversation in the country logged against an organisation, not a person's phone
- WhatsApp quality rating held at **Green**, opt-out under 0.5%
- A data asset a buyer can audit

## Build sequence

| Phase | Weeks | Deliverable |
|---|---|---|
| 0 · Foundation | 1–3 | Repo, CI, Postgres schema, auth, Django admin |
| 1 · Org Registry | 4–9 | FPO + ACS + Mill registry, people, bulk import, search |
| 2 · Farmer Core | 10–15 | Farmer master, land, consent ledger, Theta import |
| 3 · Commercial | 16–22 | Project Registry, BD Tracker, Agent Tracker |
| 4 · Engagement | 23–29 | WhatsApp + Email, templates, campaigns, opt-out |
| 5 · Intelligence | 30–36 | Quality scoring, dedupe/entity resolution, dashboards |
| 6 · Field App | 37–44 | Offline React Native app for agents |
| 7 · Scale & harden | 45–52 | Partitioning, OpenSearch, SOC-style audit, DR |

Detail in [Doc 13](./13-roadmap-and-phases.md).

## Indicative cost

| | Monthly |
|---|---|
| Infrastructure (AWS Mumbai, production + staging) | ₹45,000 – ₹95,000 |
| WhatsApp messaging (200k utility msgs/mo) | ~₹23,000 |
| Email (500k/mo, SES) | ~₹4,000 |
| Third-party data & verification APIs | ₹15,000 – ₹40,000 |
| **Total run-rate** | **₹90,000 – ₹1,65,000** |

Breakdown and the assumptions behind it in [Doc 14](./14-cost-estimate.md).
