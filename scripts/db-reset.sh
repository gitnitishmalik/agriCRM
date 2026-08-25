#!/usr/bin/env bash
# Drop and recreate every schema, then re-apply schema.sql + seed_reference.sql.
#
# Dev only. There is no equivalent for staging or production — those change
# through Django migrations, reviewed like code (Doc 02 §6 rule 1).
set -euo pipefail

if [ "${AGRICRM_ENV:-dev}" != "dev" ]; then
  echo "REFUSING: db-reset.sh is dev-only (AGRICRM_ENV=${AGRICRM_ENV})" >&2
  exit 1
fi

echo "==> Dropping schemas"
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U agricrm -d agricrm --quiet <<'SQL'
DROP SCHEMA IF EXISTS audit, dq, crm, comm, core, ref CASCADE;
SQL

. "$(dirname "$0")/_lib.sh"
"$(dirname "$0")/db-apply.sh"
