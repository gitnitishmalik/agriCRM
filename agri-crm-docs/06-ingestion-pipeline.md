# 06 · Data Ingestion Pipeline

## 1. Pipeline stages

Every record, from every source, passes through the same seven stages. No shortcuts, no "just this once" direct inserts.

```
LAND → NORMALISE → VALIDATE → MATCH → UPSERT → PROVENANCE → SCORE
```

### Stage 1 — LAND (immutable raw)

Write the source payload to S3 **unmodified** and record a pointer. Never transform before landing.

```
s3://agricrm-raw/{source_code}/{yyyy}/{mm}/{dd}/{batch_uuid}/{filename}
```

Why this matters: in month nine you will discover a normalisation bug — a phone prefix stripped, a district misspelled in a lookup, an area unit assumed wrong. With raw payloads you reprocess in an afternoon. Without them you re-collect, and for partner data you may not be able to.

Landing also writes a `dq.import_batch` row with `status='uploaded'`, `source_id`, row count and the uploading user.

### Stage 2 — NORMALISE

| Field type | Rule |
|---|---|
| **Phone** | Strip everything non-numeric → drop leading 0 → drop leading 91 if length 12 → validate 10 digits starting 6–9 → store as `+91XXXXXXXXXX`. Anything failing goes to `value_raw` with `verification='invalid'`. |
| **Email** | Lowercase, trim, RFC-validate, reject role addresses in personal fields (`info@`, `admin@` belong on the organisation, not the person) |
| **Name** | Trim, collapse whitespace, Title Case with an exception list (`Kumar`, `Devi`, `Singh` fine; do not title-case `MD`, `FPO`, `Ltd`). Preserve Devanagari input verbatim into `name_local`. |
| **Area** | 🔴 Convert everything to hectares. `1 acre = 0.404686 ha`, `1 bigha` **varies by state** — use a state-keyed conversion table, and if the state is unknown, reject rather than guess. Record the original unit and value in `extra`. |
| **Geography** | Match state → district → block → village against `ref.*` using exact match, then trigram similarity ≥0.55, then leave unmatched and flag for review. Never auto-accept below 0.55. |
| **Dates** | Parse Indian conventions (`DD/MM/YYYY`, `DD-MM-YY`) explicitly. 🔴 Never let a parser guess between `03/04/2026` as March or April — set `dayfirst=True` and test it. |
| **Money** | Strip `₹`, commas, "Lakh"/"Crore" suffixes → convert to a plain rupee `numeric` |
| **Booleans** | Map `Y/N`, `Yes/No`, `1/0`, `हाँ/नहीं`, `TRUE/FALSE` |
| **CIN** | Uppercase, strip spaces, validate 21-character format |

**The bigha problem is real.** A bigha is ~0.25 ha in West Bengal, ~0.625 ha in parts of UP, ~0.16 ha in Uttarakhand. Guessing produces landholding data that is silently wrong by a factor of four, which then corrupts every derived `farmer_class`, every segment and every project sizing. Reject rather than guess.

### Stage 3 — VALIDATE

Three levels, each producing a `dq.import_row_error` row rather than aborting the batch:

- **Structural** — required fields present, types parseable
- **Range** — `total_area_ha` between 0.01 and 5,000; `established_year` 1850–current; `avg_recovery_pct` 0–30; `crushing_capacity_tcd` 100–50,000; phone exactly 10 digits post-normalisation
- **Referential** — state/district/block/village resolve; crop codes exist; parent org exists

Errors are surfaced as a downloadable XLSX with the original row plus an `error_reason` column. Data ops fixes the source file and re-uploads. This loop is used constantly — make it good.

### Stage 4 — MATCH (entity resolution)

The heart of the pipeline. Detail in §2 below.

### Stage 5 — UPSERT

Field-level merge, not row replacement:

```
for each field:
    if incoming is empty                        → keep existing
    elif existing is empty                      → take incoming
    elif values equal                           → refresh verified_at only
    elif incoming.confidence > existing.confidence + 0.15
                                                → take incoming, archive old provenance
    else                                        → keep existing, INSERT dq.contradiction
```

🔴 **Never let a bulk import silently overwrite a human-verified value.** A field verified by an agent in the last 180 days has confidence 0.95; a scraped registry value has 0.60. The rule above means the registry cannot clobber the agent. This single rule is what keeps a data-ops team's work from being erased by the next import.

### Stage 6 — PROVENANCE

For every field written, insert `dq.field_provenance`: entity, field, value, source, source reference, confidence, `collected_at`. Mark the previous row `is_current = false` rather than deleting it.

**Baseline confidence by source kind:**

| Source kind | Confidence |
|---|---|
| `field_collection` (agent-verified, with GPS) | 0.95 |
| `partner_agreement` (MoU data, member-consented) | 0.90 |
| `public_registry` (MCA, LGD) | 0.85 |
| `inbound_signup` | 0.85 |
| `open_government_data` | 0.80 |
| `official_website` | 0.75 |
| `industry_directory` | 0.70 |
| `manual_entry` | 0.70 |
| `theta_analytics` (legacy, pending audit) | 0.60 |
| `purchased_licensed` | 0.50 |
| `inferred` | 0.40 |

Confidence decays with age — see [Doc 07](./07-data-quality-organic.md) §4.

### Stage 7 — SCORE

Recompute `completeness_score` and `quality_tier` for every touched record. Defined in [Doc 07](./07-data-quality-organic.md).

---

## 2. Entity resolution

The hardest problem in this system. "Ram Kumar, Muzaffarnagar" is not an identity — there may be four hundred of him.

### 2.1 Blocking (candidate generation)

Never compare all pairs. Generate candidates with cheap, indexed predicates:

**For organisations:**
- Same `cin` → immediate match, confidence 1.0
- Same district + trigram similarity(name) > 0.4
- Shared normalised phone number
- Same registration number + state

**For people:**
- Same `din` → immediate match, confidence 1.0
- Same normalised phone
- Same district + trigram(full_name) > 0.5

**For farmers:**
- Same normalised phone
- Same village + trigram(first_name) > 0.5
- Same `aadhaar_hash` (when present)
- Same village + same `father_or_spouse` + trigram(first_name) > 0.4

```sql
-- Organisation blocking query
SELECT o.id, o.name, o.district_id,
       similarity(o.name, :incoming_name) AS name_sim
FROM core.organisation o
WHERE o.district_id = :incoming_district
  AND o.type = :incoming_type
  AND o.name % :incoming_name          -- trigram operator, uses the GIN index
  AND NOT o.is_deleted
ORDER BY name_sim DESC
LIMIT 25;
```

### 2.2 Scoring

Weighted comparison across signals:

| Signal | Weight | Comparator |
|---|---|---|
| CIN / DIN exact | 1.00 | exact → decisive |
| Aadhaar hash exact | 1.00 | exact → decisive |
| Phone exact | 0.40 | exact on `value_normalised` |
| Name similarity | 0.25 | Jaro-Winkler + trigram, take the max |
| Father/spouse name similarity | 0.15 | Jaro-Winkler |
| Village exact | 0.12 | LGD code |
| District exact | 0.05 | LGD code |
| Land area within 10% | 0.03 | numeric |

**Thresholds:**

| Score | Action |
|---|---|
| ≥ 0.92 | Auto-merge, write `dq.merge_event` |
| 0.75 – 0.92 | Insert `dq.dedupe_candidate` for human review |
| < 0.75 | Treat as a new record |

Tune these against a hand-labelled set of 500 pairs before trusting the auto-merge band. Start conservative — set auto-merge at 0.96 for the first month and lower it once you've measured precision.

### 2.3 Indian-name matching notes

Generic string similarity underperforms here. Add these preprocessing steps:

- **Transliteration variants**: Kumar/Kumaar, Chaudhary/Chaudhri/Choudhary/Chowdhary, Singh/Sing, Yadav/Jadav, Devi/Devee. Maintain a variant dictionary and normalise before comparison.
- **Honorifics**: strip Shri/Sri/Smt/Mr/Mrs/Dr/Late from names before matching, but keep the original.
- **Order variance**: names arrive as "Ramesh Kumar Chaudhary", "Chaudhary Ramesh Kumar", "R K Chaudhary". Compare token *sets*, not ordered strings, and handle initials specially.
- **Devanagari ↔ Latin**: transliterate both to a common representation (ITRANS or ISO 15919) before comparing. `indic-transliteration` handles this.
- **Organisation suffixes**: strip Ltd/Limited/Pvt/Private/FPC/FPO/Producer Company/Co-op/Cooperative/Society/Sahkari before name comparison. "Bhainswal Kisan Producer Company Limited" and "Bhainsval Kisaan FPC" should match on "bhainswal kisan".

### 2.4 Merge mechanics

```
1. Choose the survivor: highest quality_tier, then highest completeness_score,
   then oldest created_at (the record with the most history attached)
2. Field-by-field, take the higher-confidence value
3. Re-point all children: contact_points, roles, land_parcels, farmer_crops,
   org_links, activities, opportunities, projects, messages
4. Union the aliases, tags and source references
5. Set loser.merged_into_id = survivor.id, loser.is_deleted = true
6. INSERT dq.merge_event with a full JSONB snapshot of the loser
7. Reversible for 30 days
```

🔴 Never hard-delete the loser. `dq.merge_event.merged_snapshot` is what makes a bad bulk merge recoverable.

---

## 3. Collectors

### 3.1 Base class contract

Every collector inherits from a base that enforces the compliance rules from [Doc 05](./05-data-sourcing-and-legal.md) §5:

```python
class BaseCollector:
    source_code: str
    rate_limit_rps: float = 1.0
    user_agent: str = "ThetaAnalytics-AgriCRM/1.0 (+data@thetaanalytics.in)"

    def run(self, **kwargs):
        source = Source.objects.get(code=self.source_code)
        if not source.is_approved:                                   # R1
            raise SourceNotApprovedError(
                f"{self.source_code} is not approved. Legal sign-off required "
                f"before this collector may run."
            )
        if not self.robots_allows(source.url):                       # R2
            raise RobotsDisallowedError(source.url)
        batch = ImportBatch.objects.create(source=source, ...)
        try:
            for payload in self.fetch():                             # rate-limited
                self.land(payload, batch)
            self.process(batch)
        except Exception:
            batch.status = "failed"; batch.save()
            raise
```

Non-negotiable behaviours: assert approval, respect `robots.txt`, identify yourself with a contact address, rate-limit to ≤1 rps, honour `Retry-After`, back off exponentially on 429/5xx, and **never** authenticate, solve a CAPTCHA, or evade a rate limit.

### 3.2 Collector inventory

| Collector | Source | Output | Cadence | Notes |
|---|---|---|---|---|
| `mca_master` | MCA21 bulk master data | FPO company records + directors | Quarterly | Bulk files, not per-company crawling. Join key: CIN. |
| `mca_enrich` | MCA company lookup | Enrich one org by CIN on demand | On demand | Triggered when a user opens an FPO with a CIN and no financials |
| `sfac_fpo` | SFAC state-wise PDF/XLSX lists | FPO name, state, district, promoting agency | Quarterly | PDF table extraction with `camelot`/`pdfplumber`; expect manual QA |
| `nabard_ncdc_fpo` | Implementing agency lists | FPO records with implementing agency | Quarterly | Formats vary; per-agency parsers |
| `isma_directory` | ISMA member directory | Private mills, capacity, group | Quarterly | |
| `nfcsf_directory` | NFCSF directory | Cooperative sugar factories | Quarterly | |
| `state_sugarfed` | State cane commissioner portals | Licensed mills, **command areas**, SAP | Annual | Per-state parsers. UP, MH, KA, TN, GJ first. Highest-value collector for your sugar positioning. |
| `lgd_sync` | Local Government Directory | state/district/block/village + codes | Quarterly | Run this **first** — everything else joins to it |
| `dfpd_sugar_stats` | Directorate of Sugar / DFPD | Mill-wise cane crushed, sugar produced, recovery | Monthly in season | Writes `core.org_annual_metric` |
| `agmarknet` | AGMARKNET | Daily mandi prices and arrivals | Daily | Non-personal; feeds market intelligence |
| `ogd_datasets` | data.gov.in API | Configurable agri datasets | Weekly | Generic collector driven by a dataset-id config table |

### 3.3 Partner file ingestion

Partner data (FPO member lists, mill grower registers) arrives as XLSX/CSV with wildly inconsistent columns. The flow:

1. Upload → land raw
2. **Column mapping UI** — user maps source columns to CRM fields; the mapping is saved to `dq.import_batch.mapping` and offered as a default next time from the same partner
3. **Dry run** — full pipeline against a transaction that rolls back; shows create/update/skip/error counts and a sample of 20 rows as they *would* be written
4. 🔴 **Legal basis confirmation** — the user must attach or reference the consent artefact and tick `legal_basis_confirmed`. The commit button is disabled until they do.
5. **Commit** — batched in 5,000-row transactions
6. **Reversible for 7 days** via the batch's provenance records

### 3.4 Scheduling

```
Daily   02:00 IST  agmarknet
Weekly  Sun 03:00  ogd_datasets
Monthly 1st 03:00  dfpd_sugar_stats (Nov–Apr, crushing season)
Quarterly          lgd_sync → mca_master → sfac_fpo → nabard_ncdc_fpo
                   → isma_directory → nfcsf_directory
                   (sequential; lgd_sync must complete first)
Annual  Jun        state_sugarfed
Nightly 01:00      dedupe scan on records touched in the last 24h
Nightly 04:00      quality tier + completeness rescore
Weekly  Mon 06:00  decay job — downgrade stale verifications
```

---

## 4. Import performance

For batches over 10,000 rows, ORM row-by-row is too slow. Use:

```
1. COPY the normalised CSV into a temporary staging table
2. Set-based UPDATE ... FROM staging for matches
3. INSERT ... SELECT ... WHERE NOT EXISTS for new rows
4. Bulk INSERT provenance rows
5. Single set-based rescore over the touched ids
```

Target: **≥5,000 rows/minute** end-to-end including matching. A one-million-row mill grower register should import in about three hours, not three days.

Run large imports on the `heavy` Celery queue with its own worker, so a bulk import cannot starve message delivery.

---

## 5. Error handling and observability

| Failure | Behaviour |
|---|---|
| Source unreachable | Retry 3× with exponential backoff, then alert; do not fail the whole schedule |
| Source layout changed (parse error) | Fail the batch, alert with a sample of the unparseable payload, keep the raw landing |
| Row-level validation failure | `dq.import_row_error`, continue the batch |
| Match ambiguity | `dq.dedupe_candidate`, continue |
| Field contradiction | `dq.contradiction`, keep existing value, continue |
| Batch partially committed then fails | Roll back the current 5,000-row transaction; earlier batches stand; the batch is marked `failed` with a resume point |

**Metrics to track per collector run:** rows fetched, rows landed, rows valid, rows matched, rows created, rows updated, contradictions raised, duration, bytes fetched. Plot them — a collector that suddenly returns 40% fewer rows has usually hit a silent layout change, and the row count is what tells you before the data quality does.
