#!/usr/bin/env bash
# Shared helpers for the db scripts.
#
# Git Bash on Windows rewrites arguments that look like POSIX paths into
# Windows paths before exec — so "/sql/schema.sql" (a path inside the
# container) becomes "C:/Users/.../sql/schema.sql" and psql cannot find it.
# MSYS_NO_PATHCONV=1 disables that rewriting. Harmless on Linux and macOS.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_DIR="$REPO_ROOT/agri-crm-docs/sql"

# ---------------------------------------------------------------------------
# Where the database is
#
# The environment wins, then .env, then the Docker default. Resolved in one
# place because a script that applies the schema to a different database than
# the one the application talks to is the worst outcome available here, and
# each script guessing separately is how that happens.
# ---------------------------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  DATABASE_URL="$(grep -m1 '^DATABASE_URL=' "$REPO_ROOT/.env" | cut -d= -f2- || true)"
fi
DATABASE_URL="${DATABASE_URL:-postgres://agricrm:agricrm_dev_only@localhost:5433/agricrm}"
export DATABASE_URL

# ---------------------------------------------------------------------------
# How to talk to it
#
#   psql    — on PATH. Faithful: handles \set and \echo natively.
#   docker  — no psql, and the target *is* the Docker database.
#   python  — no psql and a hosted database. scripts/pgrun.py, which needs
#             psycopg and therefore the project venv.
#
# 🔴 `docker compose exec db psql` can only ever reach the Docker database, so
# it is chosen by what DATABASE_URL points at and never as a fallback. A run
# that quietly applied the schema to a local container while the application
# read a hosted one would look like it worked and leave two databases
# disagreeing — which is far more expensive than an error.
#
# python is last because `make bootstrap` applies the schema before it creates
# the venv: on a first run, docker is the only path that exists.
# ---------------------------------------------------------------------------
_is_docker_target() {
  case "$DATABASE_URL" in
    *@localhost:5433/*|*@127.0.0.1:5433/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Run from the repo root instead of passing -f. MSYS_NO_PATHCONV leaves the
# compose path as /c/Users/..., which Windows Docker reads as C:\c\Users\...
# and cannot open — so `-f` makes this branch fail on every Windows machine.
_docker_compose() {
  ( cd "$REPO_ROOT" && docker compose "$@" )
}

_docker_psql() {
  _docker_compose exec -T db psql -v ON_ERROR_STOP=1 -U agricrm -d agricrm --quiet "$@"
}

_docker_db_is_up() {
  [ -n "$(_docker_compose ps -q db 2>/dev/null)" ]
}

_venv_python() {
  for candidate in \
    "$REPO_ROOT/.venv/Scripts/python.exe" \
    "$REPO_ROOT/.venv/bin/python" \
    "$REPO_ROOT/venv/Scripts/python.exe" \
    "$REPO_ROOT/venv/bin/python"
  do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

if command -v psql >/dev/null 2>&1; then
  SQL_RUNNER=psql
elif _is_docker_target && _docker_db_is_up; then
  SQL_RUNNER=docker
elif PGRUN_PYTHON="$(_venv_python)"; then
  SQL_RUNNER=python
else
  echo "No way to reach the database." >&2
  echo "  DATABASE_URL: ${DATABASE_URL%%\?*}" >&2
  echo "  Install psql, start Docker, or create the venv (make bootstrap)." >&2
  exit 2
fi

# The conversion MSYS_NO_PATHCONV turned off above, done explicitly and only
# for arguments that reach a native Windows binary.
_winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi
}

sql_describe_target() {
  # Credentials never reach the terminal: everything between // and @ is cut.
  local safe="${DATABASE_URL%%\?*}"
  safe="$(printf '%s' "$safe" | sed -E 's#(//)[^@/]*@#\1#')"
  echo "==> $safe   (via $SQL_RUNNER)"
}

# Run one .sql file from agri-crm-docs/sql, named without a path.
sql_file() {
  local name="$1"
  case "$SQL_RUNNER" in
    psql)   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet -f "$SQL_DIR/$name" ;;
    docker) _docker_psql -f "/sql/$name" ;;
    python) "$PGRUN_PYTHON" "$(_winpath "$REPO_ROOT/scripts/pgrun.py")" \
              "$(_winpath "$SQL_DIR/$name")" ;;
  esac
}

# Run SQL read from stdin.
sql_stdin() {
  case "$SQL_RUNNER" in
    psql)   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet ;;
    docker) _docker_psql ;;
    python) "$PGRUN_PYTHON" "$(_winpath "$REPO_ROOT/scripts/pgrun.py")" - ;;
  esac
}

# Kept so anything still calling the old helper keeps working. New code should
# use sql_file / sql_stdin, which do not assume a container path.
psql_run() {
  if [ "${1:-}" = "-f" ]; then
    sql_file "$(basename "$2")"
  else
    sql_stdin
  fi
}
