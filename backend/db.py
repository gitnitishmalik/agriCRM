"""
The database session.

🔴 The schema is owned by `agri-crm-docs/sql/schema.sql`, exactly as it was
under Django. SQLAlchemy maps it; it does not define it and never creates it.
`Base.metadata.create_all()` is not called anywhere in this package and must
not be — the DDL carries partitioning, generated columns and triggers no ORM
can express, and those *are* the compliance controls (CLAUDE.md).

That constraint is what makes this migration tractable at all. Both services
map the same tables from the same file, so they cannot drift apart in the
window where both are running.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

logger = logging.getLogger(__name__)


def _connect_args() -> dict:
    """
    Driver options asyncpg does not take from the URL.

    TLS is the whole list. asyncpg rejects libpq's `sslmode=require` as an
    unknown parameter, so `config.sqlalchemy_url` strips the query string and
    the requirement is re-expressed here.

    `ssl="require"` encrypts without verifying the certificate chain, which is
    what a managed provider's rotating certificates need and what libpq's own
    `sslmode=require` means. It is not `verify-full`; upgrading to that needs
    the provider's CA bundle on disk, and pretending otherwise by name would
    be worse than being explicit about the level in use.
    """
    if not settings.requires_tls:
        return {}
    return {"ssl": "require"}


engine = create_async_engine(
    settings.sqlalchemy_url,
    echo=False,
    pool_pre_ping=True,  # a hosted database drops idle connections
    pool_size=5,
    max_overflow=10,
    connect_args=_connect_args(),
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base. Mapping only — never a source of DDL."""


#: Attempts at *opening* a connection, including the first.
#:
#: 🔴 Only opening. The Django service carries the same guard in
#: `config/dbbackend/`, written after the ISP's resolver was measured refusing
#: roughly one lookup in five for the database's hostname. The same network
#: reaches this service, and without this the flakiness shows up as tests that
#: pass six runs and fail the seventh.
CONNECT_ATTEMPTS = 3
CONNECT_BACKOFF = (0.4, 1.0)

#: Substrings that mark a failure as worth retrying. Matched on the message
#: because the driver raises the same exception class for "cannot reach the
#: server" and "your password is wrong", and only one of those is transient.
TRANSIENT = (
    "getaddrinfo failed",
    "could not translate host name",
    "temporary failure in name resolution",
    "connection timeout expired",
    "connection was closed",
    "server closed the connection unexpectedly",
    "connection reset by peer",
    "network is unreachable",
    "no route to host",
    "connection refused",
    "cannot connect now",
)


def _is_transient(error: BaseException) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in TRANSIENT)


async def _open(session: AsyncSession) -> None:
    """
    Establish the connection, retrying a transient network failure.

    🔴 Called before the route runs a single statement, so a retry here can
    only ever re-run the act of opening a socket. It cannot replay a query,
    and it must never be moved to wrap one — a retried INSERT would be a
    correctness bug wearing a reliability costume.

    A wrong password, a missing database or a refused permission are real
    answers from a server that was reached; those raise on the first attempt.
    """
    import asyncio

    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            await session.connection()
            return
        except Exception as error:
            if not _is_transient(error) or attempt == CONNECT_ATTEMPTS:
                raise
            delay = CONNECT_BACKOFF[min(attempt - 1, len(CONNECT_BACKOFF) - 1)]
            logger.warning(
                "Database connection attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                CONNECT_ATTEMPTS,
                error,
                delay,
            )
            await session.rollback()
            await asyncio.sleep(delay)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """
    One session per request, committed on success and rolled back on error.

    A dependency rather than a context manager at each call site: a route that
    forgets to close a session leaks a connection, and against a hosted
    database that shows up as an unrelated timeout twenty minutes later.
    """
    async with SessionLocal() as session:
        try:
            await _open(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
