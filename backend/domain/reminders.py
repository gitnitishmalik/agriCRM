"""
Reminder runs — preview, confirm, send.

🔴 **No reminder is sent without a frozen preview a person confirmed, or an
explicitly enabled policy with a ceiling.** Those are the only two paths, and
the second requires a named person to have turned it on with a maximum per run
(the DDL refuses `autosend_enabled` without both). A scheduled job may always
*prepare* a run; preparing is free and sending is not.

🔴 **The skip list is part of the preview.** A preview showing only who would
be contacted hides exactly the decisions worth reviewing — the opt-outs, the
frequency caps, the quiet hours. Both lists are returned and both are stored.

Four suppressions, in the order they are checked, because the order is the
policy: opted out, then already reminded too recently, then too many times,
then outside contactable hours.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain import delivery as delivery_service
from backend.domain import receivables
from backend.domain.hashing import matches, sha256_of
from backend.domain.scoping import EntityScope
from backend.models.billing import Invoice
from backend.models.business import Organisation
from backend.models.invoice_ops import InvoiceReminder, ReminderPolicy, ReminderRun
from backend.money import format_inr

#: How long a preview stays confirmable. Short, because the candidate list
#: describes money owed right now and a payment arriving invalidates it.
PREVIEW_TTL_MINUTES = 60

DEFAULT_TEMPLATE = """\
Dear {recipient_name},

This is a reminder that invoice {invoice_no} dated {invoice_date} for \
{outstanding} remains outstanding ({days_overdue} days past due).

If payment is already on its way, please ignore this message.

Regards,
{entity}
"""


class ReminderError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(code, detail)


def _within_quiet_hours(policy: ReminderPolicy, when: datetime) -> bool:
    """
    Whether `when` falls in the policy's quiet window, in its own timezone.

    🔴 The window wraps midnight — 20:00 to 09:00 is the default, and a naive
    `start <= hour <= end` comparison reads that as an empty window and cheerfully
    messages people at 3am.
    """
    local = when.astimezone(ZoneInfo(policy.timezone or "Asia/Kolkata"))
    hour = local.hour
    start, end = policy.quiet_hour_start, policy.quiet_hour_end
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def build_preview(
    session: AsyncSession,
    scope: EntityScope,
    policy: ReminderPolicy,
    *,
    as_of: date | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Who would be reminded, and who would not, with the reason for each.

    Returns (candidates, skipped). Both go into the run row, and the hash
    covers both — a suppression that disappears between preview and confirm
    changes the run as much as a new candidate does.
    """
    now = now or datetime.now(UTC)
    as_of = as_of or now.date()

    rows = await receivables.ageing_rows(session, scope, as_of=as_of)
    triggers = sorted(policy.trigger_days or [])

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    organisations: dict[uuid.UUID, Organisation] = {}
    org_ids = [row.organisation_id for row in rows if row.organisation_id]
    if org_ids:
        for org in await session.scalars(select(Organisation).where(Organisation.id.in_(org_ids))):
            organisations[org.id] = org

    for row in rows:
        # Only invoices that have crossed a trigger threshold. The largest
        # threshold at or below the age, so an invoice 40 days overdue is
        # chased under the 30-day rule and not four times over.
        crossed = [day for day in triggers if row.days_overdue >= day]
        if not crossed:
            continue
        trigger = max(crossed)

        organisation = organisations.get(row.organisation_id) if row.organisation_id else None

        if organisation is None:
            skipped.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "buyer_name": row.buyer_name,
                    "reason": "no_customer_link",
                    "detail": (
                        "This invoice is not linked to a registry customer, so there "
                        "is no contact and no consent state to check."
                    ),
                }
            )
            continue

        # 🔴 Opt-out first. It outranks everything, including a fresh promise
        # to pay — the same rule `comm.suppression` applies to campaigns.
        if organisation.billing_opt_out:
            skipped.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "buyer_name": row.buyer_name,
                    "reason": "opted_out",
                    "detail": f"{organisation.name} has opted out of billing messages.",
                }
            )
            continue

        recipient = (
            organisation.billing_email if policy.channel == "email" else organisation.billing_phone
        )
        if not recipient:
            skipped.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "buyer_name": row.buyer_name,
                    "reason": "no_contact",
                    "detail": (
                        f"{organisation.name} has no billing "
                        f"{'email' if policy.channel == 'email' else 'phone'} on file."
                    ),
                }
            )
            continue

        sent = list(
            await session.scalars(
                select(InvoiceReminder)
                .where(
                    InvoiceReminder.invoice_id == row.invoice_id,
                    InvoiceReminder.sent_at.is_not(None),
                )
                .order_by(InvoiceReminder.sent_at.desc())
            )
        )

        if len(sent) >= policy.max_per_invoice:
            skipped.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "buyer_name": row.buyer_name,
                    "reason": "frequency_cap",
                    "detail": (
                        f"{len(sent)} reminders already sent, and the policy allows "
                        f"{policy.max_per_invoice}. More messages are not the answer "
                        f"here — a phone call or a decision is."
                    ),
                }
            )
            continue

        if sent and sent[0].sent_at is not None:
            since = (now - sent[0].sent_at).days
            if since < policy.min_days_between:
                skipped.append(
                    {
                        "invoice_id": str(row.invoice_id),
                        "invoice_no": row.invoice_no,
                        "buyer_name": row.buyer_name,
                        "reason": "too_soon",
                        "detail": (
                            f"Last reminded {since} day(s) ago; the policy requires "
                            f"{policy.min_days_between}."
                        ),
                    }
                )
                continue

        if row.promised_on is not None and row.promised_on >= as_of:
            skipped.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "buyer_name": row.buyer_name,
                    "reason": "payment_promised",
                    "detail": (
                        f"Payment is promised for {row.promised_on.isoformat()}, which "
                        f"has not passed. Chasing before then is noise."
                    ),
                }
            )
            continue

        if _within_quiet_hours(policy, now):
            skipped.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "buyer_name": row.buyer_name,
                    "reason": "quiet_hours",
                    "detail": (
                        f"It is currently within the policy's quiet hours "
                        f"({policy.quiet_hour_start:02d}:00–{policy.quiet_hour_end:02d}:00 "
                        f"{policy.timezone})."
                    ),
                }
            )
            continue

        invoice = await session.scalar(select(Invoice).where(Invoice.id == row.invoice_id))
        entity_name = (
            invoice.billing_entity.legal_name
            if invoice is not None and invoice.billing_entity is not None
            else row.entity_code
        )

        message = (policy.template_body or DEFAULT_TEMPLATE).format(
            recipient_name=organisation.billing_contact_name or organisation.name,
            invoice_no=row.invoice_no or "(draft)",
            invoice_date=row.invoice_date.strftime("%d %b %Y"),
            outstanding=format_inr(row.amount_outstanding),
            total=format_inr(row.total_value),
            days_overdue=row.days_overdue,
            entity=entity_name,
        )

        candidates.append(
            {
                "invoice_id": str(row.invoice_id),
                "invoice_no": row.invoice_no,
                "organisation_id": str(organisation.id),
                "buyer_name": row.buyer_name,
                "recipient": recipient,
                "recipient_name": organisation.billing_contact_name,
                "channel": policy.channel,
                "days_overdue": row.days_overdue,
                "trigger_day": trigger,
                "amount_outstanding": str(row.amount_outstanding),
                "message": message,
                "reminders_already_sent": len(sent),
                "priority": receivables.collection_priority(row, as_of=as_of),
            }
        )

    candidates.sort(key=lambda item: -int(item["priority"]["score"]))
    return candidates, skipped


def preview_hash(*, policy_id: uuid.UUID, candidates: list[dict], skipped: list[dict]) -> bytes:
    """
    🔴 Covers both lists.

    Hashing only the candidates would let a suppression vanish between preview
    and confirm — someone's opt-out being cleared, say — without invalidating a
    confirmation, and the person who confirmed would have approved a list that
    no longer describes what is about to happen.
    """
    return sha256_of(
        {
            "policy": str(policy_id),
            "candidates": [
                {
                    "invoice_id": item["invoice_id"],
                    "recipient": item["recipient"],
                    "message": item["message"],
                }
                for item in candidates
            ],
            "skipped": [
                {"invoice_id": item["invoice_id"], "reason": item["reason"]} for item in skipped
            ],
        }
    )


async def create_run(
    session: AsyncSession,
    scope: EntityScope,
    policy: ReminderPolicy,
    *,
    is_autosend: bool = False,
) -> ReminderRun:
    """Build a preview and store it. Sends nothing."""
    scope.check(policy.billing_entity_id, what="reminder policy")

    candidates, skipped = await build_preview(session, scope, policy)

    run = ReminderRun(
        billing_entity_id=policy.billing_entity_id,
        policy_id=policy.id,
        status="preview",
        preview_sha256=preview_hash(policy_id=policy.id, candidates=candidates, skipped=skipped),
        candidates=candidates,
        skipped=skipped,
        is_autosend=is_autosend,
        created_at=datetime.now(UTC),
        created_by=scope.user_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=PREVIEW_TTL_MINUTES),
    )
    session.add(run)
    await session.flush()
    return run


async def confirm_run(
    session: AsyncSession,
    scope: EntityScope,
    run: ReminderRun,
    *,
    preview_sha256: str,
) -> list[InvoiceReminder]:
    """
    Confirm a frozen preview and queue every candidate through the outbox.

    🔴 The preview is rebuilt and re-hashed here. If a payment arrived, an
    opt-out landed, or an invoice was cancelled since the screen was rendered,
    the hash differs and nothing is sent — the person confirmed a list, and
    this is no longer that list.
    """
    scope.check(run.billing_entity_id, what="reminder run")

    if run.status == "completed":
        return []
    if run.status != "preview":
        raise ReminderError(f"A {run.status} run cannot be confirmed.")
    if run.expires_at <= datetime.now(UTC):
        run.status = "expired"
        await session.flush()
        raise ReminderError(
            "This preview expired. Amounts owed change by the hour — build a "
            "fresh one and review it.",
            status.HTTP_409_CONFLICT,
        )

    if not matches(run.preview_sha256, preview_sha256):
        raise ReminderError(
            "The confirmation does not match this preview.",
            status.HTTP_409_CONFLICT,
        )

    policy = await session.scalar(select(ReminderPolicy).where(ReminderPolicy.id == run.policy_id))
    if policy is None:
        raise ReminderError("The policy behind this run no longer exists.")

    # Rebuild. This is the guard against the world having moved.
    candidates, skipped = await build_preview(session, scope, policy)
    current = preview_hash(policy_id=policy.id, candidates=candidates, skipped=skipped)
    if current != run.preview_sha256:
        raise ReminderError(
            "The candidate list has changed since this preview was built — a "
            "payment, an opt-out or a cancellation. Nothing was sent. Build a "
            "fresh preview and review it.",
            status.HTTP_409_CONFLICT,
        )

    if run.is_autosend:
        # 🔴 The ceiling. A policy enabled for autosend with a limit of 20 does
        # not send 400 because the queue grew while nobody was looking.
        if not policy.autosend_enabled:
            raise ReminderError(
                "Autosend is not enabled for this policy. A scheduled run may "
                "prepare a preview; sending needs a person, or a policy someone "
                "explicitly enabled with a per-run limit."
            )
        if len(candidates) > policy.autosend_max_per_run:
            raise ReminderError(
                f"This run has {len(candidates)} candidates and the policy's "
                f"autosend limit is {policy.autosend_max_per_run}. It has been left "
                f"for a person to review rather than trimmed to fit.",
                status.HTTP_409_CONFLICT,
            )

    run.status = "sending"
    run.confirmed_at = datetime.now(UTC)
    run.confirmed_by = scope.user_id
    await session.flush()

    created: list[InvoiceReminder] = []
    for candidate in candidates:
        invoice = await session.scalar(
            select(Invoice).where(Invoice.id == uuid.UUID(candidate["invoice_id"]))
        )
        if invoice is None:
            continue

        preview = await delivery_service.build_preview(
            session,
            scope,
            invoice,
            channel=policy.channel,
            recipient_override=candidate["recipient"],
            body_override=candidate["message"],
            subject_override=(
                f"Reminder: invoice {invoice.invoice_no} outstanding"
                if policy.channel == "email"
                else None
            ),
            attach_pdf=False,
        )
        if preview.blocked_reason:
            # Re-checked at the moment of queueing, so a state change between
            # the rebuild and here still stops it.
            continue

        reminder = InvoiceReminder(
            invoice_id=invoice.id,
            policy_id=policy.id,
            run_id=run.id,
            channel=policy.channel,
            recipient=candidate["recipient"],
            message_snapshot=candidate["message"],
            days_overdue=int(candidate["days_overdue"]),
            scheduled_for=datetime.now(UTC),
            approved_by=scope.user_id,
            created_at=datetime.now(UTC),
        )
        session.add(reminder)
        await session.flush()

        queued = await delivery_service.queue_delivery(
            session,
            scope,
            invoice,
            preview=preview,
            confirmed_sha256=preview.preview_sha256.hex(),
            idempotency_key=f"reminder:{reminder.id}",
            reminder_id=reminder.id,
        )
        reminder.delivery_id = queued.id
        created.append(reminder)

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    await session.flush()
    return created
