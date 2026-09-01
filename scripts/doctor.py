"""
Check the environment and say what is missing.

🔴 Why this exists. Every failure this script reports was hit for real, and
each one surfaced as something that did not name its own cause: a venv on the
wrong Python that installs cleanly and fails at import; a missing `psql` that
turns into "psycopg is not installed" three scripts later; a `TEST_DATABASE_URL`
set in `.env` where the suite could not see it, so the tests silently ran
against the hosted database and took minutes; WeasyPrint importing and then
raising `OSError` from a native library that is not there.

Read-only. It never fixes anything — it prints what is wrong and the one
command that addresses it, because a doctor that repairs things silently is a
doctor you stop reading.

    make doctor
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]

OK = "  ok   "
WARN = "  warn "
FAIL = "  FAIL "

problems: list[str] = []
warnings: list[str] = []


def report(status: str, label: str, detail: str = "", fix: str = "") -> None:
    print(f"{status} {label}" + (f" - {detail}" if detail else ""))
    if status == FAIL:
        problems.append(f"{label}: {fix or detail}")
    elif status == WARN:
        warnings.append(f"{label}: {fix or detail}")


def env_value(name: str) -> str | None:
    """Read from the process environment, then `.env`.

    🔴 Both, in that order. pydantic-settings loads `.env` onto the settings
    object and never into `os.environ`, so a value a developer put in `.env` is
    invisible to anything checking the environment alone — which is exactly how
    the test suite ended up pointed at the hosted database.
    """
    if os.environ.get(name):
        return os.environ[name]
    dotenv = REPO / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip() or None
    return None


def reachable(url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    try:
        with socket.create_connection(
            (parsed.hostname, parsed.port or 5432), timeout=4
        ):
            return True
    except OSError:
        return False


def main() -> int:
    print(f"\nAgriCRM environment check\n{'-' * 60}")

    # --- Python ------------------------------------------------------------
    version = sys.version_info
    if version >= (3, 13):
        report(OK, "Python", f"{version.major}.{version.minor}.{version.micro}")
    else:
        report(
            FAIL,
            "Python",
            f"{version.major}.{version.minor} - the project requires 3.13",
            'rebuild the venv: make bootstrap PY_BOOTSTRAP="py -3.13"',
        )

    # --- Dependencies ------------------------------------------------------
    missing = []
    for module in (
        "fastapi",
        "sqlalchemy",
        "asyncpg",
        "pydantic",
        "jose",
        "pyotp",
        "jinja2",
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        report(
            FAIL,
            "Backend packages",
            f"missing {', '.join(missing)}",
            "pip install -r backend/requirements.txt -r backend/requirements-dev.txt",
        )
    else:
        report(OK, "Backend packages", "all core imports resolve")

    # psycopg is tooling-only, and its absence breaks the scripts rather than
    # the service — which is why it gets its own line instead of the list above.
    try:
        import psycopg

        report(OK, "psycopg (scripts)", "schema and smoke-test scripts can run")
    except ImportError:
        report(
            WARN,
            "psycopg (scripts)",
            "absent - needed by scripts/pgrun.py when psql is not on PATH",
            "pip install -r backend/requirements-dev.txt",
        )

    # --- .env --------------------------------------------------------------
    if (REPO / ".env").exists():
        report(OK, ".env", "present")
    else:
        report(FAIL, ".env", "missing", "cp .env.example .env")

    # --- Database ----------------------------------------------------------
    database_url = env_value("DATABASE_URL")
    if not database_url:
        report(FAIL, "DATABASE_URL", "not set", "set it in .env")
    else:
        host = urlparse(database_url).hostname or "?"
        if reachable(database_url):
            report(OK, "Database", f"reachable at {host}")
        else:
            report(
                FAIL,
                "Database",
                f"cannot connect to {host}",
                "make up  (starts postgres on 5433)",
            )

    # 🔴 The check that would have saved a day. Without TEST_DATABASE_URL the
    # suite runs against DATABASE_URL — which is usually the shared or hosted
    # database, so tests are slow and write through every model into rows
    # somebody else is reading.
    test_url = env_value("TEST_DATABASE_URL")
    if test_url:
        report(OK, "TEST_DATABASE_URL", f"tests use {urlparse(test_url).hostname}")
    elif database_url and urlparse(database_url).hostname not in (
        "localhost",
        "127.0.0.1",
    ):
        report(
            WARN,
            "TEST_DATABASE_URL",
            "unset, and DATABASE_URL is not local - the suite will run against it",
            "set TEST_DATABASE_URL in .env to the local database",
        )
    else:
        report(
            OK, "TEST_DATABASE_URL", "unset; DATABASE_URL is local, so tests stay local"
        )

    # --- Schema ------------------------------------------------------------
    if database_url and reachable(database_url):
        try:
            import psycopg

            with psycopg.connect(database_url, connect_timeout=6) as connection:
                found = {
                    row[0]
                    for row in connection.execute(
                        "select nspname from pg_namespace "
                        "where nspname in ('ref','core','comm','crm','dq','audit')"
                    )
                }
                expected = {"ref", "core", "comm", "crm", "dq", "audit"}
                if expected <= found:
                    report(OK, "Schema", "all six business schemas applied")
                else:
                    report(
                        FAIL,
                        "Schema",
                        f"missing {', '.join(sorted(expected - found))}",
                        "make db-apply",
                    )

                districts = connection.execute(
                    "select count(*) from ref.district"
                ).fetchone()
                if districts and districts[0]:
                    report(OK, "Geography", f"{districts[0]:,} districts loaded")
                else:
                    report(
                        WARN,
                        "Geography",
                        "no districts - village and district lookups return nothing",
                        "the LGD load is Phase 1 work; see agri-crm-docs/15-execution-plan.md",
                    )
        except ImportError:
            report(WARN, "Schema", "not checked - psycopg absent")
        except Exception as error:  # noqa: BLE001 - reporting, not handling
            report(WARN, "Schema", f"not checked - {type(error).__name__}")

    # --- Optional tooling --------------------------------------------------
    for tool, why in (
        ("docker", "runs postgres and redis"),
        ("npm", "builds and serves the frontend"),
    ):
        if shutil.which(tool):
            report(OK, tool, why)
        else:
            report(FAIL, tool, f"not on PATH - {why}", f"install {tool}")

    if shutil.which("psql"):
        report(OK, "psql", "schema scripts use it directly")
    else:
        report(WARN, "psql", "not on PATH - scripts fall back to scripts/pgrun.py")

    # 🔴 WeasyPrint imports fine and raises OSError from its native libraries,
    # so "is it installed" is the wrong question. Importing is the only check
    # that matches how it actually fails.
    # In a subprocess, because a failed import writes a multi-line banner
    # straight to the OS stderr handle that `redirect_stderr` cannot intercept,
    # and it buries the one line of this report that matters.
    weasyprint_ok = (
        subprocess.run(
            [sys.executable, "-c", "import weasyprint"],
            capture_output=True,
            timeout=60,
            check=False,
        ).returncode
        == 0
    )
    if weasyprint_ok:
        report(OK, "WeasyPrint", "PDF rendering available")
    else:
        report(
            WARN,
            "WeasyPrint",
            "unavailable - invoice HTML works, PDF download does not",
            "install the GTK runtime (Windows) or libpango (Linux/macOS)",
        )

    node_modules = REPO / "frontend" / "node_modules"
    if node_modules.is_dir():
        report(OK, "Frontend deps", "node_modules present")
    else:
        report(WARN, "Frontend deps", "not installed", "make frontend-install")

    # --- Ports -------------------------------------------------------------
    for port, what in ((8001, "API"), (5173, "Vite")):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                report(OK, f"Port {port}", f"{what} already running")
        except OSError:
            report(OK, f"Port {port}", f"free - {what} can start")

    # --- Verdict -----------------------------------------------------------
    print("-" * 60)
    if problems:
        print(f"\n{len(problems)} problem(s) to fix:\n")
        for item in problems:
            print(f"  - {item}")
    if warnings:
        print(f"\n{len(warnings)} warning(s), none of which stop the app:\n")
        for item in warnings:
            print(f"  - {item}")
    if not problems and not warnings:
        print("\nEverything checks out. 'make dev' runs the API and the UI together.")
    elif not problems:
        print("\nNothing blocking. 'make dev' runs the API and the UI together.")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
