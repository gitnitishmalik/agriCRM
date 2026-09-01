"""
`/api/v1/receivables/` and the payment-request routes.

🔴 Nothing in this file records a payment. `POST /payment-requests/` asks for
money and returns a link; the only endpoint that creates a `crm.invoice_payment`
row is `POST /invoices/{id}/payments/` in `billing_write.py`, which a human
calls with a receipt in front of them, and the webhook path in
`payment_webhooks.py`, which requires a verified signature and an exact match.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.deps import SessionDep, StrictQuery
from backend.domain import payments as payment_service
from backend.domain import receivables as service
from backend.domain.scoping import BILLING_READ, BILLING_WRITE, Scope
from backend.models.billing import Invoice
from backend.models.invoice_ops import PaymentRequest
from backend.schemas.collections import (
    AgeingReport,
    PaymentRequestCreate,
    PaymentRequestOut,
    PriorityOut,
    PromiseCreate,
    PromiseOut,
)

router = APIRouter(prefix="/api/v1", tags=["receivables"])


async def _load(session, scope: Scope, invoice_id: uuid.UUID) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")
    return invoice


@router.get(
    "/receivables/ageing/",
    response_model=AgeingReport,
    name="receivables_ageing",
    dependencies=[StrictQuery],
)
async def ageing(
    session: SessionDep,
    scope: Scope,
    organisation: uuid.UUID | None = Query(default=None),
    entity_code: str | None = Query(default=None),
    as_of: date | None = Query(default=None),
) -> AgeingReport:
    """
    Outstanding balance and ageing, derived from payment rows.

    🔴 Nothing here reads a stored balance. Outstanding is `total_value` minus
    the payments actually recorded (INVOICE.md §4.5) — a stored figure and a
    payment ledger disagree the first time somebody backdates a receipt, and
    the one people trust is the wrong one.
    """
    scope.require(BILLING_READ, "read the receivables report")

    rows = await service.ageing_rows(
        session, scope, as_of=as_of, organisation_id=organisation, entity_code=entity_code
    )
    return AgeingReport(
        summary=service.summarise(rows),
        rows=[row.as_dict() for row in rows],
        by_buyer=await service.by_buyer(session, scope, rows),
    )


@router.get(
    "/receivables/priority/",
    response_model=list[PriorityOut],
    name="receivables_priority",
    dependencies=[StrictQuery],
)
async def priority(
    session: SessionDep,
    scope: Scope,
    limit: int = Query(default=25, ge=1, le=200),
) -> list[PriorityOut]:
    """
    Who to chase first, with the arithmetic that produced the order.

    🔴 Advisory and deterministic. Days overdue, amount outstanding, promised
    payments and reminders already sent — no model, no personal characteristics,
    and it denies nobody service. Every factor and its point value is in the
    response so the ranking can be argued with.
    """
    scope.require(BILLING_READ, "read the receivables report")

    rows = await service.ageing_rows(session, scope)
    scored = [service.collection_priority(row) for row in rows]
    scored.sort(key=lambda item: -item["score"])
    return [PriorityOut(**item) for item in scored[:limit]]


@router.post(
    "/invoices/{invoice_id}/payment-requests/",
    response_model=PaymentRequestOut,
    status_code=status.HTTP_201_CREATED,
    name="payment_request_create",
)
async def create_payment_request(
    invoice_id: uuid.UUID, payload: PaymentRequestCreate, session: SessionDep, scope: Scope
) -> PaymentRequestOut:
    """
    Generate a UPI link and QR, or a gateway payment request.

    🔴 This does not record a payment, and generating or scanning the QR never
    will. A manual UPI request sits at `awaiting_manual_confirmation` until a
    person enters the receipt.

    Idempotent: replaying it returns the same request rather than sending the
    customer a second link for one invoice.
    """
    scope.require(BILLING_WRITE, "request a payment")
    invoice = await _load(session, scope, invoice_id)

    row = await payment_service.create_payment_request(
        session,
        scope,
        invoice,
        provider_name=payload.provider,
        amount=payload.amount,
        note=payload.note,
        supplied_key=payload.idempotency_key,
    )
    return _request_out(row)


@router.get(
    "/invoices/{invoice_id}/payment-requests/",
    response_model=list[PaymentRequestOut],
    name="payment_request_list",
)
async def list_payment_requests(
    invoice_id: uuid.UUID, session: SessionDep, scope: Scope
) -> list[PaymentRequestOut]:
    scope.require(BILLING_READ, "read payment requests")
    invoice = await _load(session, scope, invoice_id)

    rows = await session.scalars(
        select(PaymentRequest)
        .where(PaymentRequest.invoice_id == invoice.id)
        .order_by(PaymentRequest.created_at.desc())
    )
    return [_request_out(row) for row in rows]


def _request_out(row: PaymentRequest) -> PaymentRequestOut:
    return PaymentRequestOut(
        id=row.id,
        invoice_id=row.invoice_id,
        provider=row.provider,
        provider_reference=row.provider_reference,
        amount=row.amount,
        currency=row.currency,
        payload_url=row.payload_url,
        qr_svg=row.qr_svg,
        status=row.status,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        created_at=row.created_at.isoformat(),
    )


@router.post(
    "/invoices/{invoice_id}/promises/",
    response_model=PromiseOut,
    status_code=status.HTTP_201_CREATED,
    name="payment_promise_create",
)
async def record_promise(
    invoice_id: uuid.UUID, payload: PromiseCreate, session: SessionDep, scope: Scope
) -> PromiseOut:
    """
    Record that a customer said they would pay on a date.

    Not a payment either — but a fact the reminder preview and the collection
    ranking both use, so chasing somebody the day after they promised is
    something the system declines to do.
    """
    scope.require(BILLING_WRITE, "record a payment promise")
    invoice = await _load(session, scope, invoice_id)

    row = await payment_service.record_promise(
        session,
        scope,
        invoice,
        promised_on=payload.promised_on,
        amount=payload.amount,
        note=payload.note,
        contact_name=payload.contact_name,
    )
    return PromiseOut(
        id=row.id,
        invoice_id=row.invoice_id,
        promised_on=row.promised_on,
        promised_amount=row.promised_amount,
        note=row.note,
        contact_name=row.contact_name,
        created_at=row.created_at.isoformat(),
    )


@router.get(
    "/payment-reconciliation/",
    name="payment_reconciliation_queue",
    dependencies=[StrictQuery],
)
async def reconciliation(
    session: SessionDep,
    scope: Scope,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """
    Webhook events that need a person: unmatched, replayed or unsigned.

    🔴 This queue existing is the point. An event whose amount does not match,
    whose reference resolves to nothing, or which arrived twice is never
    guessed at — it lands here, oldest first, and somebody decides.
    """
    scope.require(BILLING_READ, "read the reconciliation queue")
    return await payment_service.reconciliation_queue(session, scope, limit=limit)
