# AgriCRM — Farmer, FPO & Sugar Mill CRM for Theta Analytics

**Internal engineering & product documentation**
Version 1.0 · Prepared 24 August 2026 · Owner: Nitish Malik

---

## What this is

This documentation set specifies a production CRM for the Indian agriculture value chain. It covers four connected domains:

| Domain | What it holds |
|---|---|
| **Farmers** | Individual growers — identity, land, crops, geography, consent, engagement history |
| **FPOs** | Farmer Producer Organisations — the registered company, its board (MD/CEO/Directors), member farmers, business lines |
| **ACS / Sugar sector** | Agriculture Cooperative Societies and sugar mills — capacity, crushing season, procurement officers, cane command area |
| **Commercial** | Project Registry, Business Development Tracker, Agent Tracker, leads and contact persons |

Plus the cross-cutting layers: data ingestion, data quality, WhatsApp/Email engagement, security and compliance.

## How to read these documents

Read in this order if you are starting from zero:

| # | Document | Read it for |
|---|---|---|
| 00 | [Executive Summary](./00-executive-summary.md) | The whole system in 10 minutes. Start here. |
| 01 | [Product Requirements](./01-product-requirements.md) | Personas, modules, user stories, acceptance criteria |
| 02 | [Data Model](./02-data-model.md) | Every table, every column, the ER diagram |
| 03 | [Tech Stack](./03-tech-stack.md) | What to build with and *why* — with rejected alternatives |
| 04 | [System Architecture](./04-architecture.md) | Services, deployment, scaling to 10M+ farmer records |
| 05 | [Data Sourcing & Legal](./05-data-sourcing-and-legal.md) | **Read before writing any collector.** Where data legally comes from. |
| 06 | [Ingestion Pipeline](./06-ingestion-pipeline.md) | Collectors, ETL, entity resolution, dedupe |
| 07 | [Data Quality — Making Data "Organic"](./07-data-quality-organic.md) | How records become real, verified and stay fresh |
| 08 | [FPO & ACS Registry Module](./08-fpo-acs-registry.md) | Field-level spec for the org registry |
| 09 | [Project Registry & Trackers](./09-project-registry-and-trackers.md) | BD Tracker, Agent Tracker, lead lifecycle |
| 10 | [Communication Engine](./10-communication-whatsapp-email.md) | WhatsApp Cloud API + email, consent, templates |
| 11 | [API Specification](./11-api-spec.md) | REST contract for every resource |
| 12 | [Security & RBAC](./12-security-rbac.md) | Roles, row-level security, audit, encryption |
| 13 | [Roadmap & Phases](./13-roadmap-and-phases.md) | 12-month build plan, sprint by sprint |
| 14 | [Cost Estimate](./14-cost-estimate.md) | Infra + messaging + people, monthly and annual |
| — | [`sql/schema.sql`](./sql/schema.sql) | Runnable PostgreSQL DDL. Validated against PostgreSQL 16. |
| — | [`sql/seed_reference.sql`](./sql/seed_reference.sql) | State/district reference data + enum seeds |

## The one thing to read if you read nothing else

**Document 05.** The single largest risk to this project is not technical — it is that the farmer contact database is assembled in a way that is illegal under the DPDP Act 2023, gets the WhatsApp Business Account permanently banned, and makes the data commercially worthless to enterprise clients who will ask for provenance. Document 05 explains exactly which sources are safe, which are not, and how to build a database of millions of farmers that is *both* large and defensible.

## Document conventions

- `snake_case` for all database identifiers
- All timestamps are `timestamptz`, stored UTC, displayed Asia/Kolkata
- All monetary values are `numeric(14,2)` in INR unless a `currency` column says otherwise
- All area values are stored in **hectares** (`numeric(10,4)`); acres/bigha/guntha are input conveniences converted at the edge
- `MUST` / `SHOULD` / `MAY` follow RFC 2119 meaning
- 🔴 marks a compliance-critical requirement — do not ship without it
