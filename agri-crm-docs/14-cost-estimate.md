# 14 · Cost Estimate

All figures in INR. Cloud pricing at AWS ap-south-1 (Mumbai) on-demand rates, USD converted at ₹88. Treat these as planning estimates with ±25% accuracy, not quotes.

---

## 1. Infrastructure

### Year 1 — up to ~500k farmer records, 50 users

| Component | Spec | Monthly |
|---|---|---|
| RDS PostgreSQL | `db.m6g.large` Multi-AZ, 200 GB gp3 | ₹28,000 |
| ECS Fargate — api | 2 tasks × 1 vCPU / 2 GB | ₹6,500 |
| ECS Fargate — workers | 2 tasks × 1 vCPU / 2 GB | ₹6,500 |
| ECS Fargate — beat + webhook | 2 tasks × 0.25 vCPU / 0.5 GB | ₹1,600 |
| ElastiCache Redis | `cache.t4g.medium` | ₹4,200 |
| S3 | 500 GB + requests | ₹1,400 |
| ALB | | ₹2,200 |
| CloudFront | 200 GB transfer | ₹1,800 |
| NAT Gateway | | ₹3,600 |
| Secrets Manager, KMS, CloudWatch | | ₹1,800 |
| Backups | 30-day PITR + snapshots | ₹2,400 |
| **Production subtotal** | | **₹60,000** |
| Staging | scaled down, single-AZ, off overnight | ₹14,000 |
| **Total** | | **₹74,000** |

### Year 2–3 — 2M+ records, 150 users

| Component | Change | Monthly |
|---|---|---|
| RDS | `db.r6g.xlarge` Multi-AZ, 600 GB | ₹62,000 |
| RDS read replica | `db.r6g.large` | ₹22,000 |
| Fargate | 4 api + 4 worker tasks | ₹26,000 |
| Redis | `cache.r7g.large` | ₹11,000 |
| S3 | 3 TB + lifecycle to IA/Glacier | ₹5,500 |
| OpenSearch (if needed) | 3 × `t3.medium.search` | ₹18,000 |
| Network, monitoring, misc | | ₹12,000 |
| **Production subtotal** | | **₹156,500** |
| Staging | | ₹22,000 |
| **Total** | | **₹178,500** |

**Savings available:** Reserved Instances or Savings Plans on RDS and Fargate cut 30–40% on a 1-year commitment. Do this once usage stabilises — around month 6, not on day one. That alone takes Year 2 production from ~₹156k to ~₹105k/month.

---

## 2. Messaging

### WhatsApp (Meta Cloud API direct, India rates effective 1 July 2026)

| Category | Rate |
|---|---|
| Utility | ₹0.115 |
| Authentication | ₹0.115 |
| Marketing | ₹0.8631 |
| Service | Free until 30 Sep 2026, then ₹0.115 |

| Scenario | Volume/month | Mix | Monthly cost |
|---|---|---|---|
| Year 1 early | 50,000 | 95% utility, 5% marketing | ₹7,620 |
| Year 1 steady | 200,000 | 90% utility, 10% marketing | ₹37,970 |
| Year 2 | 800,000 | 92% utility, 8% marketing | ₹1,39,000 |
| Year 3 | 2,500,000 | 95% utility, 5% marketing | ₹3,80,000 |

🔴 **The mix is the whole cost story.** 200,000 messages at 100% utility costs ₹23,000. The same volume at 100% marketing costs ₹1,72,620 — **7.5×**. Categorise correctly, and note that the cheap category is also the one that protects your quality rating.

**If you use a BSP instead:** add 15–30% markup, or ₹15,000–60,000/month in platform fees. At 200k messages/month that's ₹6,000–15,000/month of pure margin transfer. Worth it only for the shared team inbox in year 1; migrate to direct once volume justifies it.

### Email (Amazon SES)

| Volume/month | Cost |
|---|---|
| 100,000 | ₹880 |
| 500,000 | ₹4,400 |
| 2,000,000 | ₹17,600 |

SES is ~$0.10 per 1,000 emails. Add ~₹2,000/month if you use a dedicated IP (needed above ~500k/month).

### SMS (deferred to v2)

₹0.15–0.25 per transactional SMS on DLT. Add ₹5,000–10,000 one-time for DLT entity, header and template registration. Only relevant once you have a use case WhatsApp can't cover.

---

## 3. Third-party services

| Service | Purpose | Monthly |
|---|---|---|
| Sentry (Team) | Error tracking | ₹2,600 |
| Phone verification API | Number validity, carrier lookup | ₹4,000–12,000 |
| MCA data (if using a paid aggregator rather than bulk files) | Company + director enrichment | ₹8,000–25,000 |
| Map tiles (self-hosted) | Included in S3/CloudFront | — |
| Domain, SSL | | ₹300 |
| GitHub Team | | ₹2,000 |
| **Total** | | **₹17,000 – ₹42,000** |

MCA bulk master-data files are the cheaper route and are what the `mca_master` collector is designed for; a paid aggregator is a convenience, not a necessity.

---

## 4. People

### Build phase (12 months)

| Role | Monthly | 12 months |
|---|---|---|
| Tech lead / senior full-stack | ₹1,80,000 | ₹21,60,000 |
| Full-stack engineer | ₹1,10,000 | ₹13,20,000 |
| Frontend engineer | ₹1,00,000 | ₹12,00,000 |
| Mobile engineer (5 months, from month 8) | ₹1,20,000 | ₹6,00,000 |
| Data ops analyst (from month 3) | ₹55,000 | ₹5,50,000 |
| **Subtotal** | | **₹58,30,000** |

*Rates reflect mid-market Indian salaries for experienced engineers outside the top-tier product companies. Adjust for your city and hiring channel.*

### One-off external

| Item | Cost |
|---|---|
| 🔴 Data protection lawyer (notice, consent, MoU template, retention, processor agreements) | ₹1,50,000 – ₹4,00,000 |
| Penetration test | ₹1,50,000 – ₹3,00,000 |
| UI/UX design (5 weeks) | ₹2,00,000 – ₹3,50,000 |
| Translation (Hindi + 3 languages, notices and templates) | ₹60,000 |
| **Subtotal** | **₹5,60,000 – ₹11,10,000** |

### Ongoing operations (post-launch, annual)

| Role | Annual |
|---|---|
| 1 engineer (maintenance + features) | ₹14,00,000 |
| 1–2 data ops analysts | ₹6,60,000 – ₹13,20,000 |
| Field agents (variable — a BD cost, not an IT cost) | ₹25,000–40,000/agent/month |

---

## 5. Field data collection cost

The number that determines whether the growth model in [Doc 05](./05-data-sourcing-and-legal.md) §4 is affordable.

| Channel | Cost per consented farmer |
|---|---|
| **Partnership (FPO/mill MoU)** | ₹2 – ₹8 |
| **Inbound (QR, missed call, WhatsApp opt-in)** | ₹1 – ₹5 |
| **Field agent collection** | ₹35 – ₹90 |
| Third-party list purchase | ₹5 – ₹50 — 🔴 **and legally indefensible without provenance** |

**Field agent economics:** an agent costs ~₹32,000/month loaded and collects ~1,100 consented farmers/month at 50/day × 22 days. That is **₹29/farmer**, and each record arrives Gold-tier with GPS, land data and a signed consent.

**The strategic read:** partnerships are 5–15× cheaper per record than field collection. Field collection's value is quality and reach into places no institution covers. Use partnerships for volume, field agents for depth and verification — and note that the field agents are also your BD team closing the partnerships, so the cost is doing double duty.

---

## 6. Totals

### Year 1

| | Amount |
|---|---|
| Infrastructure (12 × ₹74,000) | ₹8,88,000 |
| Messaging (ramping to 200k/mo) | ₹2,80,000 |
| Third-party services | ₹3,00,000 |
| People (build team) | ₹58,30,000 |
| One-off external | ₹8,00,000 |
| **Total Year 1** | **₹80,98,000** |
| **Excluding people** (if you build it yourself) | **₹22,68,000** |

### Year 2 (run + enhance)

| | Amount |
|---|---|
| Infrastructure (with reserved instances) | ₹15,00,000 |
| Messaging (800k/mo) | ₹16,70,000 |
| Third-party | ₹4,00,000 |
| People (1 engineer + 2 analysts) | ₹27,20,000 |
| **Total Year 2** | **₹62,90,000** |

### Steady-state monthly run rate (year 1 scale)

| | Monthly |
|---|---|
| Infrastructure | ₹74,000 |
| Messaging (200k WhatsApp + 500k email) | ₹42,000 |
| Third-party | ₹25,000 |
| **Total** | **₹1,41,000** |

Excluding people. This is the number to quote when someone asks "what does it cost to run."

---

## 7. Where the money actually goes, and what to cut

**Cost drivers, ranked:**

1. **People — 72% of Year 1.** Everything else is noise by comparison. The single biggest lever on total cost is scope discipline, not infrastructure choice.
2. **Messaging category mix.** Utility vs. marketing is a 7.5× multiplier on your largest variable cost.
3. **RDS.** ~40% of infrastructure. Right-size, and buy reserved instances at month 6.
4. **Field collection.** Scales linearly with agents. Partnerships break that linearity.

**Safe to cut in year 1:**
- OpenSearch — you don't need it under 2M records
- Read replica — add when dashboards start competing with transactional load
- Dedicated email IP — shared is better under 500k/month
- Paid MCA aggregator — bulk files are free
- Multi-AZ on staging — single-AZ is fine for non-production

**Never cut:**
- 🔴 The lawyer. A ₹3 lakh engagement against a ₹250 crore penalty ceiling is not a place to economise.
- 🔴 Multi-AZ on production RDS. A single-AZ database failure during crushing season is a business-stopping event.
- 🔴 Backups and a *tested* restore.
- 🔴 The data ops analyst. Without one, quality decays faster than it improves, and you end up with the scraped-list problem by a slower route.

---

## 8. Cost per record, and why it matters

At Year 2 scale — 2M farmers, 1.6M messages/year, ₹63 lakh annual cost:

**₹31 per farmer per year, all-in.**

If a consented, verified, messageable farmer relationship is worth more than ₹31/year to Theta Analytics — through advisory subscriptions, mill analytics contracts, input commissions, carbon aggregation, or simply as the asset that wins mill contracts — the system pays for itself.

Compare: a scraped record costs perhaps ₹0.50 to acquire and is worth **zero**, because you cannot legally message it, cannot verify it, and cannot explain it to a buyer. The cheap route has a lower cost per record and a lower total value than doing nothing at all.

---

## Sources

- [WhatsApp API Pricing 2026: Official Rates & Calculator — FlowCall](https://www.flowcall.co/blog/whatsapp-business-api-pricing)
- [WhatsApp Business API Pricing in India (2026) — AiSensy](https://aisensy.com/pricing)
- [DPDP Act Penalties: Fines Up to ₹250 Crore — TCSA](https://www.tcsa.in/frameworks/dpdp/penalties-enforcement)
