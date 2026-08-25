# 12 · Security, RBAC & Compliance Controls

This document implements the seven mandatory security safeguards under **Rule 6 of the DPDP Rules 2025**: encryption in storage and transit, access restrictions, data masking, activity monitoring, one-year log retention, incident response, and equivalent processor safeguards by contract.

---

## 1. Roles

| Role | Sees | Can do | MFA |
|---|---|---|---|
| **Field Agent** | Own territory only | Create/edit orgs, farmers, visits, consents in territory. Masked contacts. | Optional |
| **BD Manager** | Own region | Everything an agent can, plus reassign accounts, set targets, view team performance | Optional |
| **Data Ops Analyst** | All records, cross-region | Import, merge, quarantine, resolve contradictions, bulk edit. Reveal contacts (logged). | 🔴 Required |
| **Campaign Manager** | Consented audiences only | Build segments, manage templates, launch campaigns. **Cannot see individual phone numbers.** | 🔴 Required |
| **Project Manager** | Own projects + related orgs | Manage projects, milestones, sites, documents | Optional |
| **Leadership** | All, aggregated | Dashboards, reports. Read-only. Contacts masked. | Optional |
| **Compliance Officer** | All + consent + audit | Manage sources, approve legal bases, handle DSRs, read audit logs. **Cannot edit business data.** | 🔴 Required |
| **System Admin** | All | Users, roles, integrations, config | 🔴 Required |

**Two separations worth noting:**

*Campaign Manager cannot see phone numbers.* They build segments by attribute and launch to them; the send path resolves recipients. This is the strongest single control against a bulk contact leak, because the person with the most reason to want a list is the one who cannot get one.

*Compliance Officer cannot edit business data.* They audit; they do not participate. Separation of duties means the person checking the consent records is not the person who could alter them.

## 2. Permission model

Django groups + custom permissions, evaluated in DRF permission classes:

```
organisation.view · .add · .change · .delete · .merge · .export
farmer.view · .add · .change · .delete · .export · .view_consent
contact.view_masked · .view_full          ← the important split
campaign.view · .create · .approve · .launch
import.create · .commit                    ← .commit is separate; it asserts legal basis
quality.quarantine · .resolve_contradiction · .approve_source
audit.view_access_log · .view_changes
dsr.view · .fulfil
```

`contact.view_full` and `import.commit` are the two permissions to grant sparingly and review quarterly.

## 3. Row-Level Security — the backstop

🔴 Territory scoping is enforced in **PostgreSQL**, not only in application code. An application bug then cannot leak another region's data.

```sql
ALTER TABLE core.organisation ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_territory_policy ON core.organisation
  FOR SELECT
  USING (
    current_setting('app.user_role', true) IN ('data_ops','compliance','admin','leadership')
    OR district_id = ANY (
         string_to_array(current_setting('app.user_districts', true), ',')::int[]
       )
    OR owner_user_id = current_setting('app.user_id', true)::uuid
  );
```

The application sets the session variables on every connection checkout:

```python
with connection.cursor() as c:
    c.execute("SELECT set_config('app.user_id',        %s, true)", [str(user.id)])
    c.execute("SELECT set_config('app.user_role',      %s, true)", [user.primary_role])
    c.execute("SELECT set_config('app.user_districts', %s, true)", [user.district_csv])
```

`set_config(..., true)` scopes to the transaction, so a pooled connection cannot carry one user's context into another user's request. 🔴 Test this explicitly — it is the failure mode that turns RLS from a control into a false sense of security.

Apply the equivalent policy to `core.farmer`, `crm.opportunity`, `crm.lead` and `crm.field_visit`.

## 4. Data masking

Rule 6 requires masking. Implemented in the serializer layer:

| Data | Masked (default) | Full (with `contact.view_full`) |
|---|---|---|
| Mobile | `+91 98XXX XX210` | `+919876543210` |
| Email | `r****h@gmail.com` | `ramesh@gmail.com` |
| Aadhaar | `XXXX XXXX 4821` | 🔴 Never available — only ever the last 4 |
| Bank account | Not stored | — |
| Address | District + block | Full |

Unmasking is per-record, on explicit request (`?reveal=true`), and each reveal writes `audit.data_access_log`. Bulk reveal is not offered; the UI reveals one contact at a time. This is deliberate friction.

## 5. Identity data — collect less

🔴 **Recommendation: do not collect Aadhaar numbers in v1 at all.**

You do not need them. Their stated purpose is deduplication, and phone + village + father's name achieves 95%+ of the same dedupe accuracy. What Aadhaar adds is:

- The highest-severity breach category in Indian law
- Aadhaar Act storage and usage restrictions on top of DPDP
- A hard target that materially raises your attacker profile
- A conversation with every enterprise client's security team

If a specific client contract genuinely requires it, the schema supports it correctly: `aadhaar_hash` (SHA-256 with a per-record salt stored in KMS) plus `aadhaar_last4`. **Plaintext never reaches the database, a log, an export or a cache.** Use it for matching only; never display it beyond the last 4.

The same applies to bank account numbers: store `has_bank_account` boolean, never the number. Payments are not in scope for this CRM, so there is no reason to hold them.

## 6. Encryption

| Layer | Control |
|---|---|
| In transit | TLS 1.2+ everywhere; HSTS; TLS to RDS and Redis enforced, not optional |
| At rest — DB | RDS encryption with a customer-managed KMS key |
| At rest — S3 | SSE-KMS, bucket versioning, public access blocked at the account level |
| At rest — backups | Encrypted with the same KMS key; cross-region copy for DR |
| Application-level | `aadhaar_hash` salts, and any future secret-bearing column, encrypted with a KMS data key |
| Secrets | AWS Secrets Manager with automatic rotation. Nothing in env files, nothing in the repo. `gitleaks` in pre-commit and CI. |

## 7. Network

```
VPC
├── Public subnets   → ALB, NAT gateway only
├── Private subnets  → ECS tasks (api, workers)
└── Isolated subnets → RDS, ElastiCache — 🔴 no route to the internet, ever
```

Security groups are least-privilege and reference each other by group ID, not CIDR. RDS accepts connections only from the ECS task security group. No public database endpoint under any circumstance, including "just for a migration".

WAF on the ALB: managed rule sets for SQLi/XSS, rate-based rules, and geo-restriction if your user base is India-only.

## 8. Audit logging

**`audit.change_log`** — every INSERT/UPDATE/DELETE on business tables, with changed fields and before/after JSONB, actor, IP and request ID. Written by a generic Django signal handler or a database trigger. Partitioned monthly. **Retained 7 years.**

**`audit.data_access_log`** — every PII view, reveal, search, bulk read and export, with record counts and the filter used. 🔴 **Retained one year minimum** (Rule 6 requirement). Exports require a typed reason.

**Alerts:**

| Condition | Severity |
|---|---|
| Export >10,000 PII records by any user | 🔴 High — page someone |
| >500 contact reveals by one user in a day | High |
| Login from a new country | High |
| Failed logins >10 in 15 minutes | Medium |
| Permission grant or role change | Medium |
| Collector run against an unapproved source | 🔴 High |
| Access to a record far outside a user's normal territory | Medium |

The export alert is the important one. Bulk export is how contact databases leave companies, and it is almost always done by someone with legitimate access.

## 9. Retention and erasure

| Data | Retention | Then |
|---|---|---|
| Farmer PII with live consent | While consent is live + 24 months | Anonymise |
| Farmer PII, consent withdrawn | 30 days | Erase (keep the consent event as proof of withdrawal) |
| Organisation data | Indefinite (business data) | — |
| Message logs | 24 months | Delete partition |
| Consent events | 7 years | 🔴 Never delete — this is your evidence |
| Audit change log | 7 years | Archive to S3 Glacier |
| Data access log | 1 year minimum, 3 years recommended | Archive |
| Import raw payloads | 12 months | Delete |
| Exports | 7 days | Auto-delete |
| Backups | 30 days PITR + monthly for 12 months | Rotate |

**Anonymisation, not deletion,** for expired farmer data: replace name, phone, email and precise location with nulls; keep district, land size band, crop and year. You retain the analytical value and hold no personal data. Run monthly.

## 10. Data-subject requests

`audit.dsr_request` with a due date, tracked as work.

| Type | Response |
|---|---|
| **Access** | JSON + human-readable PDF of everything held, plus the full consent history and the list of sources |
| **Correction** | Update, write provenance with `source='data_subject'` at confidence 1.00 |
| **Erasure** | Anonymise the record; retain the consent event as proof; propagate to suppression |
| **Grievance** | Route to the named Grievance Officer, tracked to resolution |
| **Nomination** | Record the nominee per DPDP s.14 |

**SLA: 30 days.** Alert at 7 days remaining. Identity verification is required before fulfilling — and 🔴 the verification itself must not become a data-collection exercise; confirm against data you already hold.

## 11. Incident response

**Rule 7 obligation: notify the Data Protection Board immediately on discovery; notify affected individuals within 72 hours.**

| Hour | Action |
|---|---|
| 0 | Detect. Page the on-call. Open an incident channel. |
| 0–1 | Contain: revoke credentials, isolate, block. Do not destroy evidence. |
| 1–4 | Assess: what data, how many people, what is the risk of harm |
| 4–8 | 🔴 **Notify the Data Protection Board** |
| 8–24 | Draft individual notifications; brief leadership; engage counsel |
| **<72 h** | 🔴 **Notify affected individuals** — what happened, what data, what they should do, who to contact |
| Day 3–7 | Remediate, patch, verify |
| Day 7–14 | Post-mortem, blameless, with tracked actions |

Write this runbook properly, with named people and phone numbers, and **drill it quarterly**. A 72-hour clock is not enough time to work out who to call.

## 12. Vendor and processor management

Every vendor touching personal data is a Processor and needs a written agreement imposing equivalent safeguards (Rule 6).

| Vendor | Role | Data | Location |
|---|---|---|---|
| AWS | Infrastructure processor | All | ap-south-1 (India) |
| Meta (WhatsApp Cloud API) | Messaging processor | Phone numbers, message content | Global — 🔴 document this in the processor register and the privacy notice |
| Amazon SES | Email processor | Email addresses, content | ap-south-1 |
| Sentry | Error tracking | 🔴 Must be configured to scrub PII from payloads before send |
| Google/Meta analytics | — | 🔴 Not on any authenticated page |

Maintain a processor register: vendor, purpose, data categories, location, agreement date, review date. Review annually.

## 13. Application security

| Control | Implementation |
|---|---|
| SQL injection | ORM only; parameterised raw SQL where unavoidable; no string interpolation into SQL, ever |
| XSS | React escapes by default; `dangerouslySetInnerHTML` banned by lint rule |
| CSRF | Django CSRF for session auth; JWT in `Authorization` header (not cookies) for the API |
| File upload | Extension + MIME allow-list, size limits, virus scan, served from S3 with `Content-Disposition: attachment` and never from the app origin |
| Dependencies | Dependabot; `pip-audit` and `npm audit` in CI; build fails on High/Critical |
| Secrets in code | `gitleaks` pre-commit and CI |
| Session | 15-min access token, 7-day rotating refresh, absolute session cap 30 days, revoke-all on password change |
| Password | Django's PBKDF2/Argon2, minimum 12 chars, common-password blocklist |
| Brute force | `django-axes` — lockout after 10 failures in 15 minutes |
| Headers | HSTS, CSP, X-Content-Type-Options, Referrer-Policy, X-Frame-Options DENY |

## 14. Access lifecycle

| Event | Action | SLA |
|---|---|---|
| Joiner | Role assigned by manager, approved by admin; least privilege | Day 1 |
| Mover | Old permissions revoked before new granted | Same day |
| Leaver | 🔴 All access revoked, sessions killed, tokens blacklisted | **Within 24 h** |
| Quarterly | Access review — every role holder confirmed by their manager | Quarterly |
| Annually | Review of `contact.view_full` and `import.commit` holders specifically | Annually |

Offboarding within 24 hours is the control most often skipped and most often exploited.

## 15. Pre-launch security checklist

- [ ] MFA enforced for admin, compliance, data-ops
- [ ] RLS enabled and **tested** on organisation, farmer, opportunity, lead, field_visit — including the pooled-connection case
- [ ] Masking on by default; reveal permission-gated and logged
- [ ] No plaintext Aadhaar anywhere; ideally, no Aadhaar at all
- [ ] Encryption at rest (RDS + S3, customer-managed KMS) and in transit verified
- [ ] Secrets in Secrets Manager; `gitleaks` clean
- [ ] RDS in an isolated subnet, no public endpoint
- [ ] WAF configured
- [ ] Audit logging live; export alerting tested with a real export
- [ ] Retention jobs scheduled and dry-run
- [ ] DSR workflow tested end to end with a real request
- [ ] Incident runbook written, contacts current, **drilled once**
- [ ] Processor agreements signed
- [ ] Sentry PII scrubbing verified
- [ ] Staging confirmed free of production PII
- [ ] Penetration test completed and findings closed
- [ ] Backup restore tested
