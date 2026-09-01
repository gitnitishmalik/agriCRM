#!/usr/bin/env bash
# Apply schema.sql + seed_reference.sql to a running database.
# Not idempotent — schema.sql uses CREATE TYPE, which has no IF NOT EXISTS.
# Use scripts/db-reset.sh to start clean.
#
# Works against Docker, a hosted database, or anything else DATABASE_URL names.
# scripts/_lib.sh picks the client; this file does not care which.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

sql_describe_target

echo "==> Applying schema.sql"
sql_file schema.sql

echo "==> Applying schema_invoice_advanced.sql"
sql_file schema_invoice_advanced.sql

echo "==> Applying schema_identity.sql"
sql_file schema_identity.sql

echo "==> Applying seed_reference.sql"
sql_file seed_reference.sql

echo "==> Schema applied."
