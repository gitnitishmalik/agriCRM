"""Shapes for receivables, payment requests, delivery and reminders."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Receivables
# ---------------------------------------------------------------------------


class AgeingBucket(BaseModel):
    bucket: str
    label: str
    count: int
    amount: str
    display: str


class AgeingSummary(BaseModel):
    as_of: str
    invoice_count: int
    total_outstanding: str
    #: 🔴 How many rows were aged from an assumed due date. A report that
    #: silently invents one for every invoice missing it looks authoritative
    #: and is partly fiction.
    assumed_due_dates: int
    buckets: list[AgeingBucket]
    display: dict[str, str]
    note: str


class AgeingReport(BaseModel):
    summary: AgeingSummary
    rows: list[dict[str, Any]]
    by_buyer: list[dict[str, Any]]


class PriorityOut(BaseModel):
    invoice_id: str
    score: int
    band: str
    factors: list[dict[str, Any]]
    disclaimer: str


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


class PaymentRequestCreate(BaseModel):
    """
    🔴 Creating this asks for money. It never records any.

    A manual UPI request comes back `awaiting_manual_confirmation` and stays
    there until a person enters a receipt or a signed webhook matches it.
    """

    provider: str = "manual_upi"
    amount: Decimal | None = None
    note: str | None = None
    idempotency_key: str | None = None


class PaymentRequestOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    provider: str
    provider_reference: str | None
    amount: Decimal
    currency: str
    payload_url: str | None
    qr_svg: str | None
    status: str
    expires_at: str | None
    created_at: str
    #: Spelled out in the payload so a UI cannot accidentally render this as
    #: a completed payment.
    is_payment: bool = False
    note: str = (
        "A payment request is not a payment. Only a human-entered receipt or a "
        "signed, matched gateway webhook creates one."
    )


class PromiseCreate(BaseModel):
    promised_on: date
    amount: Decimal | None = None
    note: str | None = None
    contact_name: str | None = None


class PromiseOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    promised_on: date
    promised_amount: Decimal | None
    note: str | None
    contact_name: str | None
    created_at: str


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


class DeliveryPreviewRequest(BaseModel):
    channel: str = "email"
    recipient: str | None = None
    subject: str | None = None
    body: str | None = None
    attach_pdf: bool = True


class DeliveryPreviewOut(BaseModel):
    invoice_id: str
    channel: str
    recipient: str
    recipient_name: str | None
    subject: str | None
    body: str
    pdf_sha256: str | None
    template_version: str
    #: 🔴 Quote this back to send. It covers recipient, subject, body and the
    #: PDF hash together.
    preview_sha256: str
    warnings: list[str]
    blocked_reason: str | None
    can_send: bool


class DeliverySendRequest(DeliveryPreviewRequest):
    """Confirm a frozen preview. The hash is required."""

    preview_sha256: str = Field(min_length=64, max_length=64)
    idempotency_key: str | None = None


class DeliveryOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    channel: str
    recipient: str
    status: str
    attempts: int
    pdf_sha256: str | None
    provider: str | None
    provider_message_id: str | None
    error_code: str | None
    error_detail: str | None


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


class ReminderPolicyCreate(BaseModel):
    billing_entity: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    channel: str = "email"
    trigger_days: list[int] = [7, 15, 30]
    template_body: str | None = None
    quiet_hour_start: int = 20
    quiet_hour_end: int = 9
    timezone: str = "Asia/Kolkata"
    min_days_between: int = 7
    max_per_invoice: int = 4


class ReminderPolicyOut(BaseModel):
    id: uuid.UUID
    billing_entity: uuid.UUID
    name: str
    channel: str
    trigger_days: list[int]
    template_version: str
    quiet_hour_start: int
    quiet_hour_end: int
    timezone: str
    min_days_between: int
    max_per_invoice: int
    #: 🔴 Off unless a named person turned it on with a per-run ceiling.
    autosend_enabled: bool
    autosend_max_per_run: int
    is_active: bool


class AutosendRequest(BaseModel):
    """
    Enable or disable unattended sending for a policy.

    🔴 Enabling requires a limit above zero and a reason. Both are recorded,
    because "who decided the machine could message customers unattended, and
    what did they think the ceiling was" is the question afterwards.
    """

    enabled: bool
    max_per_run: int = 0
    reason: str = Field(min_length=3, max_length=500)


class ReminderRunOut(BaseModel):
    id: uuid.UUID
    policy_id: uuid.UUID | None
    status: str
    preview_sha256: str
    candidates: list[dict[str, Any]]
    #: 🔴 Returned alongside the candidates, never hidden. The opt-outs and the
    #: capped invoices are the part of a batch worth reviewing.
    skipped: list[dict[str, Any]]
    expires_at: str
    created_at: str


class ReminderConfirm(BaseModel):
    preview_sha256: str = Field(min_length=64, max_length=64)


class ReminderRunResult(BaseModel):
    run: ReminderRunOut
    queued: int
    reminders: list[dict[str, Any]]
