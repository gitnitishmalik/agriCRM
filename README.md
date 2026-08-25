# AgriCRM

Farmer, FPO & Sugar Mill CRM for the Indian agriculture value chain.
Theta Analytics · Owner: Nitish Malik

**Status: Phase 0 (Foundation) — complete.** Phase 1 (Organisation Registry) is next.

---

## Quick start

Requires Docker, Python 3.12+, and Git Bash (on Windows).

```bash
make bootstrap     # containers, schema, venv, deps, migrations, smoke test
make run           # API on :8000, Django Admin at /admin/
```

Then:

| URL | What |
|---|---|
| http://localhost:8000/admin/ | Django Admin — the Phase 1 data-ops console |
| http://localhost:8000/api/docs/ | Swagger UI |
| http://localhost:8000/api/v1/healthz/ | Liveness probe |

`make superuser` creates an admin login.

### Ports

Postgres is on **5433** and Redis on **6380**, not their defaults — a local
PostgreSQL install commonly occupies 5432 and silently wins the connection.

---

## Common commands

```bash
make help          # list everything
make check         # lint + compliance guards + tests + smoke — what CI runs
make smoke         # the 15-assertion schema suite
make db-reset      # 🔴 drop and re-apply the business schema (dev only)
make test          # backend tests
make fmt           # auto-format
make schema-doc    # regenerate openapi.yaml
```

---

## Layout

```
agri-crm-docs/        The specification — 16 documents. Read 00 then 05.
  sql/schema.sql      Runnable DDL. The business schema is owned here, not by Django models.
  sql/smoke_test.sql  15 behavioural assertions. Must stay green.
backend/
  config/             settings (base/dev/staging/production), urls, celery, logging
  apps/               one Django app per bounded context — no apps/core, ever
  collectors/         one module per approved dq.source
  tests/
infra/docker/         local container init
infra/terraform/      staging + production IaC
scripts/              db apply/reset, smoke test, compliance guards
.github/workflows/    CI
```

---

## How the schema works

**The business schema is owned by `agri-crm-docs/sql/schema.sql`, not by Django
models.** Django manages only its own tables (auth, sessions, celery beat) via
migrations. The `ref` / `core` / `comm` / `crm` / `dq` / `audit` schemas are
applied by DDL and will be mapped with `managed = False` models in Phase 1.

This is deliberate. The DDL carries partitioning, generated columns, triggers
and check constraints that Django's ORM cannot express, and those constraints
are the compliance controls — an append-only consent ledger enforced by a
trigger is stronger than one enforced by application code that a future
refactor can bypass.

🔴 **Never hand-edit `schema.sql` without re-running `make smoke`.** It takes
under a second and catches trigger regressions unit tests miss.

---

## Before you write code

Read **`CLAUDE.md`** at the repo root. It carries the thirteen non-negotiable
rules (R1–R13), the schema decisions that must not be undone, and the
conventions. They are compliance controls, not preferences.

The three that catch people first:

- **R6** — outbound recipients come only from `comm.v_messageable_farmer`,
  never `core.farmer`. Enforced by `scripts/check-r6.sh` in CI.
- **R11** — staging and dev never contain production PII.
- **Every query against `core.farmer` must include `state_id`**, or Postgres
  scans all 37 partitions.

---

## CI

Four jobs, all required:

| Job | Enforces |
|---|---|
| `schema` | schema applies cleanly; all 15 smoke assertions pass |
| `lint` | ruff check + format |
| `compliance` | R6 grep, gitleaks secret scan |
| `test` | pytest, no missing migrations, ≥80% coverage on `communications` and `dataquality` |

---

## Documentation

Start with [`agri-crm-docs/00-executive-summary.md`](agri-crm-docs/00-executive-summary.md).

🔴 Read [`agri-crm-docs/05-data-sourcing-and-legal.md`](agri-crm-docs/05-data-sourcing-and-legal.md)
before writing any collector. The phase-by-phase task list, preconditions and
exit gates are in [`agri-crm-docs/15-execution-plan.md`](agri-crm-docs/15-execution-plan.md).
