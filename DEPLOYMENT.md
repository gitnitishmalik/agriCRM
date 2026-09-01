# AgriCRM FastAPI Deployment

This runbook deploys the CRM with **FastAPI only**. Django, Gunicorn, Celery,
`manage.py`, and Django ORM migrations are not part of production.

## Current data status

The configured Neon database has 8 users, 746 organisations, 4,923 provenance
rows, 3 invoices, 3 lines, 2 payments, and 0 farmers. The organisation scrape
worked. Zero farmers is expected: SFAC is an organisation registry and the
collector excludes CEO contacts and farmer personal data.

## Phase 1 — mandatory backup gate

Do not apply SQL to Neon until a full backup and restore list are verified. A
dump contains personal and financial data; use only an approved encrypted
location.

```bash
pg_dump "$DATABASE_URL" --format=custom --no-owner --no-acl --file=/approved/encrypted/location/agricrm-before-fastapi.dump
pg_restore --list /approved/encrypted/location/agricrm-before-fastapi.dump
```

Record timestamp, SHA-256, Neon branch/project, and approver. Never commit a
dump or connection string.

## Phase 2 — production environment

Set secrets in the host, not Git:

```text
DATABASE_URL=postgresql://...?...sslmode=require
API_SECRET_KEY=<existing signing key; rotate in a controlled logout window>
DEBUG=false
DEV_NO_AUTH=false
DEV_NO_MFA=false
CORS_ALLOWED_ORIGINS=https://<frontend-domain>
SCRAPFLY_API_KEY=<optional>
COLLECTOR_CONTACT_EMAIL=data@thetaanalytics.in
```

🔴 `API_SECRET_KEY` signs every JWT. The older name `DJANGO_SECRET_KEY` is
still read as a fallback, so an environment set up before the rename keeps
working — but set the new name and remove the old one in the same change,
because two names for one key is how an environment ends up holding two
different values. The service does not import Django either way.

Rotating this value invalidates every unexpired refresh token, which logs
every user out at once. Do it in a chosen window, not as a side effect.

## Phase 3 — reviewed Neon additions

Never run `schema.sql` against live Neon; it is for an empty database. After
the approved backup, apply only idempotent additions:

```bash
./scripts/db-migrate.sh
./scripts/smoke-test.sh
```

This applies `schema_invoice_advanced.sql` and `schema_identity.sql`. Existing
rows are not deleted, replaced, or re-imported.

## Phase 4 — release verification

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q -c backend/pytest.ini backend/tests
.venv/Scripts/python.exe -m ruff check backend
.venv/Scripts/python.exe -m ruff format --check backend
cd frontend
npm ci
npm run typecheck
npm test -- --run
npm run build
```

Use `.venv/bin/python` on Linux/macOS.

## Phase 5 — deploy FastAPI

`render.yaml` builds from the repository root and starts:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" --workers 2
```

Then verify:

```text
GET https://<api-domain>/api/v1/healthz/ -> 200
GET https://<api-domain>/api/v1/readyz/  -> 200, database:"ok"
GET https://<api-domain>/api/docs        -> FastAPI documentation
```

Do not migrate in the web startup command. Database changes are a separately
approved release step and must not race across workers.

## Phase 6 — deploy frontend

Set `VITE_API_URL=https://<api-domain>`, build `frontend/` with `npm ci && npm
run build`, and set the API CORS origin to the exact frontend URL. Never set
`VITE_NO_AUTH=1` in production.

Existing users, password hashes, MFA devices, refresh tokens, and lockouts are
used directly. To add an admin:

```bash
ADMIN_EMAIL=admin@example.org ADMIN_NAME="System Admin" make superuser
```

## Phase 7 — data loading

Dry-run the institutional collector first:

```bash
make collector ARGS="--dry-run --states Bihar --limit 5"
make collector ARGS="--states Bihar"
```

It refuses an unapproved source, enforces robots.txt and one request/second,
and uses Scrapfly only with ASP and JS rendering disabled.

Import farmers through `/api/v1/farmers/import-csv/`. The source must be
approved, `contains_pii=true`, and be a partner agreement, field collection,
inbound signup, Theta legacy dataset, or purchased/licensed dataset.

## Phase 8 — post-deploy checks

- Sign in as an ordinary user and an MFA-required admin.
- Confirm Organisations displays 746 records.
- Select a state on Farmers; it should honestly show empty until lawful import.
- Open all three invoices and verify totals/payment balances.
- Check data-quality sources and contradictions as data ops.
- Run only a five-record SFAC dry-run as the deployment check.
- Confirm logs expose no secrets, tokens, farmer data, or database URLs.

## Rollback

Redeploy the previous image. Do not delete rows or run `db-reset.sh`; additive
tables can remain. If integrity is damaged, stop writes, restore the approved
dump to a fresh Neon branch, verify counts and financial totals, then switch
`DATABASE_URL` in a controlled cutover. Never restore over the only live copy.
