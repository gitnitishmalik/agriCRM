# AgriCRM

Farmer, FPO & Sugar Mill CRM for the Indian agriculture value chain.
Theta Analytics · Owner: Nitish Malik

**Status: Phase 0 (Foundation) complete. Phase 1 (Organisation Registry)
sprints 1–3 done, 4–6 outstanding.** The advanced billing module (I-7 → I-10)
is built and green.

The service is **FastAPI**. Django is gone — removed on 30 Aug 2026, not
merely stopped. See [Why FastAPI](#why-fastapi).

---

## Quick start

Requires **Docker**, **Python 3.13**, **Node 22+**, and **GNU make** run from
**Git Bash** on Windows (the Makefile needs a POSIX shell and the
`scripts/*.sh`; PowerShell and `cmd` will not do). If MinGW gave you
`mingw32-make` but no `make`, copy it: `cp /c/MinGW/bin/mingw32-make.exe
/c/MinGW/bin/make.exe`.

```bash
make bootstrap     # containers, .env, venv, deps, schema, smoke test, frontend
make dev           # API on :8001 and the UI on :5173, together
```

`make dev` runs both and stops both on one Ctrl-C. `make run` and
`make frontend` run them separately if you want two terminals.

Then:

| URL | What |
|---|---|
| http://localhost:5173 | The React UI |
| http://localhost:8001/admin | The data-operations console — server-rendered, read-heavy |
| http://localhost:8001/api/docs | Swagger UI (OpenAPI 3.1) |
| http://localhost:8001/api/v1/healthz | Liveness probe |

`make superuser` creates an admin login; `make seed-dev-users` creates one
account per role for development and refuses unless `DEBUG` is on. MFA is
mandatory for the privileged roles (Doc 12 §1), so expect a TOTP enrolment on
first sign-in.

### When something will not start

```bash
make doctor
```

Read-only. It checks the Python version, the packages, `.env`, whether the
database is reachable and holds all six schemas, whether the suite is pointed
at a local database, and whether Node, Docker, `psql` and WeasyPrint are
present — and prints the one command that fixes each thing it finds. It never
changes anything.

### Known gaps on a fresh install

Neither stops the app, and `make doctor` reports both:

- **No geography.** `ref.district`, `ref.block` and `ref.village` ship empty,
  so district and village lookups return nothing. The LGD load is Phase 1 work
  — see `agri-crm-docs/15-execution-plan.md`. 🔴 The codes are not invented in
  the meantime: a wrong LGD code silently corrupts every join made on it.
- **PDF rendering needs native libraries.** WeasyPrint imports and then raises
  `OSError` without the GTK runtime on Windows, or libpango elsewhere. Invoice
  HTML, the preview and the console are unaffected; only the PDF download
  needs it.

### Ports

Postgres is on **5433** and Redis on **6380**, not their defaults — a local
PostgreSQL install commonly occupies 5432 and silently wins the connection,
producing an auth failure that reads like a bad password.

---

## Common commands

```bash
make help          # list everything
make doctor        # check the environment, report what is missing
make dev           # API and UI together
make check         # lint + compliance guards + tests + smoke — what CI runs
make smoke         # the 20-assertion schema suite
make db-migrate    # apply the idempotent additions — safe on a live database
make db-reset      # 🔴 drop and re-apply the business schema (dev only)
make test          # runs backend/tests via backend/pytest.ini
make test-frontend # frontend typecheck + unit tests
make fmt           # auto-format
make schema-doc    # regenerate openapi.yaml
make collector ARGS="--dry-run --limit 5"
```

---

## Layout

```
agri-crm-docs/        The specification — 16 documents. Read 00 then 05.
  sql/schema.sql      Runnable DDL. The business schema is owned here, not by the ORM.
  sql/schema_invoice_advanced.sql   Idempotent. Applied by make db-migrate.
  sql/smoke_test.sql  20 behavioural assertions. Must stay green.
backend/              FastAPI + SQLAlchemy 2 (async) + Pydantic v2.
  main.py             App, middleware, startup checks
  routers/            One module per bounded context — the HTTP surface
  models/             SQLAlchemy models mapping the DDL. Never create_all().
  schemas/            Pydantic request/response contracts
  domain/             Compliance logic, PII masking, scoping and exports
  providers/          External services. Every one has a deterministic fake.
  admin/              The server-rendered data-operations console
  collectors/         One module per approved dq.source
  templates/          Jinja2 — invoices, console and emails
  tests/
  pytest.ini          Backend test configuration
frontend/             React 19 + TypeScript + Vite
infra/docker/         local container init
infra/terraform/      staging + production IaC (scaffolded, not applied)
scripts/              db apply/migrate/reset, smoke test, compliance guards
.github/workflows/    CI
```

---

## Why FastAPI

Django + DRF was the original choice, for two reasons: Django Admin as a
working data-ops console on day one, and trustworthy migrations. Both have
been answered.

- **The console.** `/admin` is server-rendered over the same domain layer as
  the API. It is read-heavy and write-narrow by design: it cannot issue an
  invoice, cancel one or record a payment, and a test reads the source to
  prove those code paths do not exist. Django Admin's default CRUD could not
  give that guarantee.
- **Migrations.** They never owned the business schema. `ref`, `core`, `comm`,
  `crm`, `dq` and `audit` are applied by DDL, because the partitioning,
  generated columns and triggers *are* the compliance controls. Django's
  migrations only ever covered tables that existed because Django did.

What FastAPI adds: native async against `asyncpg` for the import and export
paths, Pydantic v2 as one definition for both validation and the OpenAPI
contract the frontend generates its types from, and one auth stack instead of
simplejwt + django-otp + axes.

Full account, including the four bugs the port shipped that every test passed
and a browser caught, is in [`api/README.md`](api/README.md) and `CLAUDE.md`.

---

## How the schema works

**The business schema is owned by `agri-crm-docs/sql/schema.sql`, not by the
ORM.** 🔴 `Base.metadata.create_all()` is never called and must not be. The
`ref` / `core` / `comm` / `crm` / `dq` / `audit` schemas are applied by DDL and
*mapped* by SQLAlchemy models.

This is deliberate. The DDL carries partitioning, generated columns, triggers
and check constraints no ORM can express, and those constraints are the
compliance controls — an append-only consent ledger enforced by a trigger is
stronger than one enforced by application code that a future refactor can
bypass.

Schema changes are reviewed SQL, applied by `make db-migrate`. There is no
migration generator, and that is the point: a schema change is something a
person wrote and someone else read.

🔴 **Never hand-edit `schema.sql` without re-running `make smoke`.** It takes
under a second and catches trigger regressions unit tests miss.

---

## Before you write code

Read **`CLAUDE.md`** at the repo root. It carries the non-negotiable rules
(R1–R13), the schema decisions that must not be undone, and the conventions.
They are compliance controls, not preferences.

The three that catch people first:

- **R6** — outbound recipients come only from `comm.v_messageable_farmer`,
  never `core.farmer`. Enforced by `scripts/check-r6.sh` in CI.
- **R11** — staging and dev never contain production PII.
- **Every query against `core.farmer` must include `state_id`**, or Postgres
  scans all 37 partitions.

---

## CI

Six jobs, all required:

| Job | Enforces |
|---|---|
| `schema` | schema applies cleanly; all 20 smoke assertions pass |
| `lint` | ruff check + format on `api/` |
| `compliance` | R6 grep, gitleaks secret scan |
| `test` | the FastAPI suite against a real Postgres |
| `ai-safety` | the copilot trust boundary and the golden evaluation set |
| `frontend` | generated API types match the committed `openapi.yaml`, typecheck, build |

`ai-safety` is a separate job on purpose. When it goes red the failure names
itself — something in the trust boundary regressed, which is a different
conversation from a broken query. It runs against deterministic fakes, so it
needs no API key and cannot be skipped for want of credentials.

---

## Documentation

Start with [`agri-crm-docs/00-executive-summary.md`](agri-crm-docs/00-executive-summary.md).

🔴 Read [`agri-crm-docs/05-data-sourcing-and-legal.md`](agri-crm-docs/05-data-sourcing-and-legal.md)
before writing any collector. The phase-by-phase task list, preconditions and
exit gates are in [`agri-crm-docs/15-execution-plan.md`](agri-crm-docs/15-execution-plan.md).
The billing module's spec is [`INVOICE.md`](INVOICE.md) §12–13, and its build
notes are in [`api/README.md`](api/README.md).
