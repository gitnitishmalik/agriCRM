"""
Delivery and reminder routes.

🔴 **Preview and send are separate calls, and send quotes the preview's hash.**
The UI shows the exact recipient, the exact message and the exact PDF hash, and
the confirmation binds to those bytes. A send whose hash does not match is
refused rather than delivered to whoever the address resolves to now.

🔴 **A reminder batch cannot be sent without a confirmed preview**, and an
unattended run cannot be sent at all unless a named person enabled autosend for
that policy with a per-run ceiling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from backend.deps import SessionDep
from backend.domain import delivery as delivery_service
from backend.domain import reminders as reminder_service
from backend.domain.scoping import (
    BILLING_OVERRIDE,
    BILLING_READ,
    BILLING_SEND,
    Scope,
)
from backend.models.billing import Invoice
from backend.models.invoice_ops import ReminderPolicy, ReminderRun
from backend.schemas.collections import (
    AutosendRequest,
    DeliveryOut,
    DeliveryPreviewOut,
    DeliveryPreviewRequest,
    DeliverySendRequest,
    ReminderConfirm,
    ReminderPolicyCreate,
    ReminderPolicyOut,
    ReminderRunOut,
    ReminderRunResult,
)

router = APIRouter(prefix="/api/v1", tags=["delivery"])


async def _load_invoice(session, scope: Scope, invoice_id: uuid.UUID) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    scope.check(invoice.billing_entity_id, what="invoice")
    return invoice


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/{invoice_id}/deliveries/preview/",
    response_model=DeliveryPreviewOut,
    name="delivery_preview",
)
async def preview_delivery(
    invoice_id: uuid.UUID,
    payload: DeliveryPreviewRequest,
    session: SessionDep,
    scope: Scope,
) -> DeliveryPreviewOut:
    """
    Exactly what would be sent, to exactly whom, carrying exactly which PDF.

    A blocked preview still returns 200 with `blocked_reason` set — the screen
    has to *show* why it cannot send. Sending is refused separately.
    """
    scope.require(BILLING_READ, "preview a delivery")
    invoice = await _load_invoice(session, scope, invoice_id)

    preview = await delivery_service.build_preview(
        session,
        scope,
        invoice,
        channel=payload.channel,
        recipient_override=payload.recipient,
        body_override=payload.body,
        subject_override=payload.subject,
        attach_pdf=payload.attach_pdf,
    )
    return DeliveryPreviewOut(**preview.as_dict())


@router.post(
    "/invoices/{invoice_id}/deliveries/",
    response_model=DeliveryOut,
    status_code=status.HTTP_201_CREATED,
    name="delivery_send",
)
async def send_delivery(
    invoice_id: uuid.UUID,
    payload: DeliverySendRequest,
    session: SessionDep,
    scope: Scope,
) -> DeliveryOut:
    """
    Confirm a frozen preview and put it in the outbox.

    🔴 The row is written in the same transaction as the confirmation, then
    dispatched. A crash between the two leaves work a worker will pick up,
    rather than a confirmation that silently did nothing.
    """
    scope.require(BILLING_SEND, "send a document to a customer")
    invoice = await _load_invoice(session, scope, invoice_id)

    preview = await delivery_service.build_preview(
        session,
        scope,
        invoice,
        channel=payload.channel,
        recipient_override=payload.recipient,
        body_override=payload.body,
        subject_override=payload.subject,
        attach_pdf=payload.attach_pdf,
    )
    row = await delivery_service.queue_delivery(
        session,
        scope,
        invoice,
        preview=preview,
        confirmed_sha256=payload.preview_sha256,
        idempotency_key=payload.idempotency_key,
    )

    # Dispatch inline when it is a fresh queue entry. The outbox row exists
    # either way, so a failure here is a retry rather than a lost send.
    if row.status == "queued":
        row.status = "claimed"
        row.claimed_at = datetime.now(UTC)
        row.claimed_by = "inline"
        await session.flush()
        await delivery_service.dispatch(session, row)

    return DeliveryOut(
        id=row.id,
        invoice_id=row.invoice_id,
        channel=row.channel,
        recipient=row.recipient,
        status=row.status,
        attempts=row.attempts,
        pdf_sha256=row.pdf_sha256.hex() if row.pdf_sha256 else None,
        provider=row.provider,
        provider_message_id=row.provider_message_id,
        error_code=row.error_code,
        error_detail=row.error_detail,
    )


@router.get(
    "/invoices/{invoice_id}/deliveries/",
    name="delivery_history",
)
async def delivery_history(invoice_id: uuid.UUID, session: SessionDep, scope: Scope) -> list[dict]:
    """
    Every attempt, newest first, each naming the PDF hash it carried.

    🔴 A resend after a re-render is a different artifact. "Which document did
    they actually receive" is answerable only because each attempt recorded
    what it carried rather than pointing at whatever the invoice holds now.
    """
    scope.require(BILLING_READ, "read delivery history")
    invoice = await _load_invoice(session, scope, invoice_id)
    return await delivery_service.history(session, scope, invoice)


# ---------------------------------------------------------------------------
# Reminder policies
# ---------------------------------------------------------------------------


def _policy_out(policy: ReminderPolicy) -> ReminderPolicyOut:
    return ReminderPolicyOut(
        id=policy.id,
        billing_entity=policy.billing_entity_id,
        name=policy.name,
        channel=policy.channel,
        trigger_days=list(policy.trigger_days),
        template_version=policy.template_version,
        quiet_hour_start=policy.quiet_hour_start,
        quiet_hour_end=policy.quiet_hour_end,
        timezone=policy.timezone,
        min_days_between=policy.min_days_between,
        max_per_invoice=policy.max_per_invoice,
        autosend_enabled=policy.autosend_enabled,
        autosend_max_per_run=policy.autosend_max_per_run,
        is_active=policy.is_active,
    )


@router.post(
    "/reminder-policies/",
    response_model=ReminderPolicyOut,
    status_code=status.HTTP_201_CREATED,
    name="reminder_policy_create",
)
async def create_policy(
    payload: ReminderPolicyCreate, session: SessionDep, scope: Scope
) -> ReminderPolicyOut:
    """Create a policy. 🔴 Autosend is off; enabling it is a separate act."""
    scope.require(BILLING_SEND, "create a reminder policy")
    scope.check(payload.billing_entity, what="billing entity")

    policy = ReminderPolicy(
        billing_entity_id=payload.billing_entity,
        name=payload.name,
        channel=payload.channel,
        trigger_days=payload.trigger_days,
        template_body=payload.template_body or reminder_service.DEFAULT_TEMPLATE,
        template_version="v1",
        quiet_hour_start=payload.quiet_hour_start,
        quiet_hour_end=payload.quiet_hour_end,
        timezone=payload.timezone,
        min_days_between=payload.min_days_between,
        max_per_invoice=payload.max_per_invoice,
        autosend_enabled=False,
        autosend_max_per_run=0,
        is_active=True,
        created_at=datetime.now(UTC),
        created_by=scope.user_id,
    )
    session.add(policy)
    await session.flush()
    return _policy_out(policy)


@router.get(
    "/reminder-policies/", response_model=list[ReminderPolicyOut], name="reminder_policy_list"
)
async def list_policies(session: SessionDep, scope: Scope) -> list[ReminderPolicyOut]:
    scope.require(BILLING_READ, "read reminder policies")
    rows = await session.scalars(
        select(ReminderPolicy)
        .where(ReminderPolicy.billing_entity_id.in_(scope.entity_ids))
        .order_by(ReminderPolicy.name)
    )
    return [_policy_out(row) for row in rows]


@router.post(
    "/reminder-policies/{policy_id}/autosend/",
    response_model=ReminderPolicyOut,
    name="reminder_policy_autosend",
)
async def set_autosend(
    policy_id: uuid.UUID, payload: AutosendRequest, session: SessionDep, scope: Scope
) -> ReminderPolicyOut:
    """
    🔴 Let a scheduled run send without a person, or stop it.

    Separately permissioned, and it records who, when and why. Enabling
    requires a ceiling above zero — the DDL refuses the combination of
    "enabled" with no limit and no named enabler, so this cannot be half-done.
    """
    scope.require(BILLING_OVERRIDE, "enable unattended reminder sending")

    policy = await session.scalar(select(ReminderPolicy).where(ReminderPolicy.id == policy_id))
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder policy.")
    scope.check(policy.billing_entity_id, what="reminder policy")

    if payload.enabled:
        if payload.max_per_run <= 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Unattended sending needs a per-run ceiling above zero. Without "
                "one, a quiet week followed by a busy one messages everybody at "
                "once and nobody chose that.",
            )
        policy.autosend_enabled = True
        policy.autosend_max_per_run = payload.max_per_run
        policy.autosend_enabled_by = scope.user_id
        policy.autosend_enabled_at = datetime.now(UTC)
    else:
        policy.autosend_enabled = False
        policy.autosend_max_per_run = 0

    await session.flush()
    return _policy_out(policy)


# ---------------------------------------------------------------------------
# Reminder runs
# ---------------------------------------------------------------------------


def _run_out(run: ReminderRun) -> ReminderRunOut:
    return ReminderRunOut(
        id=run.id,
        policy_id=run.policy_id,
        status=run.status,
        preview_sha256=run.preview_sha256.hex(),
        candidates=run.candidates,
        skipped=run.skipped,
        expires_at=run.expires_at.isoformat(),
        created_at=run.created_at.isoformat(),
    )


@router.post(
    "/reminder-runs/preview/",
    response_model=ReminderRunOut,
    status_code=status.HTTP_201_CREATED,
    name="reminder_run_preview",
)
async def preview_run(policy: uuid.UUID, session: SessionDep, scope: Scope) -> ReminderRunOut:
    """
    Build a batch preview. Sends nothing.

    🔴 The response carries `skipped` beside `candidates`. A preview showing
    only who would be contacted hides the decisions worth reviewing — the
    opt-outs, the frequency caps, the promises to pay.
    """
    scope.require(BILLING_READ, "preview a reminder run")

    row = await session.scalar(select(ReminderPolicy).where(ReminderPolicy.id == policy))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder policy.")
    scope.check(row.billing_entity_id, what="reminder policy")

    run = await reminder_service.create_run(session, scope, row)
    return _run_out(run)


@router.post(
    "/reminder-runs/{run_id}/confirm/",
    response_model=ReminderRunResult,
    name="reminder_run_confirm",
)
async def confirm_run(
    run_id: uuid.UUID, payload: ReminderConfirm, session: SessionDep, scope: Scope
) -> ReminderRunResult:
    """
    Confirm a frozen preview and queue every candidate.

    🔴 The preview is rebuilt and re-hashed before anything is queued. A
    payment that arrived, an opt-out that landed or an invoice that was
    cancelled since the screen was rendered changes the hash, and the whole run
    is refused rather than partly sent.
    """
    scope.require(BILLING_SEND, "send reminders")

    run = await session.scalar(select(ReminderRun).where(ReminderRun.id == run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder run.")
    scope.check(run.billing_entity_id, what="reminder run")

    created = await reminder_service.confirm_run(
        session, scope, run, preview_sha256=payload.preview_sha256
    )
    return ReminderRunResult(
        run=_run_out(run),
        queued=len(created),
        reminders=[
            {
                "id": str(item.id),
                "invoice_id": str(item.invoice_id),
                "recipient": item.recipient,
                "days_overdue": item.days_overdue,
                "delivery_id": str(item.delivery_id) if item.delivery_id else None,
            }
            for item in created
        ],
    )
