# 04 · System Architecture

## 1. Context diagram

```
   ┌─────────────┐   ┌─────────────┐   ┌──────────────┐
   │  Web (React)│   │ Mobile (RN) │   │ Django Admin │
   │  BD, ops,   │   │ Field agents│   │  Data ops    │
   │  leadership │   │  offline    │   │              │
   └──────┬──────┘   └──────┬──────┘   └──────┬───────┘
          │ HTTPS/JWT       │ HTTPS/JWT       │ session
          └─────────────────┴─────────────────┘
                            │
                  ┌─────────▼──────────┐
                  │   CloudFront + ALB │
                  └─────────┬──────────┘
                            │
        ┌───────────────────▼────────────────────┐
        │          API service (Django/DRF)      │
        │  auth · RBAC · RLS · validation · CRUD │
        └───┬──────────┬───────────┬─────────────┘
            │          │           │
    ┌───────▼──┐  ┌────▼─────┐ ┌──▼──────────┐
    │ Postgres │  │  Redis   │ │     S3      │
    │ +PostGIS │  │ cache/   │ │ docs·imports│
    │ (Multi-AZ│  │ broker   │ │ exports     │
    │  + replica)│└────┬─────┘ └─────────────┘
    └──────────┘      │
                      │ Celery
     ┌────────────────┼───────────────────┬──────────────┐
     │                │                   │              │
┌────▼─────┐  ┌───────▼──────┐  ┌─────────▼──────┐ ┌────▼──────┐
│ worker   │  │ collector    │  │ messaging      │ │ beat      │
│ imports  │  │ public       │  │ WhatsApp/email │ │ scheduler │
│ exports  │  │ registries   │  │ send + retry   │ │           │
│ dedupe   │  │ (whitelisted)│  └───────┬────────┘ └───────────┘
│ scoring  │  └──────────────┘          │
└──────────┘                            │
                        ┌───────────────┴──────────────┐
                        ▼                              ▼
              ┌──────────────────┐          ┌────────────────┐
              │ Meta WhatsApp    │          │   Amazon SES   │
              │ Cloud API        │          │                │
              └────────┬─────────┘          └───────┬────────┘
                       │ webhooks                   │ SNS
                       └──────────┬─────────────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ webhook receiver    │
                       │ (delivery, replies, │
                       │  bounces, STOP)     │
                       └─────────────────────┘
```

## 2. Services

| Service | Responsibility | Scaling | Notes |
|---|---|---|---|
| **api** | Synchronous HTTP. Never does long work — anything over ~2s is queued. | 2–8 Fargate tasks, autoscale on CPU >65% and ALB request count | Gunicorn, 4 workers × 2 threads |
| **worker** | Imports, exports, dedupe scans, quality scoring, merges | 2–6 tasks, autoscale on queue depth | Separate queues: `default`, `import`, `heavy` |
| **collector** | Scheduled fetches from **approved** sources only | 1–2 tasks | Isolated so a scraper crash can't take down message delivery |
| **messaging** | WhatsApp/email dispatch with rate limiting and retries | 1–4 tasks | Dedicated queue; strict per-second throttle |
| **beat** | Celery Beat scheduler | **Exactly 1 task** | Two beat instances = every scheduled job runs twice |
| **webhook** | Receives Meta and SES/SNS callbacks | 2+ tasks | Must respond <5s; writes to a queue and returns 200 immediately |

**The webhook rule matters.** Meta retries on non-2xx and will disable your webhook after repeated failures. The receiver validates the signature, writes the raw payload to Redis, returns 200, and a worker processes it asynchronously. Never do database work inline in a webhook handler.

## 3. Request flow — a campaign send

```
1. Campaign Manager builds a segment in the UI
        ↓
2. POST /api/v1/campaigns/{id}/preview
        ↓
3. API resolves the segment against comm.v_messageable_farmer
   ← returns: 12,400 matched · 9,180 eligible · 3,220 excluded
                                                (2,100 no consent,
                                                   890 suppressed,
                                                   230 failed delivery ≥3)
        ↓   🔴 The exclusion breakdown is shown, always. It is the
            single most useful compliance habit you can build in.
4. Manager reviews cost estimate (9,180 × ₹0.115 = ₹1,056) and approves
        ↓
5. POST /api/v1/campaigns/{id}/launch  → enqueues a Celery chord
        ↓
6. messaging worker, per recipient:
      a. RE-CHECK consent + suppression at send time (state may have
         changed since preview — this check is not optional)
      b. render template variables
      c. respect quiet hours (no sends 21:00–08:00 IST)
      d. call Meta Cloud API with a per-second throttle
      e. INSERT comm.message (state='sent', provider_message_id)
      f. INSERT crm.activity onto the farmer's timeline
        ↓
7. Meta webhooks arrive → delivered / read / failed
        ↓
8. worker updates comm.message state, increments campaign counters,
   and on failure increments core.contact_point.delivery_failures
        ↓
9. Inbound "STOP" → comm.inbound_message → intent='optout'
   → INSERT comm.consent_event(status='opted_out')
   → INSERT comm.suppression
   → confirmation reply sent
   → (trigger already updated comm.consent_current)
```

Step 6a is the one people skip. Between preview and send — which may be hours apart for a scheduled campaign — someone can opt out. Re-checking at dispatch time is what keeps that from becoming a violation.

## 4. Request flow — offline field visit sync

```
Agent's phone (offline)
   → creates visit with client_uuid = <device-generated UUIDv4>
   → writes to local SQLite, marks dirty
   ...hours later, connectivity returns...
   → POST /api/v1/sync/push  { visits: [...], farmers: [...], photos: [...] }
        ↓
   API validates each record independently
        ↓
   INSERT ... ON CONFLICT (client_uuid) DO NOTHING
        ↓
   Response: per-record { client_uuid, status: accepted|rejected, server_id, error }
        ↓
   Phone marks accepted records clean, surfaces rejected ones to the agent
        ↓
   GET /api/v1/sync/pull?since=<cursor>&territory=<agent_territory>
        ↓
   Server returns changed records since cursor, ≤100KB per page,
   scoped to the agent's territory
```

**Design rules for sync:**
- Idempotency by `client_uuid`, enforced by a unique constraint, not by application logic
- Per-record accept/reject, never all-or-nothing — one bad record must not block a day's work
- Cursor is `updated_at` + `id` (a compound cursor; `updated_at` alone loses records with identical timestamps)
- Territory scoping happens server-side. Never trust a client-supplied territory filter.
- Photos upload separately via presigned S3 URLs, on Wi-Fi by default

## 5. Data ingestion architecture

```
┌──────────────────────────────────────────────────────────────┐
│ dq.source registry — the compliance gate                     │
│ Every collector's FIRST action: assert source.is_approved     │
│ A collector against an unapproved source MUST fail loudly.    │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌─────────────┬─────────────┬──────────────┬───────────────────┐
│ Registry    │ Open data   │ Partner      │ Field / inbound   │
│ collectors  │ API pulls   │ file drops   │ (mobile, web)     │
│ MCA·SFAC·   │ data.gov.in │ FPO/mill MoU │ with consent      │
│ ISMA·LGD    │ AGMARKNET   │ CSV/XLSX     │ captured at source│
└──────┬──────┴──────┬──────┴──────┬───────┴─────────┬─────────┘
       └─────────────┴─────────────┴─────────────────┘
                         ▼
        ┌────────────────────────────────┐
        │ RAW LANDING (S3 + jsonb)        │
        │ immutable; original payload kept│
        └────────────────┬───────────────┘
                         ▼
        ┌────────────────────────────────┐
        │ NORMALISE                       │
        │ phone→E.164 · names→title case  │
        │ area→hectares · geo→LGD codes   │
        │ dates→ISO · state/district match │
        └────────────────┬───────────────┘
                         ▼
        ┌────────────────────────────────┐
        │ VALIDATE                        │
        │ schema · ranges · referential   │
        │ rejects → dq.import_row_error   │
        └────────────────┬───────────────┘
                         ▼
        ┌────────────────────────────────┐
        │ MATCH (entity resolution)       │
        │ blocking → scoring → decision   │
        │ auto-merge >0.92 · review 0.75– │
        │ 0.92 · new <0.75                │
        └────────────────┬───────────────┘
                         ▼
        ┌────────────────────────────────┐
        │ UPSERT + PROVENANCE             │
        │ writes dq.field_provenance      │
        │ detects dq.contradiction        │
        └────────────────┬───────────────┘
                         ▼
        ┌────────────────────────────────┐
        │ SCORE                           │
        │ completeness · quality tier     │
        └────────────────────────────────┘
```

The **raw landing zone is not optional**. When you discover in month nine that your phone normaliser was dropping a leading zero for one state's format, the raw payloads are what let you reprocess instead of re-collect.

## 6. Scaling plan

| Trigger | Action |
|---|---|
| p95 API latency >400ms | Add API tasks; check for N+1 queries with `django-silk`; add `select_related`/`prefetch_related` |
| Dashboard queries slow the transactional workload | Route read-only analytical queries to the RDS read replica |
| Farmer table >5M rows | Confirm all queries filter on `state_id`; verify partition pruning in `EXPLAIN` |
| Search p95 >600ms | Introduce OpenSearch with CDC |
| `comm.message` >50M rows | Detach and archive partitions older than 12 months to S3/Parquet |
| Import throughput <5k rows/min | Switch to `COPY` into a staging table + set-based merge, instead of row-by-row ORM writes |
| Celery queue depth persistently >10k | Add workers; split heavy queues; check for a poison message in a retry loop |
| Connection count near RDS limit | PgBouncer in transaction pooling mode (note: disables session-level features — test prepared statements) |

**The single most important scaling rule:** every query against `core.farmer` must include `state_id` in its WHERE clause, or Postgres scans all 36 partitions. Enforce this with a Django manager that requires it, and assert partition pruning in tests.

## 7. Reliability

| Concern | Approach |
|---|---|
| Backups | RDS automated, 30-day retention, PITR. **Restore tested quarterly** — an untested backup is a hope, not a backup. |
| RPO / RTO | 15 min / 4 h |
| Multi-AZ | RDS Multi-AZ; Fargate tasks spread across 3 AZs |
| Idempotency | `Idempotency-Key` header on all POSTs; `client_uuid` on mobile writes |
| Retries | Celery: exponential backoff, max 5, then dead-letter queue with an alert |
| Rate limits | Per-user API throttling (DRF); per-second throttle on Meta and SES calls |
| Circuit breaker | If Meta returns 5xx or 429 for 60s, pause the messaging queue and alert — don't burn your quality rating retrying into a wall |
| Graceful degradation | If WhatsApp is down, campaigns queue rather than fail. If search is down, fall back to Postgres LIKE. |
| Poison messages | Max 5 retries then dead-letter; never infinite-retry |

## 8. Environments and data hygiene

| | dev | staging | production |
|---|---|---|---|
| Infra | Docker Compose, local | Single-AZ, scaled down | Multi-AZ |
| Data | Synthetic (factory_boy) | 🔴 Synthetic or irreversibly anonymised | Real |
| Messaging | Mocked | Meta test number, internal recipients only | Live |
| Access | All engineers | All engineers | Break-glass, MFA, logged |

🔴 **Never copy production PII into staging.** It is the most common way personal data leaks, and it is entirely avoidable: a `manage.py generate_synthetic_data` command that produces realistic Indian names, districts and landholdings costs two days and removes the temptation permanently.

## 9. Observability

**Structured JSON logs** with `request_id`, `user_id`, `entity_type`, `entity_id` on every line, correlating an API call through Celery to a provider webhook.

**Alerts that should page someone:**
- WhatsApp quality rating drops from Green
- Opt-out rate on a campaign exceeds 1%
- Message failure rate exceeds 5%
- Celery queue depth >10,000 for more than 10 minutes
- Any collector running against a source where `is_approved = false`
- Export of more than 10,000 PII records by any single user
- DSR request within 7 days of its due date
- Replica lag >60s

That third-from-last one is deliberate. Large PII exports are how databases walk out of the door, and an alert costs nothing.

**Dashboards:** API latency and error rate by endpoint · Celery queue depth and task duration · message delivery funnel by campaign · data-quality tier distribution over time · consented-farmer growth by state · pipeline value by stage.

## 10. Security architecture summary

Detail in [Doc 12](./12-security-rbac.md). The headlines:

- JWT access tokens (15 min) + refresh tokens (7 days, rotating); MFA mandatory for admin, compliance and data-ops roles
- **PostgreSQL Row-Level Security** as the enforcement backstop — territory scoping is applied by the database, so an application bug cannot leak cross-region data
- Field-level masking: phone numbers show as `+91 98XXX XX210` unless the role holds `view_full_contact`
- Encryption at rest (RDS + S3 with KMS), TLS 1.2+ everywhere
- All PII access logged to `audit.data_access_log`; exports require a typed reason
- VPC with private subnets for RDS and ElastiCache; no public database endpoint, ever
- Quarterly access review; offboarding revokes within 24 hours
