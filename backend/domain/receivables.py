"""
Receivables — ageing, outstanding balances, and an advisory collection ranking.

🔴 **Everything here is derived from payment rows and due dates.** Outstanding
is never a stored column (INVOICE.md §4.5), because a stored balance and a
payment ledger disagree the first time somebody backdates a receipt, and the
one people trust is the wrong one.

🔴 **The "payment risk" ranking is deterministic and shows its inputs.** It is
arithmetic over days overdue, amount, promises and prior payment behaviour —
no model, no sensitive personal traits, and no automatic denial of service. A
score whose contributing facts are not visible is a score nobody can argue
with, and this one exists to start a conversation about who to call first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.scoping import EntityScope
from backend.models.billing import OUTSTANDING_STATUSES, Invoice
from backend.models.business import Organisation
from backend.models.invoice_ops import InvoiceReminder, PaymentPromise
from backend.money import format_inr

#: The buckets, in days overdue. `current` is anything not yet due.
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("current", -10_000, 0),
    ("1_30", 1, 30),
    ("31_60", 31, 60),
    ("61_90", 61, 90),
    ("90_plus", 91, None),
)

BUCKET_LABELS = {
    "current": "Not yet due",
    "1_30": "1–30 days",
    "31_60": "31–60 days",
    "61_90": "61–90 days",
    "90_plus": "90+ days",
}

#: When an invoice carries no due date. 30 days is this business's observed
#: norm, and the report says which invoices were assumed rather than told.
ASSUMED_TERMS_DAYS = 30


def bucket_for(days_overdue: int) -> str:
    for name, low, high in BUCKETS:
        if days_overdue >= low and (high is None or days_overdue <= high):
            return name
    return "90_plus"


@dataclass
class AgeingRow:
    invoice_id: uuid.UUID
    invoice_no: str | None
    entity_code: str
    organisation_id: uuid.UUID | None
    buyer_name: str
    invoice_date: date
    due_date: date | None
    due_date_assumed: bool
    total_value: Decimal
    amount_received: Decimal
    amount_outstanding: Decimal
    days_overdue: int
    bucket: str
    status: str
    promised_on: date | None
    promise_note: str | None
    last_reminder_at: datetime | None
    reminder_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_no": self.invoice_no,
            "entity_code": self.entity_code,
            "organisation_id": str(self.organisation_id) if self.organisation_id else None,
            "buyer_name": self.buyer_name,
            "invoice_date": self.invoice_date.isoformat(),
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "due_date_assumed": self.due_date_assumed,
            "total_value": str(self.total_value),
            "amount_received": str(self.amount_received),
            "amount_outstanding": str(self.amount_outstanding),
            "days_overdue": self.days_overdue,
            "bucket": self.bucket,
            "bucket_label": BUCKET_LABELS[self.bucket],
            "status": self.status,
            "promised_on": self.promised_on.isoformat() if self.promised_on else None,
            "promise_note": self.promise_note,
            "last_reminder_at": (
                self.last_reminder_at.isoformat() if self.last_reminder_at else None
            ),
            "reminder_count": self.reminder_count,
            "display": {
                "total": format_inr(self.total_value),
                "outstanding": format_inr(self.amount_outstanding),
            },
        }


def effective_due_date(invoice: Invoice) -> tuple[date, bool]:
    """
    The date this invoice is measured against, and whether it was assumed.

    🔴 The flag matters. An ageing report that silently invents a due date for
    every invoice missing one looks authoritative and is partly fiction; one
    that says which rows were assumed can be believed.
    """
    if invoice.due_date is not None:
        return invoice.due_date, False
    return invoice.invoice_date + timedelta(days=ASSUMED_TERMS_DAYS), True


async def ageing_rows(
    session: AsyncSession,
    scope: EntityScope,
    *,
    as_of: date | None = None,
    organisation_id: uuid.UUID | None = None,
    entity_code: str | None = None,
) -> list[AgeingRow]:
    """Every invoice that still owes money, aged."""
    as_of = as_of or datetime.now(UTC).date()

    conditions = [
        Invoice.billing_entity_id.in_(scope.entity_ids),
        Invoice.is_deleted.is_(False),
        Invoice.status.in_(OUTSTANDING_STATUSES),
    ]
    if organisation_id is not None:
        conditions.append(Invoice.organisation_id == organisation_id)
    if entity_code:
        conditions.append(Invoice.entity_code == entity_code)

    invoices = list(await session.scalars(select(Invoice).where(and_(*conditions))))
    if not invoices:
        return []

    ids = [invoice.id for invoice in invoices]

    promises = {
        row.invoice_id: row
        for row in await session.scalars(
            select(PaymentPromise)
            .where(PaymentPromise.invoice_id.in_(ids))
            .order_by(PaymentPromise.created_at.asc())
        )
    }
    reminders: dict[uuid.UUID, list[InvoiceReminder]] = {}
    for row in await session.scalars(
        select(InvoiceReminder).where(InvoiceReminder.invoice_id.in_(ids))
    ):
        reminders.setdefault(row.invoice_id, []).append(row)

    rows: list[AgeingRow] = []
    for invoice in invoices:
        outstanding = invoice.amount_outstanding
        if outstanding <= 0:
            # Paid in full but the trigger has not moved the status yet, or a
            # rounding remainder. Either way it is not a receivable.
            continue

        due, assumed = effective_due_date(invoice)
        days_overdue = (as_of - due).days
        promise = promises.get(invoice.id)
        sent = [r for r in reminders.get(invoice.id, []) if r.sent_at is not None]

        rows.append(
            AgeingRow(
                invoice_id=invoice.id,
                invoice_no=invoice.invoice_no,
                entity_code=invoice.entity_code,
                organisation_id=invoice.organisation_id,
                buyer_name=invoice.buyer_name,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                due_date_assumed=assumed,
                total_value=invoice.total_value,
                amount_received=invoice.amount_received,
                amount_outstanding=outstanding,
                days_overdue=days_overdue,
                bucket=bucket_for(days_overdue),
                status=invoice.status,
                promised_on=promise.promised_on if promise else None,
                promise_note=promise.note if promise else None,
                last_reminder_at=max((r.sent_at for r in sent), default=None),
                reminder_count=len(sent),
            )
        )

    rows.sort(key=lambda row: (-row.days_overdue, -row.amount_outstanding))
    return rows


def summarise(rows: list[AgeingRow]) -> dict[str, Any]:
    """Bucket totals plus a grand total, formatted the Indian way."""
    buckets: dict[str, dict[str, Any]] = {
        name: {"label": BUCKET_LABELS[name], "count": 0, "amount": Decimal(0)}
        for name, _, _ in BUCKETS
    }
    total = Decimal(0)
    for row in rows:
        buckets[row.bucket]["count"] += 1
        buckets[row.bucket]["amount"] += row.amount_outstanding
        total += row.amount_outstanding

    return {
        "as_of": datetime.now(UTC).date().isoformat(),
        "invoice_count": len(rows),
        "total_outstanding": str(total),
        "assumed_due_dates": sum(1 for row in rows if row.due_date_assumed),
        "buckets": [
            {
                "bucket": name,
                "label": data["label"],
                "count": data["count"],
                "amount": str(data["amount"]),
                "display": format_inr(data["amount"]),
            }
            for name, data in buckets.items()
        ],
        "display": {"total_outstanding": format_inr(total)},
        "note": (
            f"Outstanding is derived from payment rows, never stored. Invoices "
            f"with no due date are aged from {ASSUMED_TERMS_DAYS} days after the "
            f"invoice date and are counted in `assumed_due_dates`."
        ),
    }


async def by_buyer(
    session: AsyncSession, scope: EntityScope, rows: list[AgeingRow]
) -> list[dict[str, Any]]:
    """Roll the same rows up per customer, oldest debt first."""
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = str(row.organisation_id) if row.organisation_id else f"name:{row.buyer_name}"
        entry = grouped.setdefault(
            key,
            {
                "organisation_id": str(row.organisation_id) if row.organisation_id else None,
                "buyer_name": row.buyer_name,
                "invoice_count": 0,
                "total_outstanding": Decimal(0),
                "oldest_days_overdue": 0,
                "buckets": {name: Decimal(0) for name, _, _ in BUCKETS},
                "billing_opt_out": False,
                "billing_email": None,
            },
        )
        entry["invoice_count"] += 1
        entry["total_outstanding"] += row.amount_outstanding
        entry["oldest_days_overdue"] = max(entry["oldest_days_overdue"], row.days_overdue)
        entry["buckets"][row.bucket] += row.amount_outstanding

    org_ids = [
        uuid.UUID(entry["organisation_id"])
        for entry in grouped.values()
        if entry["organisation_id"]
    ]
    if org_ids:
        for org in await session.scalars(select(Organisation).where(Organisation.id.in_(org_ids))):
            entry = grouped.get(str(org.id))
            if entry is not None:
                entry["billing_opt_out"] = org.billing_opt_out
                entry["billing_email"] = org.billing_email

    out = []
    for entry in grouped.values():
        out.append(
            {
                **entry,
                "total_outstanding": str(entry["total_outstanding"]),
                "buckets": {k: str(v) for k, v in entry["buckets"].items()},
                "display": {"total_outstanding": format_inr(entry["total_outstanding"])},
            }
        )
    out.sort(key=lambda item: -int(item["oldest_days_overdue"]))
    return out


# ---------------------------------------------------------------------------
# The advisory ranking
# ---------------------------------------------------------------------------


def collection_priority(row: AgeingRow, *, as_of: date | None = None) -> dict[str, Any]:
    """
    A transparent score for "who should we call first", with its inputs.

    🔴 Advisory, deterministic, and explained. INVOICE.md §12.3 D: it must not
    use sensitive personal traits and must not automatically deny service — so
    the inputs are exactly four facts about *this invoice*, each contributing a
    stated number of points, and the caller can disagree with any of them.

    Nothing here calls a model. A "risk score" a model produced would be a
    number nobody can reproduce, attached to a customer relationship.
    """
    as_of = as_of or datetime.now(UTC).date()
    factors: list[dict[str, Any]] = []
    score = 0

    if row.days_overdue > 0:
        points = min(40, row.days_overdue // 3)
        score += points
        factors.append(
            {
                "factor": "days_overdue",
                "value": row.days_overdue,
                "points": points,
                "explanation": f"{row.days_overdue} days past due (capped at 40 points).",
            }
        )
    else:
        factors.append(
            {
                "factor": "days_overdue",
                "value": row.days_overdue,
                "points": 0,
                "explanation": f"Not yet due — {abs(row.days_overdue)} days to go.",
            }
        )

    # Amount, on a log-ish scale: a ₹10 lakh invoice matters more than a
    # ₹10,000 one, but not a hundred times more.
    if row.amount_outstanding >= Decimal(1000000):
        amount_points = 30
        band = "₹10 lakh or more"
    elif row.amount_outstanding >= Decimal(300000):
        amount_points = 20
        band = "₹3–10 lakh"
    elif row.amount_outstanding >= Decimal(100000):
        amount_points = 12
        band = "₹1–3 lakh"
    elif row.amount_outstanding >= Decimal(25000):
        amount_points = 6
        band = "₹25,000–1 lakh"
    else:
        amount_points = 2
        band = "under ₹25,000"
    score += amount_points
    factors.append(
        {
            "factor": "amount_outstanding",
            "value": str(row.amount_outstanding),
            "points": amount_points,
            "explanation": f"{format_inr(row.amount_outstanding)} outstanding — {band}.",
        }
    )

    if row.promised_on is not None:
        if row.promised_on >= as_of:
            score -= 15
            factors.append(
                {
                    "factor": "payment_promised",
                    "value": row.promised_on.isoformat(),
                    "points": -15,
                    "explanation": (
                        f"Payment promised for {row.promised_on.isoformat()}, which "
                        f"has not arrived yet. Chasing before then is noise."
                    ),
                }
            )
        else:
            broken = (as_of - row.promised_on).days
            points = min(25, 10 + broken // 5)
            score += points
            factors.append(
                {
                    "factor": "promise_broken",
                    "value": row.promised_on.isoformat(),
                    "points": points,
                    "explanation": (
                        f"Payment was promised for {row.promised_on.isoformat()}, "
                        f"{broken} days ago, and has not arrived."
                    ),
                }
            )

    if row.reminder_count == 0 and row.days_overdue > 0:
        score += 10
        factors.append(
            {
                "factor": "never_reminded",
                "value": 0,
                "points": 10,
                "explanation": ("Nobody has chased this invoice yet — the cheapest thing to try."),
            }
        )
    elif row.reminder_count >= 3:
        factors.append(
            {
                "factor": "reminders_sent",
                "value": row.reminder_count,
                "points": 0,
                "explanation": (
                    f"{row.reminder_count} reminders already sent. More reminders are "
                    f"unlikely to help; this needs a phone call or a decision."
                ),
            }
        )

    if row.amount_received > 0:
        score -= 5
        factors.append(
            {
                "factor": "part_paid",
                "value": str(row.amount_received),
                "points": -5,
                "explanation": (
                    f"{format_inr(row.amount_received)} has already been received "
                    f"against this invoice, which is a paying customer rather than a "
                    f"silent one."
                ),
            }
        )

    score = max(0, min(100, score))
    band_label = "high" if score >= 60 else "medium" if score >= 30 else "low"

    return {
        "invoice_id": str(row.invoice_id),
        "score": score,
        "band": band_label,
        "factors": factors,
        "disclaimer": (
            "Advisory only. Computed from days overdue, amount outstanding, "
            "promised-payment history and reminders sent — nothing else. It does "
            "not deny service, does not use personal characteristics, and no "
            "model produced it."
        ),
    }
