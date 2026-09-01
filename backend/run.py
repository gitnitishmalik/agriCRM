"""
Start the API.

    python -m backend.run              # http://127.0.0.1:8001, with reload
    python -m backend.run --port 9000
    python -m backend.run --no-reload

🔴 Why this exists rather than plain `uvicorn backend.main:app --reload`.

On Windows, uvicorn creates the event loop and *then* imports the app module
named on its command line. psycopg refuses to run async on Windows' default
ProactorEventLoop, so by the time `api/__init__.py` sets the policy the loop
already exists and every database call fails with:

    Psycopg cannot use the 'ProactorEventLoop' to run in async mode

The service starts, `/healthz` returns 200, and `/readyz` — the first thing to
touch the database — returns a 500. A health check that passes while the
database is unreachable is the worst possible shape for that failure, which is
why `/readyz` exists separately and why this launcher does.

Setting the policy here, before `uvicorn.run`, is the fix. On Linux and macOS
this module changes nothing at all — the default loop there is fine — so
production is unaffected either way.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 🔴 Runnable from either directory, like `main.py`.
#
#   python -m backend.run     from the repository root
#   python run.py             from inside backend/
#
# The second is what you type when you are already in `backend/`, and without
# this it fails twice over: `python -m backend.run` cannot even find the module
# from in here, and once uvicorn does start it imports `backend.main`, which
# needs the *parent* of `backend/` on the path.
#
# Inserting rather than appending, because a `backend` directory on the path
# ahead of ours would otherwise win.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AgriCRM API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Serve without the reloader. Use this for anything but development.",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        # The reloader spawns a child process, and the child has to set the
        # policy for itself — `api/__init__.py` does that, and it works there
        # because the child imports the app before serving rather than after.
        log_level="info",
    )


if __name__ == "__main__":
    main()
