"""
AgriCRM API (FastAPI).

🔴 The event-loop policy is set here, before anything imports asyncio's loop.

Windows defaults to `ProactorEventLoop`, and psycopg refuses to run in async
mode on it — "Psycopg cannot use the 'ProactorEventLoop' to run in async
mode". Every database call fails, so this is not a test-only nicety: it is the
difference between a service that starts and one that 500s on its first query.

Set at package import rather than in `main.py` because the policy has to be in
place before the loop is created, and uvicorn creates the loop before it
imports the app module named on its command line.

Why not switch to asyncpg, which has no such restriction: psycopg3 is what the
Django service already uses against this schema, and the schema is full of
custom enums, arrays and generated columns whose adaptation is already proven
with this driver. One driver across both services during a migration means one
set of behaviours to reason about — worth more than avoiding three lines.

The cost is that this loop cannot spawn subprocesses on Windows. Nothing in
this service does; Celery owns the work that would.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
