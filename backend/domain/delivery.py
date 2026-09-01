"""
The delivery outbox — preview, confirm, queue, claim, send.

🔴 **Transactional outbox, not a queue call.** The row is written in the same
transaction as the decision to send. A crash between "the user confirmed" and
"the broker accepted" then leaves a row a worker will pick up, rather than a
confirmation that silently did nothing — which is the failure mode of calling
a task queue directly from a request handler.

🔴 **Confirmation binds to a frozen preview.** The caller previews, sees the
exact recipient, the exact message and the exact PDF hash, and confirms by
quoting the preview's hash back. If anything changed — the invoice was
cancelled, the customer opted out, the PDF was regenerated — the hash differs
and the send is refused.

🔴 **Consent is re-checked at dispatch, not at preview** (R7). A customer who
opts out between preview and send does not receive the message.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain import storage as object_storage
from backend.domain.hashing import matches, sha256_of
from backend.domain.scoping import EntityScope
from backend.models.billing import Invoice
from backend.models.business import Organisation
from backend.models.invoice_ops import InvoiceDelivery
from backend.money import format_inr
from backend.providers.messaging import get_provider, normalise_phone, valid_email

logger = logging.getLogger("backend.delivery")

#: Backoff between attempts, in seconds. Bounded and short at first — a
#: transient SMTP failure usually clears in seconds, and an hour's wait on the
#: first retry means an invoice sent tomorrow.
BACKOFF_SECONDS = (60, 300, 1800, 7200, 21600)

DEFAULT_SUBJECT = "Invoice {invoice_no} from {entity}"

DEFAULT_BODY = """\
Dear {recipient_name},

Please find attached invoice {invoice_no} dated {invoice_date} for \
{total} from {entity}.

{payment_line}
Regards,
{entity}
"""


class DeliveryError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(code, detail)


@dataclass
class DeliveryPreview:
    """
    Exactly what would be sent, and its hash.

    🔴 The hash covers the recipient, the body, the subject and the PDF digest
    together. Hashing the body alone would let a confirmed message go to a
    different address; hashing the recipient alone would let different text
    reach a confirmed one.
    """

    invoice_id: uuid.UUID
    channel: str
    recipient: str
    recipient_name: str | None
    subject: str | None
    body: str
    pdf_sha256: bytes | None
    pdf_object_id: uuid.UUID | None
    template_version: str
    warnings: list[str]
    blocked_reason: str | None

    @property
    def preview_sha256(self) -> bytes:
        return sha256_of(
            {
                "invoice_id": self.invoice_id,
                "channel": self.channel,
                "recipient": self.recipient,
                "subject": self.subject,
                "body": self.body,
                "pdf_sha256": self.pdf_sha256,
                "template_version": self.template_version,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "channel": self.channel,
            "recipient": self.recipient,
            "recipient_name": self.recipient_name,
            "subject": self.subject,
            "body": self.body,
            "pdf_sha256": self.pdf_sha256.hex() if self.pdf_sha256 else None,
            "template_version": self.template_version,
            "preview_sha256": self.preview_sha256.hex(),
            "warnings": self.warnings,
            "blocked_reason": self.blocked_reason,
            "can_send": self.blocked_reason is None,
        }


async def resolve_recipient(
    session: AsyncSession, invoice: Invoice, *, channel: str, override: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """
    Where this document goes. Returns (address, name, problem).

    🔴 The billing contact lives on `core.organisation`, not on the invoice.
    An invoice's buyer block is a snapshot of what was printed; a delivery
    address is current information, and freezing it into a document is how a
    resend goes to an address the customer abandoned two years ago.
    """
    organisation = None
    if invoice.organisation_id is not None:
        organisation = await session.scalar(
            select(Organisation).where(Organisation.id == invoice.organisation_id)
        )

    if override:
        candidate, name = (
            override.strip(),
            (organisation.billing_contact_name if organisation else None),
        )
    elif organisation is None:
        return (
            None,
            None,
            (
                "This invoice is not linked to a registry customer, so there is no "
                "billing contact to send it to. Link the customer, or supply a "
                "recipient explicitly."
            ),
        )
    elif channel == "email":
        candidate, name = (
            organisation.billing_email or "",
            organisation.billing_contact_name,
        )
        if not candidate:
            return (
                None,
                None,
                (
                    f"{organisation.name} has no billing email on file. Add one to the "
                    f"customer record."
                ),
            )
    else:
        candidate, name = (
            organisation.billing_phone or "",
            organisation.billing_contact_name,
        )
        if not candidate:
            return (
                None,
                None,
                (
                    f"{organisation.name} has no billing phone on file. Add one to the "
                    f"customer record."
                ),
            )

    if channel == "email":
        if not valid_email(candidate):
            return None, name, f"'{candidate}' is not an email address."
        return candidate, name, None

    normalised = normalise_phone(candidate)
    if normalised is None:
        return (
            None,
            name,
            (
                f"'{candidate}' is not a phone number this can dial. Numbers are stored "
                f"E.164 (+91XXXXXXXXXX)."
            ),
        )
    return normalised, name, None


async def build_preview(
    session: AsyncSession,
    scope: EntityScope,
    invoice: Invoice,
    *,
    channel: str,
    recipient_override: str | None = None,
    body_override: str | None = None,
    subject_override: str | None = None,
    attach_pdf: bool = True,
) -> DeliveryPreview:
    """
    Assemble what would be sent, and why it might not be.

    A blocked preview is still returned rather than raised — the screen needs
    to *show* the reason. Sending is refused separately.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    warnings: list[str] = []
    blocked: str | None = None

    # 🔴 Only a real document goes out. A draft has no number, and a customer
    # receiving one has received something that does not exist.
    if invoice.status in ("draft", "discarded"):
        blocked = (
            f"This invoice is a {invoice.status} and has no number. Issue it first — "
            f"a document a customer receives has to be one that exists."
        )
    elif invoice.status == "cancelled":
        blocked = "This invoice is cancelled. Sending it would be sending a void document."

    recipient, recipient_name, problem = await resolve_recipient(
        session, invoice, channel=channel, override=recipient_override
    )
    if problem and blocked is None:
        blocked = problem

    # 🔴 R7 in miniature. Checked here so the preview says so, and re-checked
    # at dispatch so an opt-out between the two still lands.
    if invoice.organisation_id is not None:
        organisation = await session.scalar(
            select(Organisation).where(Organisation.id == invoice.organisation_id)
        )
        if organisation is not None and organisation.billing_opt_out:
            blocked = (
                f"{organisation.name} has opted out of billing messages"
                + (
                    f" ({organisation.billing_opt_out_at.date().isoformat()})"
                    if organisation.billing_opt_out_at
                    else ""
                )
                + ". An opt-out outranks a send."
            )

    pdf_sha256 = invoice.pdf_sha256
    pdf_object_id = invoice.pdf_object_id
    if attach_pdf and pdf_sha256 is None:
        warnings.append(
            "No PDF has been generated for this invoice yet, so the message would "
            "go out without the document attached. Generate it first."
        )

    entity_name = invoice.billing_entity.legal_name if invoice.billing_entity else "Theta"
    payment_line = ""
    if invoice.amount_outstanding > 0 and invoice.amount_outstanding != invoice.total_value:
        payment_line = (
            f"Amount outstanding: {format_inr(invoice.amount_outstanding)} "
            f"(of {format_inr(invoice.total_value)}).\n"
        )

    body = body_override or DEFAULT_BODY.format(
        recipient_name=recipient_name or invoice.buyer_name,
        invoice_no=invoice.invoice_no or "(draft)",
        invoice_date=invoice.invoice_date.strftime("%d %b %Y"),
        total=format_inr(invoice.total_value),
        entity=entity_name,
        payment_line=payment_line,
    )

    subject = None
    if channel == "email":
        subject = subject_override or DEFAULT_SUBJECT.format(
            invoice_no=invoice.invoice_no or "(draft)", entity=entity_name
        )

    return DeliveryPreview(
        invoice_id=invoice.id,
        channel=channel,
        recipient=recipient or "(unresolved)",
        recipient_name=recipient_name,
        subject=subject,
        body=body,
        pdf_sha256=pdf_sha256 if attach_pdf else None,
        pdf_object_id=pdf_object_id if attach_pdf else None,
        template_version="v1",
        warnings=warnings,
        blocked_reason=blocked,
    )


async def queue_delivery(
    session: AsyncSession,
    scope: EntityScope,
    invoice: Invoice,
    *,
    preview: DeliveryPreview,
    confirmed_sha256: str,
    idempotency_key: str | None = None,
    reminder_id: uuid.UUID | None = None,
) -> InvoiceDelivery:
    """
    Confirm a frozen preview and put it in the outbox.

    🔴 Idempotent on the key. A caller replaying "send this" gets the same row
    rather than a second email in a customer's inbox — which is the difference
    between a retry and a complaint.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    if preview.blocked_reason:
        raise DeliveryError(preview.blocked_reason)

    if not matches(preview.preview_sha256, confirmed_sha256):
        raise DeliveryError(
            "The confirmation does not match the preview. Something changed "
            "between previewing and sending — reload the preview and check the "
            "recipient and the message before confirming.",
            status.HTTP_409_CONFLICT,
        )

    key = idempotency_key or f"{invoice.id}:{preview.channel}:{preview.preview_sha256.hex()[:32]}"
    existing = await session.scalar(
        select(InvoiceDelivery).where(InvoiceDelivery.idempotency_key == key)
    )
    if existing is not None:
        return existing

    row = InvoiceDelivery(
        invoice_id=invoice.id,
        billing_entity_id=invoice.billing_entity_id,
        channel=preview.channel,
        recipient=preview.recipient,
        recipient_name=preview.recipient_name,
        subject=preview.subject,
        body_snapshot=preview.body,
        template_version=preview.template_version,
        pdf_sha256=preview.pdf_sha256,
        pdf_object_id=preview.pdf_object_id,
        preview_sha256=preview.preview_sha256,
        confirmed_by=scope.user_id,
        confirmed_at=datetime.now(UTC),
        status="queued",
        attempts=0,
        max_attempts=len(BACKOFF_SECONDS),
        next_attempt_at=datetime.now(UTC),
        idempotency_key=key,
        reminder_id=reminder_id,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def claim_due(
    session: AsyncSession, *, worker: str, limit: int = 10
) -> list[InvoiceDelivery]:
    """
    Take the next due deliveries, locking them against other workers.

    🔴 `SKIP LOCKED`. Two workers polling the same table without it either
    block on each other or, worse, both claim a row and send twice. This is
    the one query in the module that must not be simplified.
    """
    rows = list(
        await session.scalars(
            select(InvoiceDelivery)
            .where(
                InvoiceDelivery.status == "queued",
                InvoiceDelivery.next_attempt_at <= datetime.now(UTC),
            )
            .order_by(InvoiceDelivery.next_attempt_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )

    now = datetime.now(UTC)
    for row in rows:
        row.status = "claimed"
        row.claimed_at = now
        row.claimed_by = worker
    await session.flush()
    return rows


async def dispatch(session: AsyncSession, delivery: InvoiceDelivery) -> InvoiceDelivery:
    """
    Send one claimed delivery.

    🔴 Consent is re-checked here, not only at preview (R7). A customer who
    opted out after confirming but before the worker ran does not receive the
    message, and the row is cancelled with the reason rather than failed.
    """
    invoice = await session.scalar(select(Invoice).where(Invoice.id == delivery.invoice_id))
    if invoice is None:
        return _terminal(delivery, "invoice_gone", "The invoice no longer exists.")

    if invoice.status in ("cancelled", "discarded", "draft"):
        return _terminal(
            delivery,
            "invoice_not_sendable",
            f"The invoice became {invoice.status} before this was sent.",
            cancelled=True,
        )

    if invoice.organisation_id is not None:
        organisation = await session.scalar(
            select(Organisation).where(Organisation.id == invoice.organisation_id)
        )
        if organisation is not None and organisation.billing_opt_out:
            return _terminal(
                delivery,
                "opted_out",
                "The customer opted out of billing messages after this was confirmed.",
                cancelled=True,
            )

    attachment = None
    if delivery.pdf_object_id is not None:
        try:
            content, stored = await object_storage.read(session, delivery.pdf_object_id)
        except object_storage.StorageError as error:
            # 🔴 A hash mismatch here means the file behind the key changed.
            # Sending it anyway would attach a document nobody approved.
            return _terminal(delivery, "artifact_unavailable", str(error))
        filename = f"{(invoice.invoice_no or 'invoice').replace('/', '-')}.pdf"
        attachment = (filename, content, stored.content_type)

    try:
        provider = get_provider(delivery.channel)
        result = await provider.send(
            recipient=delivery.recipient,
            subject=delivery.subject,
            body=delivery.body_snapshot,
            attachment=attachment,
            idempotency_key=delivery.idempotency_key,
        )
    except Exception as error:  # a provider that raises is treated as transient
        logger.warning("Delivery %s raised: %s", delivery.id, error)
        return _retry_or_fail(delivery, "provider_exception", str(error))

    delivery.attempts += 1
    delivery.provider = getattr(provider, "name", delivery.channel)

    if result.ok:
        delivery.status = "sent"
        delivery.provider_message_id = result.provider_message_id
        delivery.sent_at = datetime.now(UTC)
        delivery.error_code = None
        delivery.error_detail = None
        await session.flush()
        return delivery

    if result.retryable:
        return _retry_or_fail(delivery, result.error_code, result.error_detail, counted=True)
    return _terminal(delivery, result.error_code, result.error_detail, counted=True)


def _retry_or_fail(
    delivery: InvoiceDelivery,
    code: str | None,
    detail: str | None,
    *,
    counted: bool = False,
) -> InvoiceDelivery:
    """Schedule another attempt, or give up once the budget is spent."""
    if not counted:
        delivery.attempts += 1

    delivery.error_code = code
    delivery.error_detail = detail

    if delivery.attempts >= delivery.max_attempts:
        delivery.status = "failed"
        delivery.failed_at = datetime.now(UTC)
        return delivery

    wait = BACKOFF_SECONDS[min(delivery.attempts - 1, len(BACKOFF_SECONDS) - 1)]
    delivery.status = "queued"
    delivery.claimed_at = None
    delivery.claimed_by = None
    delivery.next_attempt_at = datetime.now(UTC) + timedelta(seconds=wait)
    return delivery


def _terminal(
    delivery: InvoiceDelivery,
    code: str | None,
    detail: str | None,
    *,
    cancelled: bool = False,
    counted: bool = False,
) -> InvoiceDelivery:
    """
    Stop trying.

    `cancelled` rather than `failed` when the reason is a decision rather than
    an error — an opt-out is not a delivery failure, and counting it as one
    would make the failure rate meaningless.
    """
    if not counted:
        delivery.attempts += 1
    delivery.status = "cancelled" if cancelled else "failed"
    delivery.failed_at = datetime.now(UTC)
    delivery.error_code = code
    delivery.error_detail = detail
    return delivery


async def history(
    session: AsyncSession, scope: EntityScope, invoice: Invoice
) -> list[dict[str, Any]]:
    """
    Every attempt against this invoice, newest first.

    🔴 Each row names its own PDF hash. A resend after a re-render is a
    different artifact, and "which document did they actually receive" is only
    answerable because each attempt recorded what it carried.
    """
    scope.check(invoice.billing_entity_id, what="invoice")

    rows = list(
        await session.scalars(
            select(InvoiceDelivery)
            .where(InvoiceDelivery.invoice_id == invoice.id)
            .order_by(InvoiceDelivery.created_at.desc())
        )
    )

    return [
        {
            "id": str(row.id),
            "channel": row.channel,
            "recipient": row.recipient,
            "recipient_name": row.recipient_name,
            "subject": row.subject,
            "body_snapshot": row.body_snapshot,
            "pdf_sha256": row.pdf_sha256.hex() if row.pdf_sha256 else None,
            "status": row.status,
            "attempts": row.attempts,
            "provider": row.provider,
            "provider_message_id": row.provider_message_id,
            "confirmed_at": row.confirmed_at.isoformat(),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
            "failed_at": row.failed_at.isoformat() if row.failed_at else None,
            "error_code": row.error_code,
            "error_detail": row.error_detail,
            "is_reminder": row.reminder_id is not None,
        }
        for row in rows
    ]
