#!/usr/bin/env bash
# Shared helpers for the db scripts.
#
# Git Bash on Windows rewrites arguments that look like POSIX paths into
# Windows paths before exec — so "/sql/schema.sql" (a path inside the
# container) becomes "C:/Users/.../sql/schema.sql" and psql cannot find it.
# MSYS_NO_PATHCONV=1 disables that rewriting. Harmless on Linux and macOS.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

psql_run() {
  docker compose exec -T db \
    psql -v ON_ERROR_STOP=1 -U agricrm -d agricrm --quiet "$@"
}
