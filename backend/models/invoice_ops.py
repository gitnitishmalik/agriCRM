"""
Everything around an invoice that is not the invoice: verification evidence,
pre-issue checks, the delivery outbox, reminders, payment requests, webhook
events, inbound messaging identity and dated tax knowledge.

The grouping is deliberate. These tables share one property — each exists so
that an action taken against a customer has a durable record of *who decided
it, on what evidence, and whether it actually happened*. That is the difference
between a billing system and a mail merge.

Tables in `sql/schema_invoice_advanced.sql`; this module maps them and never
defines them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base
from backend.models.types import (
    BILLING_UNIT,
    CHANNEL,
    CHECK_SEVERITY,
    DELIVERY_STATUS,
    GSTIN_VERIFICATION_STATUS,
    KNOWLEDGE_REVIEW_STATUS,
    PAYMENT_REQUEST_STATUS,
    REMINDER_RUN_STATUS,
    WEBHOOK_PROCESSING_RESULT,
)

# ---------------------------------------------------------------------------
# GSTIN verification
# ---------------------------------------------------------------------------

#: 🔴 Statuses that permit issue without an override. Note what is absent:
#: `verification_unavailable` is not "fine", it is "we do not know", and
#: INVOICE.md §12.4 is explicit that downtime must never read as valid.
GSTIN_OK_STATUSES = frozenset({"valid_active"})

#: Statuses that block issue outright when the organisation's policy is
#: `require_current`, and warn otherwise.
GSTIN_BAD_STATUSES = frozenset({"cancelled", "valid_inactive", "not_found", "invalid_format"})


class GstinVerification(Base):
    """
    One lookup of one GSTIN, cached with its expiry.

    🔴 The provider's reply is kept as a hash plus the fields actually used.
    The full body describes a real business; retaining it whole in a hot table
    spreads identity data across every backup, and the only audit question is
    ever "is this the reply we acted on".
    """

    __tablename__ = "gstin_verification"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    gstin: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(GSTIN_VERIFICATION_STATUS)

    legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    taxpayer_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancellation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    principal_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    raw_response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    raw_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.stored_object.id"), nullable=True
    )

    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    @property
    def is_usable(self) -> bool:
        """Whether this result may populate a buyer without a warning."""
        return self.status in GSTIN_OK_STATUSES


class InvoiceGstinCheck(Base):
    """
    🔴 Issue-time evidence, immutable by trigger.

    Re-verifying a GSTIN next year writes a new `GstinVerification`. It must
    not rewrite what an issued invoice was checked against — otherwise the
    record of an issue decision changes after the decision, which is the one
    thing an audit trail exists to prevent.
    """

    __tablename__ = "invoice_gstin_check"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    verification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.gstin_verification.id"), nullable=True
    )
    checked_gstin: Mapped[str] = mapped_column(Text)
    local_result: Mapped[str] = mapped_column(Text)
    live_status: Mapped[str | None] = mapped_column(GSTIN_VERIFICATION_STATUS, nullable=True)
    blocking_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    mismatches: Mapped[list[Any]] = mapped_column(JSONB, default=list)

    override_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


# ---------------------------------------------------------------------------
# Pre-issue checks
# ---------------------------------------------------------------------------


class InvoiceCheckRun(Base):
    """
    One run of the deterministic pre-issue checks.

    `invoice_sha256` is what makes a pass non-transferable: issue re-runs the
    checks and compares the hash, so a draft edited after a clean run is
    checked again rather than issued on a stale pass.
    """

    __tablename__ = "invoice_check_run"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ran_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    invoice_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    blocking_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    is_issue_evidence: Mapped[bool] = mapped_column(Boolean, default=False)


class InvoiceCheckAck(Base):
    """
    A human accepting a non-blocking warning, with their reason.

    🔴 There is no acknowledgement path for a blocking error. A blocking error
    is fixed or overridden through its own permissioned path; "acknowledged"
    would turn a control into a checkbox.
    """

    __tablename__ = "invoice_check_ack"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    check_code: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(CHECK_SEVERITY)
    reason: Mapped[str] = mapped_column(Text)
    acknowledged_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Delivery outbox
# ---------------------------------------------------------------------------

DELIVERY_TERMINAL = frozenset({"delivered", "failed", "cancelled"})


class InvoiceDelivery(Base):
    """
    One attempt to put one document in front of one recipient.

    🔴 A transactional outbox, not a task queue call. The row is written in the
    same transaction as the decision to send, and a worker claims it
    afterwards — so a crash between "the user confirmed" and "the queue
    accepted" leaves a row that will be sent, rather than a confirmation that
    silently did nothing.

    A resend is a new row. The original keeps its own `pdf_sha256`, because
    "which document did they actually receive" is answerable only if every
    send records the artifact it carried.
    """

    __tablename__ = "invoice_delivery"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    channel: Mapped[str] = mapped_column(CHANNEL)
    recipient: Mapped[str] = mapped_column(Text)
    recipient_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The approved text itself, not a template id. Re-rendering later is how a
    #: delivery record stops describing what was sent.
    body_snapshot: Mapped[str] = mapped_column(Text)
    template_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    pdf_sha256: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pdf_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.stored_object.id"), nullable=True
    )

    preview_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(DELIVERY_STATUS, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 🔴 Unique. A caller replaying "send this" gets the same row back rather
    #: than a second email landing in a customer's inbox.
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    reminder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


class ReminderPolicy(Base):
    """
    When and how a customer may be chased, and whether a machine may do it.

    🔴 `autosend_enabled` is off unless a named person turned it on with a
    ceiling. A scheduled job may always *prepare* a run; this is the only
    thing that lets one send without a person looking at it, and the DDL
    refuses the combination of "enabled" with "nobody enabled it".
    """

    __tablename__ = "reminder_policy"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    name: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(CHANNEL)
    trigger_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    template_body: Mapped[str] = mapped_column(Text)
    template_version: Mapped[str] = mapped_column(Text, default="v1")

    quiet_hour_start: Mapped[int] = mapped_column(SmallInteger, default=20)
    quiet_hour_end: Mapped[int] = mapped_column(SmallInteger, default=9)
    timezone: Mapped[str] = mapped_column(Text, default="Asia/Kolkata")
    min_days_between: Mapped[int] = mapped_column(Integer, default=7)
    max_per_invoice: Mapped[int] = mapped_column(Integer, default=4)

    autosend_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    autosend_max_per_run: Mapped[int] = mapped_column(Integer, default=0)
    autosend_enabled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    autosend_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ReminderRun(Base):
    """
    A batch, frozen at preview and confirmed by hash.

    The hash is the whole mechanism: a confirmation quotes back the preview it
    saw, and a candidate list that has changed since — a payment arrived, an
    opt-out landed — is refused rather than sent.
    """

    __tablename__ = "reminder_run"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.reminder_policy.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(REMINDER_RUN_STATUS, default="preview")
    preview_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    #: Who was left out and why. A preview that shows only the recipients hides
    #: exactly the decisions worth reviewing — the opt-outs and the caps.
    skipped: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    is_autosend: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceReminder(Base):
    __tablename__ = "invoice_reminder"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.reminder_policy.id"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.reminder_run.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(CHANNEL)
    recipient: Mapped[str] = mapped_column(Text)
    message_snapshot: Mapped[str] = mapped_column(Text)
    days_overdue: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice_delivery.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


class PaymentRequest(Base):
    """
    An ask for money. 🔴 Never a payment.

    A UPI URI or a QR code is a convenience for the person paying and carries
    no information about whether they did. Its status says
    `awaiting_manual_confirmation` and stays there until either a human records
    a receipt or a signed gateway webhook matches it.
    """

    __tablename__ = "payment_request"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    provider: Mapped[str] = mapped_column(Text)
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    payload_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_svg: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(PAYMENT_REQUEST_STATUS, default="created")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice_payment.id"), nullable=True
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentWebhookEvent(Base):
    """
    One event from one provider, stored before it is trusted.

    🔴 The row is written with its signature verdict *first*, then processed.
    A webhook handler that verifies, processes and only then records has no
    trace of the events it rejected — which is precisely the set you want when
    something has gone wrong.
    """

    __tablename__ = "payment_webhook_event"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text)
    provider_event_id: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.stored_object.id"), nullable=True
    )
    raw_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    processing_result: Mapped[str] = mapped_column(WEBHOOK_PROCESSING_RESULT, default="pending")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.payment_request.id"), nullable=True
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice.id"), nullable=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice_payment.id"), nullable=True
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    mismatch_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentPromise(Base):
    """A customer said they would pay on a date. The only promise held."""

    __tablename__ = "payment_promise"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    promised_on: Mapped[date] = mapped_column(Date)
    promised_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kept: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


# ---------------------------------------------------------------------------
# Inbound messaging
# ---------------------------------------------------------------------------


class MessagingIdentity(Base):
    """
    🔴 A sender address bound to exactly one billing entity and one user.

    This binding *is* the authorisation for inbound WhatsApp. An unknown
    sender resolves to nothing and therefore reads nothing — no organisation
    search, no invoice lookup, no acknowledgement that a tenant exists. It
    lives in the database rather than a config file because revoking it has to
    be an auditable act.
    """

    __tablename__ = "messaging_identity"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(CHANNEL)
    sender_address: Mapped[str] = mapped_column(Text)
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    authorised_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    authorised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InboundInvoiceMessage(Base):
    """
    What arrived, whether it was trusted, and what it produced.

    🔴 `transcript`, not audio. Retaining a voice note needs a consent basis
    this module does not have, and the transcript is what the proposal was
    built from anyway (INVOICE.md §12.3 A).
    """

    __tablename__ = "inbound_invoice_message"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(CHANNEL)
    provider: Mapped[str] = mapped_column(Text)
    provider_message_id: Mapped[str] = mapped_column(Text)
    sender_address: Mapped[str] = mapped_column(Text)
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.messaging_identity.id"), nullable=True
    )
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(Text, default="text")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.ai_proposal.id"), nullable=True
    )
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Knowledge and contracts
# ---------------------------------------------------------------------------


class TaxCodeKnowledge(Base):
    """
    An HSN/SAC code and its rate, valid between two dates.

    🔴 Retrieval is by the *invoice's* date. A rate that changed in July does
    not retroactively apply to a June document, and a table without effective
    dates cannot express that at all — which is why the suggestion service
    refuses to run against a row with no `effective_from`.

    `review_status` is the other half: an `ai_suggested` row may be shown, but
    only labelled as unreviewed. Only a row a named CA approved is presentable
    as verified.
    """

    __tablename__ = "tax_code_knowledge"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text)
    code_kind: Mapped[str] = mapped_column(Text, default="sac")
    description: Mapped[str] = mapped_column(Text)
    gst_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    jurisdiction: Mapped[str] = mapped_column(Text, default="IN")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.stored_object.id"), nullable=True
    )
    review_status: Mapped[str] = mapped_column(KNOWLEDGE_REVIEW_STATUS, default="ai_suggested")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    @property
    def is_approved(self) -> bool:
        return self.review_status == "approved"


class ContractRate(Base):
    """
    What was agreed, so a rate variance is a comparison rather than a feeling.

    Populated by hand or from a PO until `crm.project` grows its own contract
    records. `tolerance_pct` exists because a contract rate is rarely exact in
    practice — a rounding on a partial acre should not raise a warning, and a
    ₹150 rate billed at ₹230 should.
    """

    __tablename__ = "contract_rate"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.billing_entity.id"))
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    buyer_order_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    hsn_sac: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(BILLING_UNIT)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    rate_is_tax_inclusive: Mapped[bool] = mapped_column(Boolean, default=False)
    tolerance_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal(0))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class InvoiceExtraction(Base):
    """
    `crm.invoice_extraction` — what the model read off an uploaded document,
    kept beside what a human then accepted.

    🔴 Provenance for a machine-filled form. If a model misreads a rate, the
    evidence of what it read is still here afterwards — and `accepted_values`
    holds what was actually billed, so "what the model said" and "what went out"
    can always be compared. That comparison is the evaluation set: the golden
    cases come from real corrections, not from guesses about what is hard.
    """

    __tablename__ = "invoice_extraction"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice.id"), nullable=True
    )
    billing_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.billing_entity.id"), nullable=True
    )
    file_name: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.stored_object.id"), nullable=True
    )
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `text_layer` or `vision`. 🔴 Worth recording: a vision reading always
    #: gets an unconditional review warning, because a rasterised invoice is a
    #: perfect transcript thrown away and then reconstructed — and the
    #: reconstruction is where the errors come from.
    extraction_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    accepted_values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, default=list)

    duplicate_of_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.invoice.id"), nullable=True
    )
    duplicate_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)

    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
