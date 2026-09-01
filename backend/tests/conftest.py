"""
Test setup.

Runs against the real database and the real DDL, exactly as the Django suite
does. That is not laziness — the whole premise of this migration is that both
services map one schema owned by `sql/schema.sql`, and a suite against a
sqlite double would prove nothing about whether the mapping is right.

Every test that writes does so inside a transaction that is rolled back, so
the shared database is left as it was found.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# 🔴 The suite verifies that authentication works. It must do that whatever a
# developer has in their local `.env` — and both bypasses turn assertions into
# tests that assert nothing and pass. Claimed before the settings are read.
os.environ["DEV_NO_AUTH"] = "0"
os.environ["DEV_NO_MFA"] = "0"
os.environ["DEBUG"] = "False"

# Providers the suite exercises. 🔴 All of them are the deterministic fakes,
# and that is the point: the safety assertions — a duplicate webhook cannot
# create a second payment, an opt-out outranks a send, the copilot cannot issue
# — must run on every commit, cost nothing and be incapable of reaching a real
# customer. A test suite whose safety checks are skipped for want of a key
# looks like coverage and is not.
os.environ.setdefault("COPILOT_PROVIDER", "fake")
os.environ.setdefault("GSTIN_LOOKUP_PROVIDER", "fake")
os.environ.setdefault("EMAIL_PROVIDER", "fake")
os.environ.setdefault("WHATSAPP_PROVIDER", "fake")
os.environ.setdefault("PAYMENT_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("UPI_VPA", "theta@icicitest")
os.environ.setdefault("UPI_PAYEE_NAME", "Theta Foundation for Development")
# Local object storage under the repo's ignored var/ directory, never a bucket.
os.environ.setdefault("STORAGE_BACKEND", "local")


# 🔴 `TEST_DATABASE_URL` points the suite somewhere other than the application's
# database, and it exists because of a measured problem rather than a
# preference: the hosted database is in Singapore behind a resolver that refuses
# roughly one lookup in five for its hostname, so a suite that runs against it
# takes four minutes and fails a test at random. `api/db.py` retries the *open*
# for the application; a test whose fixture cannot resolve the host has already
# errored before any of that runs.
#
# Point it at `docker compose`'s local Postgres — same `schema.sql`, same
# `schema_invoice_advanced.sql`, applied by `make db-apply`. When it is unset
# the suite uses DATABASE_URL exactly as before, so nothing changes for anyone
# who has not opted in.
# 🔴 Read `.env` here, not just the process environment.
#
# Every other setting in this project lives in `.env`, and pydantic-settings
# loads that file onto the `settings` object — it never puts it in
# `os.environ`. So `TEST_DATABASE_URL` written in `.env`, which is exactly
# where a developer puts it, was invisible to the check below and the suite
# went on running against whatever `DATABASE_URL` pointed at: the hosted
# database, with real rows in it. The symptom was a slow suite and
# `test_organisations_can_be_filtered_by_source` failing because the console
# listed 746 organisations that the test's own transaction knew nothing about.
#
# An exported variable still wins, so CI — which exports it — is unaffected.
def _test_database_url() -> str | None:
    from_environment = os.environ.get("TEST_DATABASE_URL")
    if from_environment:
        return from_environment

    env_file = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TEST_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("\"'") or None
    return None


_test_url = _test_database_url()
if _test_url:
    os.environ["TEST_DATABASE_URL"] = _test_url
    os.environ["DATABASE_URL"] = _test_url

from fastapi import Depends
from sqlalchemy.pool import NullPool

from backend.config import get_settings, settings
from backend.db import _connect_args, get_session
from backend.deps import require_verified_user
from backend.main import app
from backend.models.accounts import User
from backend.security import hash_password

get_settings.cache_clear()

PASSWORD = "correct-horse-battery-staple"


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """
    A session inside a transaction that is always rolled back.

    Nested in an outer transaction rather than relying on the test to clean up
    after itself: a test that fails half way through must not leave rows
    behind for the next one to trip over.

    🔴 Its own engine, with `NullPool`. An asyncpg connection belongs to the
    event loop that opened it, and pytest-asyncio gives every test a fresh
    loop — so a pooled connection handed to a second test is bound to a loop
    that has already closed. The symptom is the confusing kind: each test
    passes alone and the suite fails, with the traceback pointing at
    `pool_pre_ping` rather than at anything the test did.

    NullPool opens and closes per connection, which is slower and correct.
    The application engine keeps its pool; only the tests need this.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(
        settings.sqlalchemy_url,
        poolclass=NullPool,
        connect_args=_connect_args(),
    )

    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with maker() as db:
            yield db
        await transaction.rollback()

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncClient:
    """An HTTP client wired to the same rolled-back session the test holds."""

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def _make_user(session: AsyncSession, *, role: str, email: str) -> User:
    user = User(
        public_id=uuid.uuid4(),
        email=email,
        password=hash_password(PASSWORD),
        full_name=f"Test {role}",
        role=role,
        district_ids=[9001],
        is_active=True,
        is_staff=False,
        is_superuser=False,
        # Derived exactly as Django's save() derives it, so the row is the
        # same one either service would have written.
        mfa_enforced=role in {"data_ops", "campaign_manager", "compliance", "admin"},
        date_joined=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def agent(session: AsyncSession) -> User:
    """A role MFA does not apply to. Everything must keep working for them."""
    return await _make_user(
        session, role="field_agent", email=f"agent-{uuid.uuid4().hex[:8]}@test.in"
    )


@pytest_asyncio.fixture
async def biller(session: AsyncSession) -> User:
    """
    A role permitted to issue, cancel and record payments.

    🔴 Deliberately not `agent`. Issuing allocates a permanent number and
    freezes a statutory document, and `domain.scoping.BILLING_ISSUE` excludes
    `field_agent` on purpose: an agent in the field raises the draft and
    somebody in the office turns it into a document. `project_manager` is not
    in `MFA_REQUIRED_ROLES`, so these tests still exercise the ordinary
    single-factor path.
    """
    return await _make_user(
        session, role="project_manager", email=f"pm-{uuid.uuid4().hex[:8]}@test.in"
    )


@pytest_asyncio.fixture
async def data_ops(session: AsyncSession) -> User:
    """A role MFA is mandatory for (Doc 12 §1)."""
    return await _make_user(session, role="data_ops", email=f"ops-{uuid.uuid4().hex[:8]}@test.in")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def mfa_headers(client):
    """
    Sign a privileged user all the way in, second factor included.

    🔴 The tests for `data_ops` / `compliance` actions need this because those
    roles genuinely cannot reach a business route on a password alone — which
    is the control `test_mfa_boundary.py` exists to hold. Faking it by adding
    the claim by hand would test the route while stepping around the thing
    that makes the route safe.
    """

    async def _sign_in(user):
        import pyotp

        tokens = await client.post(
            "/api/v1/auth/login/", json={"email": user.email, "password": PASSWORD}
        )
        assert tokens.status_code == 200, tokens.text
        access = tokens.json()["access"]

        enrolled = await client.post(
            "/api/v1/auth/mfa/enrol/", headers={"Authorization": f"Bearer {access}"}
        )
        assert enrolled.status_code == 200, enrolled.text

        verified = await client.post(
            "/api/v1/auth/mfa/verify/",
            json={"token": pyotp.TOTP(enrolled.json()["secret"]).now()},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert verified.status_code == 200, verified.text
        return {"Authorization": f"Bearer {verified.json()['access']}"}

    return _sign_in


@pytest.fixture
def narrow_scope():
    """
    Run a block with the caller scoped to fewer billing entities than exist.

    🔴 Tenant isolation is genuinely hard to test in this deployment, because
    there is one customer and every user may act for both TFD and TEPL — so
    there is no natural pair of accounts on opposite sides of the boundary.

    The tempting shortcut is to reach into the database and move a row's
    `billing_entity_id` to a random uuid. That tests nothing useful: the
    foreign key refuses it, and where it does not, what is exercised is
    Postgres rather than `EntityScope.check`. Overriding the scope dependency
    puts the record on the far side of the boundary while leaving it a real,
    valid row — which is the situation the check exists for.
    """
    import contextlib

    from backend.domain.scoping import EntityScope, get_scope
    from backend.main import app

    @contextlib.contextmanager
    def _narrow(*, exclude=None, only=None):
        async def _override(caller=Depends(require_verified_user), session=Depends(get_session)):
            from sqlalchemy import select as _select

            from backend.models.billing import BillingEntity

            ids = list(await session.scalars(_select(BillingEntity.id)))
            if only is not None:
                ids = [i for i in ids if i in set(only)]
            if exclude is not None:
                excluded = {exclude} if not isinstance(exclude, (list, set)) else set(exclude)
                ids = [i for i in ids if i not in excluded]
            return EntityScope(caller, ids)

        previous = app.dependency_overrides.get(get_scope)
        app.dependency_overrides[get_scope] = _override
        try:
            yield
        finally:
            if previous is None:
                app.dependency_overrides.pop(get_scope, None)
            else:
                app.dependency_overrides[get_scope] = previous

    return _narrow
