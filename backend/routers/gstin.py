"""
`/api/v1/gstin/` — the two verification layers, and the buyer comparison.

🔴 The local check and the live lookup are separate fields in every response.
A checksum-valid GSTIN is a well-formed one, not an active one, and the whole
D1/D2 problem in the historical data comes from people conflating the two. The
API cannot stop a UI writing "verified" over a local result, but it can refuse
to hand it a single boolean that invites it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.deps import SessionDep, StrictQuery
from backend.domain import verification as service
from backend.domain.scoping import BILLING_OVERRIDE, BILLING_READ, BILLING_WRITE, Scope
from backend.models.billing import Invoice
from backend.models.invoice_ops import GstinVerification, InvoiceGstinCheck
from backend.schemas.gstin import (
    GstinCheckOut,
    GstinOverrideRequest,
    LocalCheckOut,
    UseVerifiedResult,
    VerificationCreate,
    VerificationOut,
)

router = APIRouter(prefix="/api/v1", tags=["gstin"])


@router.get(
    "/gstin/check/",
    response_model=LocalCheckOut,
    name="gstin_local_check",
    dependencies=[StrictQuery],
)
async def local_check(
    scope: Scope,
    value: str = Query(min_length=1, max_length=32),
    govt_uin: bool = Query(default=False),
) -> LocalCheckOut:
    """
    Layer one: format, embedded PAN, state code and checksum. No network.

    🔴 Fast enough to run as the user types, and it is **not** a verification.
    The response says so in `note`, and there is deliberately no field a UI
    could read as "GST-verified".
    """
    scope.require(BILLING_READ, "check a GSTIN")
    return LocalCheckOut(**service.check_locally(value, allow_govt_uin=govt_uin).as_dict())


@router.post(
    "/gstin/verifications/",
    response_model=VerificationOut,
    status_code=status.HTTP_201_CREATED,
    name="gstin_verification_create",
)
async def create_verification(
    payload: VerificationCreate, session: SessionDep, scope: Scope
) -> VerificationOut:
    """
    Layer two: ask the provider whether this registration is active.

    Cached for a configurable TTL and deduplicated across concurrent callers.
    `force` is what **Verify again** calls — a registration can be cancelled
    without notice, and a customer that matters is worth a fresh lookup.

    🔴 Only the GSTIN is sent to the provider. Never invoice lines, never
    amounts, never anything else from the CRM.
    """
    scope.require(BILLING_WRITE, "verify a GSTIN")

    row = await service.verify(
        session,
        scope,
        billing_entity_id=payload.billing_entity,
        gstin=payload.gstin,
        allow_govt_uin=payload.govt_uin,
        force=payload.force,
    )
    return VerificationOut(**service.serialise(row))


@router.get(
    "/gstin/verifications/{verification_id}/",
    response_model=VerificationOut,
    name="gstin_verification_get",
)
async def get_verification(
    verification_id: uuid.UUID, session: SessionDep, scope: Scope
) -> VerificationOut:
    scope.require(BILLING_READ, "read a GSTIN verification")

    row = await session.scalar(
        select(GstinVerification).where(GstinVerification.id == verification_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such verification.")
    scope.check(row.billing_entity_id, what="verification")

    return VerificationOut(**service.serialise(row))


@router.post(
    "/invoices/{invoice_id}/gstin-check/",
    response_model=GstinCheckOut,
    name="invoice_gstin_check",
)
async def invoice_gstin_check(
    invoice_id: uuid.UUID,
    session: SessionDep,
    scope: Scope,
    force: bool = Query(default=False),
) -> GstinCheckOut:
    """
    Both layers for this invoice's buyer, plus the CRM comparison.

    🔴 The comparison reports differences and writes none. Silently overwriting
    a customer record with a provider's spelling of their name is how a
    registry stops being something a human curated.
    """
    scope.require(BILLING_READ, "check an invoice's GSTIN")

    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")

    return GstinCheckOut(**await service.evidence_for_invoice(session, scope, invoice, force=force))


@router.post(
    "/invoices/{invoice_id}/gstin-check/use-verified/",
    response_model=UseVerifiedResult,
    name="invoice_gstin_use_verified",
)
async def use_verified_details(
    invoice_id: uuid.UUID, session: SessionDep, scope: Scope
) -> UseVerifiedResult:
    """
    Populate a **draft** from the verified registry identity.

    🔴 Draft only, and only for an active registration. An issued invoice's
    buyer block is a snapshot of what was printed; a customer that moved office
    must not silently alter a document their accounts team already holds.
    """
    scope.require(BILLING_WRITE, "apply verified buyer details")

    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")

    verification = await service.verify(
        session,
        scope,
        billing_entity_id=invoice.billing_entity_id,
        gstin=invoice.buyer_gstin or "",
        allow_govt_uin=invoice.buyer_is_govt_uin,
    )
    changes = await service.apply_verified_details(session, scope, invoice, verification)

    return UseVerifiedResult(
        invoice_id=invoice.id,
        verification=service.serialise(verification),
        changes=changes,
    )


@router.post(
    "/invoices/{invoice_id}/gstin-check/override/",
    response_model=GstinCheckOut,
    name="invoice_gstin_override",
)
async def override_gstin_block(
    invoice_id: uuid.UUID,
    payload: GstinOverrideRequest,
    session: SessionDep,
    scope: Scope,
) -> GstinCheckOut:
    """
    🔴 Proceed despite an unavailable or mismatched verification.

    Separately permissioned, and it captures actor, reason and time on an
    immutable row. Allowed only where the result is *unknown* or a mismatch —
    never where the registry positively reports the registration cancelled,
    because billing GST to a cancelled registration is not a judgement call.
    """
    scope.require(BILLING_OVERRIDE, "override a blocking GSTIN result")

    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")

    evidence = await service.evidence_for_invoice(session, scope, invoice)
    live = evidence.get("live") or {}

    if live.get("status") == "cancelled":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The registry reports this registration as cancelled. That is not an "
            "unknown to be overridden — billing GST against it denies the customer "
            "input credit and raises a mismatch on their return. Correct the GSTIN "
            "on the customer record.",
        )

    from datetime import UTC, datetime

    session.add(
        InvoiceGstinCheck(
            invoice_id=invoice.id,
            verification_id=uuid.UUID(live["id"]) if live.get("id") else None,
            checked_gstin=evidence.get("gstin") or "(none)",
            local_result="valid" if evidence["local"]["valid"] else "invalid_format",
            live_status=live.get("status"),
            blocking_reasons=["overridden"],
            mismatches=evidence.get("differences", []),
            override_by=scope.user_id,
            override_reason=payload.reason.strip(),
            override_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            created_by=scope.user_id,
        )
    )
    await session.flush()

    evidence["blocks_issue"] = False
    evidence["overridden"] = True
    return GstinCheckOut(**evidence)
