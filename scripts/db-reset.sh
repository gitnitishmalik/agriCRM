#!/usr/bin/env bash
# Drop and recreate every schema, then re-apply schema.sql + seed_reference.sql.
#
# Dev only. There is no equivalent for staging or production — those change
# through Django migrations, reviewed like code (Doc 02 §6 rule 1).
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

if [ "${AGRICRM_ENV:-dev}" != "dev" ]; then
  echo "REFUSING: db-reset.sh is dev-only (AGRICRM_ENV=${AGRICRM_ENV})" >&2
  exit 1
fi

sql_describe_target

# 🔴 This drops data. On a hosted database that is not a container someone can
# recreate in ten seconds, so say what is about to happen and let a stray
# Enter be harmless. --yes skips it for CI and scripted use.
if [ "${1:-}" != "--yes" ] && [ -t 0 ]; then
  read -r -p "Drop every schema on the database above? [y/N] " reply
  case "$reply" in
    [yY]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

echo "==> Dropping schemas"
sql_stdin <<'SQL'
DROP SCHEMA IF EXISTS audit, dq, crm, comm, core, ref CASCADE;
SQL

"$(dirname "$0")/db-apply.sh"
