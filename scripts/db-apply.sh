#!/usr/bin/env bash
# Apply schema.sql + seed_reference.sql to a running database.
# Not idempotent — schema.sql uses CREATE TYPE, which has no IF NOT EXISTS.
# Use scripts/db-reset.sh to start clean.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

echo "==> Applying schema.sql"
psql_run -f /sql/schema.sql

echo "==> Applying seed_reference.sql"
psql_run -f /sql/seed_reference.sql

echo "==> Schema applied."
