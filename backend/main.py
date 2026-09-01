"""
AgriCRM API — FastAPI.

Runs alongside the Django service during the migration, against the same
database and the same DDL-owned schema. Both mint tokens the other accepts, so
traffic can be moved one route at a time rather than in a single cutover.

    uvicorn backend.main:app --reload

🔴 What the migration must not lose, and what this module is arranged to keep:

  * MFA enforced by default on business routes, with a declared opt-out list.
    The Django service shipped a phase where the permission class existed and
    was attached to nothing — `deps.PRE_MFA` and the router walk in
    `tests/test_mfa_boundary.py` are that lesson made structural.
  * The bypass switches refusing to work outside debug.
  * The business schema owned by `sql/schema.sql`, not by an ORM.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.admin.billing_views import router as admin_billing_router
from backend.admin.router import router as admin_router
from backend.admin.security import LoginRequired, redirect_to_login
from backend.config import settings
from backend.db import engine
from backend.routers import (
    auth,
    billing,
    billing_entities,
    billing_extract,
    billing_render,
    billing_write,
    compliance,
    copilot,
    dataquality,
    deliveries,
    farmers,
    geography,
    gstin,
    imports,
    inbound,
    invoice_checks,
    organisations,
    organisations_write,
    payment_webhooks,
    people,
    receivables,
)

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    🔴 Refuse to serve a misconfigured process.

    Django ran its system checks for `runserver` and `migrate` and not for
    gunicorn, so the guard that was meant to stop an open API ran everywhere
    except production. A lifespan hook has no such gap: uvicorn calls it before
    it accepts a connection, whatever invoked it.
    """
    problems: list[str] = []

    if settings.dev_no_auth and not settings.debug:
        problems.append(
            "DEV_NO_AUTH=1 with DEBUG off — the API would be open to anyone. "
            "🔴 R11 forbids production data behind it."
        )
    if settings.dev_no_mfa and not settings.debug:
        problems.append(
            "DEV_NO_MFA=1 with DEBUG off — privileged roles would sign in with a "
            "password alone. Doc 12 §1 makes MFA mandatory for data_ops, "
            "campaign_manager, compliance and admin."
        )
    if not settings.debug and settings.secret_key.startswith("insecure-"):
        problems.append(
            "API_SECRET_KEY is still the development default. Set it (or the "
            "legacy DJANGO_SECRET_KEY, still read) to a real secret."
        )

    if problems:
        raise RuntimeError("Refusing to start:\n  - " + "\n  - ".join(problems))

    if not settings.require_mfa:
        logger.warning(
            "DEV_NO_MFA is on — privileged roles sign in without a second factor. "
            "🔴 Never point this instance at production data (R11)."
        )
    if not settings.auth_enabled:
        logger.warning(
            "DEV_NO_AUTH is on — the API is open and MFA is not enforced. "
            "🔴 Never point this instance at production data (R11)."
        )

    yield

    await engine.dispose()


app = FastAPI(
    title="AgriCRM API",
    description="Farmer, FPO & Sugar Mill CRM for Theta Analytics",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/schema",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # the JWT travels in the Authorization header
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_payload(request: Request, code: str, message: str, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", None),
        }
    }


@app.middleware("http")
async def request_identity(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    detail = error.detail
    if isinstance(detail, dict):
        message = str(detail.get("message", "Request failed."))
        details = detail.get("details") or {
            key: value for key, value in detail.items() if key not in {"message", "details"}
        }
    else:
        message = str(detail)
        details = {}
    code = {
        400: "validation_error",
        401: "authentication_failed",
        403: "permission_denied",
        404: "not_found",
        409: "conflict",
    }.get(error.status_code, "error")
    return JSONResponse(
        status_code=error.status_code,
        content=_error_payload(request, code, message, details),
        headers=error.headers,
    )


@app.exception_handler(LoginRequired)
async def admin_login_required(request: Request, error: LoginRequired):
    """
    🔴 The console sends a browser to the sign-in page, not a JSON 401.

    A person who let a session lapse mid-task should land on a form, not on
    `{"error": ...}` — and `next` carries them back to the page they wanted.
    """
    return redirect_to_login(error.next_url)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    details: dict[str, list[str]] = {}
    for item in error.errors():
        location = item.get("loc", ())
        field = str(location[-1]) if location else "non_field_errors"
        details.setdefault(field, []).append(str(item.get("msg", "Invalid value.")))
    return JSONResponse(
        status_code=400,
        content=_error_payload(request, "validation_error", "Invalid request.", details),
    )


app.include_router(auth.router)
app.include_router(organisations.router)
app.include_router(billing.router)
app.include_router(geography.router)
# Writes are a separate module; same prefix, same tag.
app.include_router(organisations_write.router)
app.include_router(farmers.router)
# 🔴 People, roles and contact points — the lawful home for a named human.
# R4 at the door, R9 masking by default, R10 on volume. See the module.
app.include_router(people.router)
# 🔴 Bulk import. R5 is enforced in the commit handler, not on a screen.
app.include_router(imports.router)
app.include_router(dataquality.router)
app.include_router(billing_write.router)
app.include_router(billing_entities.router)
app.include_router(billing_render.router)
app.include_router(billing_extract.router)

# The advanced module (INVOICE.md §12-13). Each router is a bounded area,
# and each depends on `domain.scoping.Scope` rather than reading a tenant id
# off a request body.
app.include_router(copilot.router)
app.include_router(invoice_checks.router)
app.include_router(receivables.router)
app.include_router(deliveries.router)
app.include_router(payment_webhooks.router)
app.include_router(gstin.router)
app.include_router(inbound.router)
app.include_router(compliance.router)

# 🔴 The data-operations console. Mounted last, on its own prefix, and
# excluded from the OpenAPI schema — it is HTML for people, not a contract
# for clients. CLAUDE.md values Django Admin at ~3 months of frontend work
# for a data-curation system; this is that, over the same domain layer.
app.include_router(admin_router)
app.include_router(admin_billing_router)


# 🔴 Both forms, no redirect. A health checker that receives a 307 may
# score the instance as down, and some do not follow redirects at all.
@app.get("/api/v1/healthz/", tags=["ops"], name="healthz")
@app.get("/api/v1/healthz", include_in_schema=False, name="healthz_alias")
async def healthz() -> dict[str, str]:
    """Unauthenticated liveness probe. A load balancer has no token to offer."""
    return {"status": "ok"}


@app.get("/api/v1/readyz/", tags=["ops"], name="readyz")
@app.get("/api/v1/readyz", include_in_schema=False, name="readyz_alias")
async def readyz() -> dict[str, str]:
    """
    Readiness — liveness plus a database round trip.

    Separate from healthz on purpose: a process that is up but cannot reach
    Singapore should be taken out of rotation, not restarted.
    """
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
