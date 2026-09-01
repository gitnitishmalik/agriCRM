"""
Run a .sql file against DATABASE_URL when there is no `psql` to run it with.

`scripts/_lib.sh` reaches for this only after native psql and the Docker
container have both been ruled out — which is the normal situation on a Windows
machine pointed at a hosted database (Neon, RDS). psql is not part of a Python
install and the Docker client can only ever talk to the Docker database, so
without this the schema, reset and smoke scripts simply do not work off-Docker.

Deliberately a thin stand-in, not a psql reimplementation:

  * ON_ERROR_STOP is psycopg's default behaviour — an exception aborts the
    transaction, and the exit code follows.
  * NOTICE output is forwarded to stdout, because that is where the smoke
    suite's PASS lines live and `smoke-test.sh` greps them.
  * psql meta-commands are dropped. `\\set` and `\\echo` are instructions to
    the client, not SQL, and the server rejects them as a syntax error.

Usage:  python scripts/pgrun.py <file.sql>     # or - for stdin
"""

from __future__ import annotations

import os
import pathlib
import sys


def main() -> int:
    try:
        import psycopg
    except ImportError:
        print(
            "pgrun: psycopg is not installed for this interpreter.\n"
            "       Use the project venv (.venv/Scripts/python.exe on Windows,\n"
            "       .venv/bin/python elsewhere), created by 'make bootstrap'.\n"
            "       If that venv exists, psycopg is missing from it: it is a\n"
            "       tooling dependency in backend/requirements-dev.txt, not part\n"
            "       of the runtime, which uses asyncpg.",
            file=sys.stderr,
        )
        return 2

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("pgrun: DATABASE_URL is not set.", file=sys.stderr)
        return 2

    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    source = sys.argv[1]
    raw = (
        sys.stdin.read()
        if source == "-"
        else pathlib.Path(source).read_text(encoding="utf-8")
    )

    body = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("\\")
    )

    try:
        with psycopg.connect(url, connect_timeout=30) as conn:
            # Notices arrive during execution; print them as they come so a
            # long file shows progress rather than going quiet.
            conn.add_notice_handler(
                lambda note: print(note.message_primary, flush=True)
            )
            # 🔴 Autocommit, matching psql's default. The smoke suite manages
            # its own transaction and rolls itself back; wrapping it in a
            # second one would leave that rollback with nothing to undo.
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(body)
    except psycopg.Error as error:
        print(f"pgrun: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
