"""
Payment requests and webhook reconciliation.

🔴 **Only two things create a `crm.invoice_payment` row:** a human entering a
receipt, and a signed gateway webhook whose amount, currency and reference
match an outstanding request. Everything else — a UPI link, a QR code, a
customer saying they have paid — is a request in
`awaiting_manual_confirmation`.

🔴 **A webhook is stored before it is trusted.** The row is written with its
signature verdict first, then processed. A handler that verifies, processes and
only then records has no trace of what it rejected, which is exactly the set
you want when something has gone wrong.

🔴 **Anything ambiguous goes to reconciliation, never to the invoice.** An
event whose amount does not match, whose reference resolves to nothing, or
which arrived twice, is recorded with `processing_result` saying which — and a
person looks at it. Guessing here would silently mark an invoice paid.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain import storage as object_storage
from backend.domain.hashing import sha256_bytes
from backend.domain.scoping import EntityScope
from backend.models.billing import Invoice, InvoicePayment
from backend.models.invoice_ops import PaymentPromise, PaymentRequest, PaymentWebhookEvent
from backend.money import money
from backend.providers.payments import PaymentProviderError, WebhookEvent, get_provider

logger = logging.getLogger("backend.payments")

#: Statuses an invoice must be in to accept a payment request. A draft has no
#: number to reference and a cancelled invoice is not owed.
REQUESTABLE_STATUSES = frozenset({"issued", "part_paid", "on_hold"})


def idempotency_key(
    *, invoice_id: uuid.UUID, provider: str, amount: Decimal, supplied: str | None
) -> str:
    """
    🔴 A caller-supplied key wins; otherwise one is derived from the request.

    Derivation matters more than it looks: a UI that retries a failed POST
    without a key would otherwise create a second payment request, and the
    customer would receive two links for one invoice.
    """
    if supplied and supplied.strip():
        return f"{invoice_id}:{supplied.strip()[:120]}"
    return f"{invoice_id}:{provider}:{amount}"


async def create_payment_request(
    session: AsyncSession,
    scope: EntityScope,
    invoice: Invoice,
    *,
    provider_name: str,
    amount: Decimal | None = None,
    note: str | None = None,
    supplied_key: str | None = None,
) -> PaymentRequest:
    """
    Ask for money. Idempotent, and never a payment.

    The default amount is what is outstanding, not the invoice total — a
    part-paid invoice chased for its full value is how a customer is asked to
    pay twice.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    if invoice.status not in REQUESTABLE_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A payment cannot be requested against a {invoice.status} invoice. "
            + (
                "Issue it first — a draft has no number for the customer to reference."
                if invoice.status == "draft"
                else "It is not owed."
            ),
        )

    requested = money(amount) if amount is not None else invoice.amount_outstanding
    if requested <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nothing is outstanding on this invoice.",
        )
    if requested > invoice.amount_outstanding:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{requested} is more than the {invoice.amount_outstanding} outstanding "
            f"on this invoice.",
        )

    key = idempotency_key(
        invoice_id=invoice.id, provider=provider_name, amount=requested, supplied=supplied_key
    )
    existing = await session.scalar(
        select(PaymentRequest).where(PaymentRequest.idempotency_key == key)
    )
    if existing is not None:
        return existing

    try:
        provider = get_provider(provider_name)
        result = await provider.create_request(
            invoice_no=invoice.invoice_no or str(invoice.id),
            amount=requested,
            currency="INR",
            payee_name=(invoice.billing_entity.legal_name if invoice.billing_entity else "Theta"),
            note=note or f"Invoice {invoice.invoice_no or ''}".strip(),
            idempotency_key=key,
        )
    except PaymentProviderError as error:
        # 🔴 A provider failure is a 502, not a request that quietly succeeded
        # with no link in it.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error

    row = PaymentRequest(
        invoice_id=invoice.id,
        billing_entity_id=invoice.billing_entity_id,
        provider=result.provider,
        provider_reference=result.provider_reference,
        amount=requested,
        currency="INR",
        payload_url=result.payload_url,
        qr_svg=result.qr_svg,
        status=result.status,
        expires_at=result.expires_at,
        idempotency_key=key,
        created_at=datetime.now(UTC),
        created_by=scope.user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def record_promise(
    session: AsyncSession,
    scope: EntityScope,
    invoice: Invoice,
    *,
    promised_on,
    amount: Decimal | None,
    note: str | None,
    contact_name: str | None,
) -> PaymentPromise:
    """
    A customer said they would pay on a date.

    Kept as a row rather than a note on the invoice so the collections ranking
    can use it and so a broken promise is visible as a fact rather than as a
    memory.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    row = PaymentPromise(
        invoice_id=invoice.id,
        promised_on=promised_on,
        promised_amount=money(amount) if amount is not None else None,
        note=(note or "").strip() or None,
        contact_name=(contact_name or "").strip() or None,
        recorded_by=scope.user_id,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


async def ingest_webhook(
    session: AsyncSession,
    *,
    provider_name: str,
    body: bytes,
    headers: dict[str, str],
) -> PaymentWebhookEvent:
    """
    Take one event from a gateway. Store it, verify it, then decide.

    🔴 Ordering is the design. Store → verify → match → act, with the row
    written first, so a rejected event leaves evidence. A handler that returns
    early on a bad signature keeps no record of the attempt, and "we started
    getting events we could not verify" is precisely the thing you want to be
    able to see afterwards.

    Returns the stored row whatever the outcome. The route answers 200 in
    almost every case — a gateway retries on non-2xx, and retrying an event we
    have deliberately quarantined achieves nothing but load.
    """
    from backend.config import settings

    raw_digest = sha256_bytes(body)

    try:
        provider = get_provider(provider_name)
        event = provider.parse_webhook(body, headers)
    except PaymentProviderError as error:
        event = WebhookEvent(
            provider_event_id=f"unknown-provider-{raw_digest.hex()[:16]}",
            event_type=None,
            signature_verified=False,
            provider_timestamp=None,
            amount=None,
            currency=None,
            reference=None,
            raw={},
            failure=str(error),
        )

    # 🔴 Unique on (provider, provider_event_id). A gateway retries on any
    # non-2xx and on its own timeouts; without this a redelivery is a duplicate
    # payment row and the customer's invoice reads overpaid.
    existing = await session.scalar(
        select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.provider == provider_name,
            PaymentWebhookEvent.provider_event_id == event.provider_event_id,
        )
    )
    if existing is not None:
        logger.info(
            "Webhook %s/%s already seen; ignoring the redelivery.",
            provider_name,
            event.provider_event_id,
        )
        return existing

    stored = await object_storage.store(
        session,
        body,
        content_type="application/json",
        purpose="webhook_payload",
        original_name=f"{provider_name}-{event.provider_event_id}.json",
    )

    row = PaymentWebhookEvent(
        provider=provider_name,
        provider_event_id=event.provider_event_id,
        event_type=event.event_type,
        signature_verified=event.signature_verified,
        received_at=datetime.now(UTC),
        provider_timestamp=event.provider_timestamp,
        raw_object_id=stored.object_id,
        raw_sha256=raw_digest,
        processing_result="pending",
        amount=event.amount,
        currency=event.currency,
        reference=event.reference,
    )
    session.add(row)
    await session.flush()

    if not event.signature_verified:
        row.processing_result = "signature_failed"
        row.mismatch_detail = event.failure or "signature could not be verified"
        row.processed_at = datetime.now(UTC)
        await session.flush()
        logger.warning(
            "Webhook %s/%s failed signature verification.",
            provider_name,
            event.provider_event_id,
        )
        return row

    # 🔴 Replay window. A correctly signed event from six months ago is a
    # replay, and a signature alone cannot tell you that.
    if event.provider_timestamp is not None:
        age = abs((datetime.now(UTC) - event.provider_timestamp).total_seconds())
        if age > settings.webhook_replay_window_seconds:
            row.processing_result = "replayed"
            row.mismatch_detail = (
                f"event timestamp is {int(age)}s from now, outside the "
                f"{settings.webhook_replay_window_seconds}s window"
            )
            row.processed_at = datetime.now(UTC)
            await session.flush()
            return row

    await _reconcile(session, row)
    return row


async def _reconcile(session: AsyncSession, row: PaymentWebhookEvent) -> None:
    """
    Match a verified event to a payment request, or quarantine it.

    🔴 Every mismatch is `unmatched`, never a best guess. An event that names
    an amount we did not ask for might be a partial payment, a currency error
    or a different invoice — and a system that picks one of those on the
    customer's behalf will eventually pick wrong on a large number.
    """
    now = datetime.now(UTC)
    row.processed_at = now

    if not row.reference:
        row.processing_result = "unmatched"
        row.mismatch_detail = "the event carries no reference to match against"
        await session.flush()
        return

    request = await session.scalar(
        select(PaymentRequest).where(
            PaymentRequest.provider == row.provider,
            PaymentRequest.provider_reference == row.reference,
        )
    )
    if request is None:
        row.processing_result = "unmatched"
        row.mismatch_detail = f"no payment request with provider reference '{row.reference}'"
        await session.flush()
        return

    row.payment_request_id = request.id
    row.invoice_id = request.invoice_id

    if request.status == "succeeded" and request.payment_id is not None:
        row.processing_result = "duplicate"
        row.payment_id = request.payment_id
        row.mismatch_detail = "this payment request has already been settled"
        await session.flush()
        return

    if (row.currency or "INR") != request.currency:
        row.processing_result = "unmatched"
        row.mismatch_detail = (
            f"currency {row.currency} does not match the request's {request.currency}"
        )
        await session.flush()
        return

    if row.amount is None or money(row.amount) != money(request.amount):
        row.processing_result = "unmatched"
        row.mismatch_detail = (
            f"amount {row.amount} does not match the requested {request.amount}. "
            f"A part payment is recorded by a person, not inferred here."
        )
        await session.flush()
        return

    invoice = await session.scalar(select(Invoice).where(Invoice.id == request.invoice_id))
    if invoice is None:
        row.processing_result = "unmatched"
        row.mismatch_detail = "the invoice behind this request no longer exists"
        await session.flush()
        return

    if invoice.status in ("cancelled", "discarded", "draft"):
        row.processing_result = "unmatched"
        row.mismatch_detail = (
            f"the invoice is {invoice.status}; money received against it is a "
            f"refund question for a person, not a payment row"
        )
        await session.flush()
        return

    # Everything matches. This is the one path in the module that creates a
    # payment, and the invoice's status is moved by the database trigger rather
    # than here (smoke test 20).
    payment = InvoicePayment(
        invoice_id=invoice.id,
        amount=money(row.amount),
        received_on=(row.provider_timestamp or now).date(),
        mode=row.provider,
        reference=row.reference,
        note=f"Gateway event {row.provider_event_id}",
        recorded_by=request.created_by,
        created_at=now,
    )
    session.add(payment)
    await session.flush()

    request.status = "succeeded"
    request.settled_at = now
    request.payment_id = payment.id
    row.payment_id = payment.id
    row.processing_result = "processed"
    await session.flush()


async def reconciliation_queue(
    session: AsyncSession, scope: EntityScope, *, limit: int = 100
) -> list[dict[str, Any]]:
    """
    Events that need a person: unmatched, replayed or signature-failed.

    🔴 Ordered oldest first. A queue that shows the newest first is a queue
    where the oldest problem is never reached.
    """
    rows = list(
        await session.scalars(
            select(PaymentWebhookEvent)
            .where(
                PaymentWebhookEvent.processing_result.in_(
                    ("unmatched", "replayed", "signature_failed", "error", "pending")
                )
            )
            .order_by(PaymentWebhookEvent.received_at.asc())
            .limit(limit)
        )
    )

    return [
        {
            "id": str(row.id),
            "provider": row.provider,
            "provider_event_id": row.provider_event_id,
            "event_type": row.event_type,
            "signature_verified": row.signature_verified,
            "received_at": row.received_at.isoformat(),
            "processing_result": row.processing_result,
            "amount": str(row.amount) if row.amount is not None else None,
            "currency": row.currency,
            "reference": row.reference,
            "invoice_id": str(row.invoice_id) if row.invoice_id else None,
            "mismatch_detail": row.mismatch_detail,
            "raw_sha256": row.raw_sha256.hex(),
        }
        for row in rows
    ]
