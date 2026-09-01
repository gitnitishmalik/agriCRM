#!/usr/bin/env bash
# Apply the idempotent schema additions to a database that already exists.
#
# 🔴 The difference from db-apply.sh matters. That script runs schema.sql,
# which uses bare CREATE TYPE and expects an empty set of schemas — against a
# database holding real invoices it fails on the first type and leaves you
# guessing how far it got. This one runs only files written to be re-runnable,
# so it is safe against dev, staging and production alike, and safe to run
# twice when you are not sure whether the first run finished.
#
# Add a file here when it is idempotent. Never add schema.sql.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

FILES=(
  schema_invoice_advanced.sql
  schema_identity.sql
)

sql_describe_target

for file in "${FILES[@]}"; do
  echo "==> Applying $file"
  sql_file "$file"
done

echo "==> Migrations applied."
