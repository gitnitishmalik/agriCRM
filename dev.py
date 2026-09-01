"""
Run the whole thing: API and UI, one command, one Ctrl-C.

    python dev.py

🔴 Why this sits at the repository root and takes no arguments.

Everything else here needs you to be somewhere specific. `make dev` needs GNU
make and a POSIX shell, which on Windows means Git Bash and not the PowerShell
terminal VS Code opens by default. `python -m backend.run` needs the repository
root. `uvicorn main:app` needs `backend/`, and then binds uvicorn's default
port 8000 while the dev proxy is looking at 8001 — so the UI loads, every
request fails, and the sign-in form is where you find out.

That is four ways to be holding it wrong, and the failures do not name
themselves. This script removes the question: it finds the repository from its
own location, so it does not care what your working directory is, and it starts
both halves on the ports they expect.

    API   http://127.0.0.1:8001   docs at /api/docs, console at /admin
    UI    http://localhost:5173

Ctrl-C once stops both. Output is prefixed so you can tell which half is
talking. Neither half is started until its prerequisites are actually present,
because a launcher that half-starts is worse than one that refuses.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent
FRONTEND = REPO / "frontend"

API_PORT = 8001
UI_PORT = 5173

# ANSI is on by default in Windows Terminal and VS Code, and harmless where it
# is not interpreted.
DIM = "\033[2m"
API_COLOUR = "\033[36m"
UI_COLOUR = "\033[35m"
WARN = "\033[33m"
RESET = "\033[0m"

processes: list[subprocess.Popen[str]] = []


def venv_python() -> str:
    """
    The interpreter from `.venv`, falling back to the one running this file.

    🔴 Not `sys.executable` unconditionally. Double-clicking this or running it
    with a system Python would otherwise serve from an environment that has
    none of the dependencies, and the failure lands as a `ModuleNotFoundError`
    on some import deep in the app rather than as "you are using the wrong
    Python".
    """
    for candidate in (
        REPO / ".venv" / "Scripts" / "python.exe",
        REPO / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def check() -> bool:
    """Refuse to half-start. Returns False when something is missing."""
    problems: list[str] = []

    python = venv_python()
    if python == sys.executable and not (REPO / ".venv").exists():
        problems.append(
            "No .venv in the repository. Create it:\n"
            "      python -m venv .venv\n"
            "      .venv\\Scripts\\python.exe -m pip install "
            "-r backend/requirements.txt -r backend/requirements-dev.txt"
        )

    if not (REPO / ".env").exists():
        problems.append("No .env. Copy the template:\n      copy .env.example .env")

    if not shutil.which("npm"):
        problems.append("npm is not on PATH. Install Node 22+ from https://nodejs.org")
    elif not (FRONTEND / "node_modules").is_dir():
        problems.append(
            "Frontend dependencies are not installed:\n      cd frontend && npm install"
        )

    if problems:
        print(f"{WARN}Cannot start:{RESET}\n")
        for item in problems:
            print(f"  - {item}\n")
        print("Run `python scripts/doctor.py` for a full check.\n")
        return False

    return True


def stream(process: subprocess.Popen[str], label: str, colour: str) -> None:
    """Forward one process's output, tagged so two streams stay readable."""
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(f"{colour}{label}{RESET} {line}")
        sys.stdout.flush()


def spawn(
    command: list[str], cwd: Path, label: str, colour: str
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        # A new process group on Windows, so Ctrl-C reaches this script rather
        # than being delivered straight to the children — which would let them
        # die in whatever order the console chose while this one kept running.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    processes.append(process)
    threading.Thread(target=stream, args=(process, label, colour), daemon=True).start()
    return process


def shutdown(*_: object) -> None:
    print(f"\n{DIM}Stopping...{RESET}")
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # 🔴 Vite's node child does not always go with a terminate on
            # Windows, and a survivor holds 5173 — so the next run fails to
            # bind with an error that never mentions this one.
            process.kill()
    sys.exit(0)


def main() -> int:
    if not check():
        return 1

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(
        f"\n  API   {API_COLOUR}http://127.0.0.1:{API_PORT}{RESET}   docs /api/docs | console /admin"
    )
    print(f"  UI    {UI_COLOUR}http://localhost:{UI_PORT}{RESET}")
    print(f"\n{DIM}  Ctrl-C stops both.{RESET}\n")

    spawn(
        [venv_python(), "-m", "backend.run", "--port", str(API_PORT)],
        cwd=REPO,
        label="api ",
        colour=API_COLOUR,
    )
    spawn(
        # `npm.cmd` on Windows: `npm` alone is a shell script the process
        # creation API cannot execute directly.
        ["npm.cmd" if os.name == "nt" else "npm", "run", "dev"],
        cwd=FRONTEND,
        label="ui  ",
        colour=UI_COLOUR,
    )

    # If either half exits on its own, stop the other — a lone survivor is the
    # confusing state this script exists to avoid.
    while True:
        for process in processes:
            if process.poll() is not None:
                shutdown()
        try:
            processes[0].wait(timeout=1)
        except subprocess.TimeoutExpired:
            continue


if __name__ == "__main__":
    raise SystemExit(main())
