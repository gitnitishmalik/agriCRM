# 11 · API Specification

Base URL: `https://api.agricrm.thetaanalytics.in/api/v1`
Auth: `Authorization: Bearer <JWT>`
Content type: `application/json`
Schema: OpenAPI 3.1, emitted by FastAPI from the Pydantic models, served at `/api/schema` with Swagger UI at `/api/docs`. The generated `openapi.yaml` is committed — regenerate with `make schema-doc`, and a diff in review is how contract drift becomes visible.

🔴 **Every route registers both the slashed and unslashed form.** FastAPI's default trailing-slash redirect answers 307 with an absolute URL, and browsers drop the `Authorization` header across origins — which presents in the log as an expiring session rather than as a redirect. `test_no_api_route_redirects_on_a_trailing_slash` walks every route.

---

## 1. Conventions

### Pagination

Cursor-based on all list endpoints (offset pagination breaks on large, actively-written tables):

```
GET /organisations/?limit=50&cursor=cD0yMDI2LTA4LTI0
```

```json
{
  "results": [...],
  "next": "cD0yMDI2LTA4LTI0VDEwOjAw",
  "previous": null,
  "count": 34812
}
```

`count` is an estimate above 10,000 rows (from `pg_class.reltuples`) — an exact count on a 10M-row partitioned table is a sequential scan.

### Filtering, ordering, sparse fields

```
GET /organisations/?type=fpo&state=9&district=9001
                   &member_count__gte=500&quality_tier=gold
                   &ordering=-updated_at
                   &fields=id,name,district,primary_contact
```

Supported suffixes: `__gte` `__lte` `__gt` `__lt` `__in` `__isnull` `__icontains`

### Errors

```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid input",
    "details": { "total_area_ha": ["Must be between 0.01 and 5000"] },
    "request_id": "req_01J8XYZ..."
  }
}
```

| Status | Code | Meaning |
|---|---|---|
| 400 | `validation_error` | Bad input |
| 401 | `unauthenticated` | Missing or expired token |
| 403 | `permission_denied` | Role or territory forbids it |
| 403 | `consent_required` | Attempted send to a non-consented recipient |
| 404 | `not_found` | Absent, or outside your territory (deliberately indistinguishable) |
| 409 | `conflict` | Duplicate, or optimistic-lock failure |
| 422 | `unprocessable` | Valid shape, invalid business state |
| 429 | `rate_limited` | With `Retry-After` |
| 500 | `internal_error` | With `request_id` |

🔴 404 rather than 403 for out-of-territory records is intentional: a 403 confirms the record exists, which leaks the existence of accounts in other territories.

### Idempotency

All POST endpoints accept `Idempotency-Key: <uuid>`. Keys are retained 24 hours; a repeat returns the original response.

### Rate limits

| Scope | Limit |
|---|---|
| Authenticated user | 1,000 req/hour |
| Bulk endpoints | 60 req/hour |
| Export | 20 req/hour |
| Mobile sync | 300 req/hour |

Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`

---

## 2. Auth

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login/` | → `{access, refresh, user}`. Access 15 min, refresh 7 days. |
| POST | `/auth/refresh/` | Rotating refresh tokens |
| POST | `/auth/logout/` | Blacklists the refresh token |
| POST | `/auth/mfa/verify/` | TOTP. 🔴 Required for admin, compliance and data-ops roles. |
| GET | `/auth/me/` | Current user, roles, permissions, territory |
| POST | `/auth/password/change/` | |

---

## 3. Organisations

| Method | Path | Notes |
|---|---|---|
| GET | `/organisations/` | List with filters |
| POST | `/organisations/` | Create. Returns **409** with candidate matches if a duplicate is likely — pass `force=true` to override (logged). |
| GET | `/organisations/{id}/` | Detail incl. type profile |
| PATCH | `/organisations/{id}/` | Partial update |
| DELETE | `/organisations/{id}/` | Soft delete |
| GET | `/organisations/{id}/people/` | Roles at this org |
| GET | `/organisations/{id}/members/` | Linked farmers |
| GET | `/organisations/{id}/activities/` | Timeline |
| GET | `/organisations/{id}/projects/` | |
| GET | `/organisations/{id}/opportunities/` | |
| GET | `/organisations/{id}/documents/` | |
| GET | `/organisations/{id}/quality/` | Provenance per field, contradictions, verification history |
| POST | `/organisations/{id}/verify/` | Record a verification event |
| POST | `/organisations/{id}/merge/` | `{merge_id, field_choices}` → merged record |
| POST | `/organisations/check-duplicates/` | Live duplicate check for the create form |
| POST | `/organisations/bulk-assign/` | `{ids[], owner_user_id}` |
| GET | `/organisations/export/` | 🔴 Requires `reason`; audited |

**Duplicate response on create:**

```json
{
  "error": {
    "code": "conflict",
    "message": "3 similar organisations found",
    "details": {
      "candidates": [
        { "id": "uuid", "name": "Bhainswal Kisan Producer Company Ltd",
          "district": "Muzaffarnagar", "similarity": 0.87,
          "matched_on": ["name", "district"] }
      ]
    }
  }
}
```

---

## 4. People

| Method | Path |
|---|---|
| GET / POST | `/people/` |
| GET / PATCH / DELETE | `/people/{id}/` |
| GET / POST | `/people/{id}/roles/` |
| PATCH | `/people/{id}/roles/{role_id}/` — end-date a role |
| GET / POST | `/people/{id}/contact-points/` |
| POST | `/people/{id}/contact-points/{cp_id}/verify/` |
| GET | `/people/{id}/consent/` — full consent history |
| GET | `/people/{id}/activities/` |

🔴 **Contact-point responses are masked by default**: `"+91 98XXX XX210"`. Unmasking requires `?reveal=true` plus the `view_full_contact` permission, and writes `audit.data_access_log`.

---

## 5. Farmers

| Method | Path | Notes |
|---|---|---|
| GET | `/farmers/` | 🔴 `state` is **required** — the partition key. Without it the request is rejected 400. |
| POST | `/farmers/` | |
| GET / PATCH / DELETE | `/farmers/{id}/` | |
| GET / POST | `/farmers/{id}/land-parcels/` | |
| GET / POST | `/farmers/{id}/crops/` | |
| GET / POST | `/farmers/{id}/organisations/` | FPO/mill links |
| GET | `/farmers/{id}/consent/` | |
| POST | `/farmers/{id}/consent/` | Record a consent event |
| GET | `/farmers/{id}/messages/` | Message history |
| GET | `/farmers/{id}/quality/` | |
| POST | `/farmers/bulk-import/` | → import batch (see §9) |
| GET | `/farmers/export/` | 🔴 reason required |
| GET | `/farmers/stats/` | Aggregates by state/district/tier/consent |

Requiring `state` on the list endpoint is the API-level enforcement of the partition-pruning rule from [Doc 04](./04-architecture.md) §6. It is mildly annoying and it prevents the single worst performance failure this system can have.

---

## 6. Projects

| Method | Path |
|---|---|
| GET / POST | `/projects/` |
| GET / PATCH / DELETE | `/projects/{id}/` |
| GET / POST / DELETE | `/projects/{id}/organisations/[{link_id}/]` |
| GET / POST / DELETE | `/projects/{id}/contacts/[{link_id}/]` |
| GET / POST | `/projects/{id}/sites/` |
| GET / POST / PATCH | `/projects/{id}/milestones/[{ms_id}/]` |
| GET / POST | `/projects/{id}/documents/` |
| GET | `/projects/{id}/activities/` |
| POST | `/projects/{id}/stage/` — `{stage, note}` |
| GET | `/projects/portfolio/` — dashboard aggregates |

---

## 7. Pipeline

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/leads/` | |
| GET / PATCH | `/leads/{id}/` | |
| POST | `/leads/{id}/convert/` | → creates org (if needed), person, opportunity |
| POST | `/leads/{id}/disqualify/` | `{reason}` |
| GET / POST | `/opportunities/` | |
| GET / PATCH / DELETE | `/opportunities/{id}/` | |
| POST | `/opportunities/{id}/stage/` | `{stage, note, loss_reason?}`. 🔴 422 if `stage=lost` without `loss_reason`. |
| GET | `/opportunities/{id}/history/` | Stage history |
| GET | `/opportunities/forecast/` | `?period=quarter` → committed / best_case / weighted |
| GET | `/opportunities/ageing/` | Stuck deals by stage |
| GET | `/opportunities/pipeline/` | Funnel aggregates |

---

## 8. Field operations

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/agents/` | |
| GET / PATCH | `/agents/{id}/` | |
| GET / POST | `/agents/{id}/territories/` | |
| GET / POST | `/agents/{id}/targets/` | |
| GET | `/agents/{id}/performance/` | Target vs. actual |
| GET | `/agents/{id}/day-plan/` | Prioritised list for today |
| GET / POST | `/visits/` | |
| GET | `/visits/{id}/` | |
| POST | `/sync/push/` | Batch offline writes — see below |
| GET | `/sync/pull/` | `?since=<cursor>` territory-scoped delta |

**`POST /sync/push/`**

```json
{ "visits":  [ { "client_uuid": "...", "agent_id": "...", "organisation_id": "...",
                 "visit_purpose": "follow_up", "outcome": "needs_followup",
                 "latitude": 29.4721, "longitude": 77.7284,
                 "visited_at": "2026-08-24T10:14:00+05:30" } ],
  "farmers": [ ... ],
  "consents":[ ... ] }
```

```json
{ "visits": [ { "client_uuid": "...", "status": "accepted", "server_id": "uuid" },
              { "client_uuid": "...", "status": "rejected",
                "error": "organisation_id not in your territory" } ],
  "farmers": [ ... ], "consents": [ ... ] }
```

🔴 Per-record accept/reject, never all-or-nothing. One bad record must not cost an agent a day's work.

---

## 9. Data quality & imports

| Method | Path | Notes |
|---|---|---|
| GET | `/sources/` | Approved-source registry |
| POST | `/imports/` | Upload → batch (multipart) |
| GET | `/imports/{id}/` | Status and counts |
| POST | `/imports/{id}/mapping/` | Column mapping |
| POST | `/imports/{id}/dry-run/` | Counts + 20-row preview, no writes |
| POST | `/imports/{id}/commit/` | 🔴 **422 unless `legal_basis_confirmed=true`** |
| POST | `/imports/{id}/rollback/` | Within 7 days |
| GET | `/imports/{id}/errors/` | `?format=xlsx` |
| GET | `/dedupe/candidates/` | Review queue |
| POST | `/dedupe/candidates/{id}/resolve/` | `{action: merge\|reject, field_choices}` |
| GET | `/contradictions/` | Open contradictions |
| POST | `/contradictions/{id}/resolve/` | `{chosen_value, note}` |
| GET | `/quality/dashboard/` | Tier distribution, completeness, source scorecard |
| GET | `/quality/decay-forecast/` | Records dropping a tier in 30/60/90 days |

---

## 10. Communications

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/templates/` | |
| GET / PATCH | `/templates/{id}/` | |
| POST | `/templates/{id}/submit/` | Submit to Meta for approval |
| GET / POST | `/campaigns/` | |
| GET / PATCH | `/campaigns/{id}/` | |
| POST | `/campaigns/{id}/preview/` | 🔴 Returns eligible count **and the exclusion breakdown** |
| POST | `/campaigns/{id}/approve/` | |
| POST | `/campaigns/{id}/launch/` | |
| POST | `/campaigns/{id}/pause/` · `/abort/` | |
| GET | `/campaigns/{id}/metrics/` | |
| POST | `/messages/send/` | Single transactional send |
| GET | `/messages/` | `?subject_id=&channel=` |
| GET / POST | `/consent/` | Query / record consent |
| GET / POST / DELETE | `/suppression/` | |
| POST | `/webhooks/whatsapp/` | 🔴 Signature-verified, responds <5s |
| POST | `/webhooks/ses/` | SNS bounce/complaint |

**`POST /campaigns/{id}/preview/`**

```json
{
  "matched": 12400,
  "eligible": 9180,
  "excluded": {
    "no_consent": 2100,
    "suppressed": 890,
    "delivery_failures": 230
  },
  "estimated_cost_inr": 1055.70,
  "by_district": [ { "district": "Muzaffarnagar", "eligible": 9180 } ]
}
```

---

## 11. Search, reporting, compliance

| Method | Path | Notes |
|---|---|---|
| GET | `/search/?q=` | Cross-entity. `?types=organisation,person,farmer` |
| GET | `/search/suggest/?q=` | Typeahead, ≤10 results |
| GET / POST | `/saved-views/` | Per user or shared |
| GET | `/dashboards/{name}/` | bd · agent · quality · portfolio · leadership |
| POST | `/exports/` | 🔴 `{entity, filters, format, reason}` → async job |
| GET | `/exports/{id}/` | Status + presigned download URL (expires 1 h) |
| GET / POST | `/dsr/` | Data-subject requests |
| POST | `/dsr/{id}/fulfil/` | Generates the response package |
| GET | `/audit/access-log/` | Compliance role only |
| GET | `/audit/changes/?entity_type=&entity_id=` | Field-level history |

---

## 12. Webhooks out (for integrations)

Let downstream systems subscribe:

| Event | Payload |
|---|---|
| `organisation.created` / `.updated` / `.merged` | Entity snapshot |
| `farmer.consent_changed` | Subject, channel, purpose, new status |
| `opportunity.stage_changed` | Opportunity, from, to |
| `project.stage_changed` | Project, from, to |
| `campaign.completed` | Campaign metrics |
| `quality.tier_changed` | Entity, from tier, to tier, reason |

Signed with HMAC-SHA256 over the raw body using a per-subscription secret; `X-AgriCRM-Signature` header; retries with exponential backoff for 24 hours.

---

## 13. Versioning

- URL-versioned: `/api/v1/`
- Additive changes ship inside v1
- Breaking changes get `/api/v2/` with v1 supported for **12 months**
- Deprecations announced via a `Sunset` header and in the OpenAPI description
- 🔴 The mobile app is the constraint: field agents update slowly, so **v1 must stay alive for the full deprecation window** regardless of how few web clients still use it
